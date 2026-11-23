"""
演示模式（Demo Mode）—— 在没有 PostgreSQL 的机器上也能完整看前端产出。

═══════════════════════════════════════════════════════════════════════════════
  这是什么，不是什么
═══════════════════════════════════════════════════════════════════════════════

✅ 它是什么：
   - 一个**进程内内存存储**，按 sql/01_init.sql 里 8 张表的结构组织数据
   - 实现了与 app/services.py **同名同签名**的业务函数，
     因此 app/api.py 的 18 个端点无需改动就能跑通
   - AI 特征提取与相似检索**用的是真实算法**（app/ai/），
     只有"向量索引检索"退化为 numpy 全量点积（等价于 pgvector 的精确检索）
   - 事务、触发器、视图的行为在代码层面**模拟**：终审仍走"校验→改流水→改资产→写审计日志"四步

❌ 它不是什么：
   - **不是 PostgreSQL**。下列数据库特性在这一模式下不生效或被简化：
       · 列级 GRANT / RBAC（数据库权限拦截不会触发）
       · 真实触发器（新增/更新/删除的留痕是手工写的，逻辑等价但不是 DB 触发器）
       · CHECK / UNIQUE / 外键约束（只在代码里做了必要的业务校验）
       · HNSW 近似最近邻（改为精确全量检索，结果更准但大表下会更慢）
       · MVCC 与并发控制（单进程单锁，FOR UPDATE 退化为互斥锁）
   - **数据不持久化**。进程一退出，所有数据消失，下次启动回到种子状态。

═══════════════════════════════════════════════════════════════════════════════
  为什么需要它
═══════════════════════════════════════════════════════════════════════════════

本机安全策略禁止执行 initdb，无法创建 PostgreSQL 实例。
为了让"前端产出"能被真实地看到和点通（而不是看静态截图），
提供这个"降级但诚实"的运行方式：功能可演示，界面上有醒目水印说明它不是真库。

═══════════════════════════════════════════════════════════════════════════════
  如何切换
═══════════════════════════════════════════════════════════════════════════════

    ICH_DEMO_MODE=1   强制演示模式（内存数据，无需数据库）
    ICH_DEMO_MODE=auto 自动：数据库连得上就用真库，连不上自动降级（**默认值**）
    ICH_DEMO_MODE=0   强制真库模式，数据库不可用时直接报 503

真实部署请显式设 ICH_DEMO_MODE=0，避免误以为在跑真库。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import HTTPException, status

from app.ai.classifier import SimilarityHit, suggest_level
from app.ai.features import current_backend_info, extract_from_bytes, file_hash
from app.config import BASE_DIR, settings

log = logging.getLogger("ich.demo")

# 演示模式下的固定"当前时间"基准（让时间轴看起来合理，且每次重启一致）
_T0 = datetime.now(timezone.utc)


def _now() -> datetime:
    """返回带时区的当前时间（模拟 TIMESTAMPTZ）。"""
    return datetime.now(timezone.utc)


# =============================================================================
#  种子数据：与 sql/03_seed.sql 保持一致
# =============================================================================
DEMO_PASSWORD = "Passw0rd!"

# 账号 / 母题 / 示例作品统一从 data/seed_demo.json 读取。
# WHY 外置成 JSON 而不是继续写死在 Python 里：
#   1. 演示数据要能被非程序员读懂、改写、增删（运营同学直接编辑 JSON 即可）
#   2. 同一份文件既能喂给演示模式，也能作为真库 03_seed.sql 的内容源，避免两套数据漂移
#   3. 想演示"空库首屏"时，把文件改名即可（或设 ICH_DEMO_SEED=off），不用改代码
SEED_FILE = BASE_DIR / "data" / "seed_demo.json"

# 兜底：JSON 缺失或被删时仍能登录（这是最后的可用性保障）
_FALLBACK_USERS = [
    (1, "heritor01", "杨秀英", "heritor", "黔东南苗族刺绣传习所"),
    (2, "expert01", "周文渊", "expert", "省非物质文化遗产保护中心"),
    (3, "admin01", "陈立", "admin", "省文旅厅非遗处"),
]
_FALLBACK_MOTIFS = [
    (1, "MO-001", "蝴蝶纹", "贵州黔东南",
     "苗族刺绣核心母题，象征繁衍与族群认同，属公有传统纹样", "motifs/motif_butterfly.jpg"),
    (2, "MO-002", "缠枝莲纹", "江苏苏州",
     "汉族传统连续纹样，明清织物与瓷器常见，公有领域", "motifs/motif_lotus.jpg"),
    (3, "MO-003", "回纹", "河南洛阳",
     "几何折线连续纹，源自青铜器，最古老的公有装饰母题之一", "motifs/motif_huiwen.jpg"),
]

# 兼容旧引用（bootstrap.py / 测试里按元组解包）
SEED_USERS = _FALLBACK_USERS
SEED_MOTIFS = _FALLBACK_MOTIFS


def load_seed() -> dict:
    """
    读取 data/seed_demo.json。任何异常都不应让服务起不来 ——
    演示模式的意义就是"随手能跑"，宁可少几条数据也不能白屏。
    """
    if os.getenv("ICH_DEMO_SEED", "on").strip().lower() in ("0", "off", "false", "no"):
        log.info("演示种子已按 ICH_DEMO_SEED=off 关闭，仅加载兜底账号与母题")
        return {}
    if not SEED_FILE.exists():
        log.warning("演示种子文件不存在：%s（将只用兜底账号与母题）", SEED_FILE)
        return {}
    try:
        data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.error("演示种子文件解析失败 %s：%s（将只用兜底账号与母题）", SEED_FILE, exc)
        return {}
    log.info(
        "演示种子已加载：%d 账号 / %d 母题 / %d 作品",
        len(data.get("users") or []), len(data.get("motifs") or []), len(data.get("assets") or []),
    )
    return data


class DemoStore:
    """
    进程内"数据库"。用普通 dict 模拟表，用 list 模拟行集合。

    并发：所有写操作持 self._lock（模拟单事务串行的 MVP 效果）。
    这是演示级实现 —— 真实系统的并发控制靠 PostgreSQL 的 MVCC + 行锁，
    不应该在一个进程内的 Python 锁上重造一遍。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    # ------------------------------------------------------------------ 初始化
    def reset(self) -> None:
        """
        重建种子数据（用于启动与 /api/demo/reset）。

        数据来源优先级：data/seed_demo.json → 代码内兜底。
        加载顺序刻意如此，因为表之间有外键依赖：
          账号 → 母题 → 作品（引用 owner_id / motif_id）→ 审核流水 / 授权 / 审计日志
        任何一步失败都只会少几条演示数据，不会让进程起不来。
        """
        with self._lock:
            seed = load_seed()
            self._seq = {
                "asset": 0, "fv": 0, "audit": 0, "license": 0,
                "genealogy": 0, "log": 0,
            }
            self._vec_cache: dict[str, np.ndarray] = {}
            self.assets: dict[int, dict] = {}
            self.feature_vectors: dict[int, dict] = {}
            self.audit_records: dict[int, dict] = {}
            self.license_records: dict[int, dict] = {}
            self.genealogy: dict[int, dict] = {}
            self.audit_log: dict[int, dict] = {}

            self._load_users(seed)
            self._load_motifs(seed)
            self._load_assets(seed)

            # 序号必须推到已有最大 id 之后，否则后续新建会撞主键
            self._seq["asset"] = max(self.assets, default=0)
            self._seq["fv"] = max((v["fv_id"] for v in self.feature_vectors.values()), default=0)
            self._seq["audit"] = max(self.audit_records, default=0)
            self._seq["license"] = max(self.license_records, default=0)
            self.seed_stats = {
                "users": len(self.users), "motifs": len(self.motifs),
                "assets": len(self.assets), "audits": len(self.audit_records),
                "licenses": len(self.license_records), "logs": len(self.audit_log),
                "source": str(SEED_FILE.name) if seed else "fallback",
            }

    # ------------------------------------------------------- 种子：账号与母题
    def _load_users(self, seed: dict) -> None:
        rows = seed.get("users") or [
            {"user_id": uid, "username": un, "display_name": dn, "role": role,
             "org_name": org, "password": DEMO_PASSWORD}
            for uid, un, dn, role, org in _FALLBACK_USERS
        ]
        self.users: dict[int, dict] = {}
        for r in rows:
            pw = r.get("password") or DEMO_PASSWORD
            self.users[r["user_id"]] = {
                "user_id": r["user_id"], "username": r["username"],
                "display_name": r["display_name"], "role": r["role"],
                "org_name": r.get("org_name"), "is_active": True,
                # 演示用：真实系统是 pgcrypto 的 bcrypt（crypt + gen_salt('bf')）
                "password_hash": hashlib.sha256(
                    f"{pw}|{r['username']}".encode()
                ).hexdigest(),
                "created_at": _T0,
            }

    def _load_motifs(self, seed: dict) -> None:
        rows = seed.get("motifs") or [
            {"motif_id": mid, "motif_code": code, "name": name, "region": region,
             "description": descr, "reference_img": img}
            for mid, code, name, region, descr, img in _FALLBACK_MOTIFS
        ]
        self.motifs: dict[int, dict] = {}
        for r in rows:
            self.motifs[r["motif_id"]] = {
                "motif_id": r["motif_id"], "motif_code": r["motif_code"],
                "name": r["name"], "region": r.get("region"),
                "description": r.get("description"),
                "reference_img": r.get("reference_img"),
                # 母题库的结构化描述（展示页要用，公开可查的元信息，不涉密）
                "ethnic": r.get("ethnic"), "category": r.get("category"),
                "era": r.get("era"), "keywords": r.get("keywords") or [],
                "base_vector": None,          # 首次检索前懒加载真实特征
                "vector_model": None, "created_at": _T0,
            }

    # ------------------------------------------------------- 种子：作品与流水
    def _load_assets(self, seed: dict) -> None:
        for spec in seed.get("assets") or []:
            row, created = self._seed_one_asset(spec)
            if row is None:
                continue
            self._seed_licenses(spec)          # 需要 asset_id 与 owner_id 已存在
            self._seed_approve_log(spec, row, created)

    def _seed_one_asset(self, spec: dict) -> tuple[dict | None, datetime]:
        """把一条种子作品展开成 asset 行 + 审核流水。返回 (asset 行, 创建时间)。"""
        from app.config import UPLOAD_DIR

        aid = spec["asset_id"]
        object_key = spec["file_path"]
        path = (UPLOAD_DIR / object_key).resolve()
        if not path.exists():
            log.warning("演示种子：作品 #%s 的图片缺失 %s，跳过该作品", aid, path)
            return None, _T0
        data = path.read_bytes()

        created = _T0 - timedelta(days=float(spec.get("created_days_ago", 0)))
        decided = (
            _T0 - timedelta(days=float(spec["decided_days_ago"]))
            if spec.get("decided_days_ago") is not None else None
        )
        model_ver = spec.get("ai_model_version")
        if not model_ver and spec.get("ai_suggest_level"):
            # 与 analyze_asset 写入的格式一致；真实值在首次分析时被覆盖
            info = current_backend_info()
            model_ver = f"{info.get('backend', 'builtin')}@1.0.0"

        row = {
            "asset_id": aid, "title": spec["title"], "owner_id": spec["owner_id"],
            "motif_id": spec.get("motif_id"),
            "file_path": object_key, "file_hash": file_hash(data),
            "file_size": len(data), "mime_type": "image/jpeg",
            "right_level": spec.get("right_level") or "public",
            "audit_status": spec.get("audit_status") or "draft",
            "ai_suggest_level": spec.get("ai_suggest_level"),
            "ai_confidence": spec.get("ai_confidence"),
            "ai_model_version": model_ver,
            "version_no": int(spec.get("version_no") or 0),
            "description": spec.get("description"),
            "created_at": created,
            "updated_at": decided or created,
        }
        self.assets[aid] = row
        # 模拟 AFTER INSERT 触发器：登记即留痕
        self.write_audit_log(aid, "INSERT", f"heritor:{spec['owner_id']}", None, dict(row), None)

        # ---- 审核流水 ----
        for a in spec.get("audits") or []:
            audit_id = self.next_id("audit")
            # similarity_json 的形状与 analyze_asset 写的一致：
            #   [{id, code, name, similarity, distance}, ...] —— top-K 母题快照，按相似度降序
            # ★ 种子里的 similarity 是「余弦相似度」，不是「置信度」。
            #   两者量纲完全不同（置信度是 squash 后的判定把握），混用会让母题关联页
            #   显示错误的数值 —— 台账的 top1_similarity 正是取这个字段。
            sim_json = self._snapshot_for(a.get("top_motif_id"), a.get("similarity"))
            a_created = _T0 - timedelta(days=float(a.get("days_ago", 0)))
            a_decided = (
                _T0 - timedelta(days=float(a["decided_days_ago"]))
                if a.get("decided_days_ago") is not None else None
            )
            self.audit_records[audit_id] = {
                "audit_id": audit_id, "asset_id": aid,
                "expert_id": a.get("expert_id"), "state": a["state"],
                "ai_suggest_level": a.get("ai_suggest_level"),
                "ai_confidence": a.get("ai_confidence"),
                "expert_level": a.get("expert_level"),
                "similarity_json": sim_json,
                "opinion": a.get("opinion"),
                "is_override": bool(a.get("is_override")),
                "created_at": a_created,
                "decided_at": a_decided or (a_created if a["state"] != "pending" else None),
            }

        return row, created

    def _snapshot_for(self, top_motif_id: int | None, top_sim: float | None) -> list[dict]:
        """
        构造一条 top-K 母题相似度快照。

        种子数据里的 similarity 是「该作品与各母题的**真实**相似度」（由
        scripts/check_seed.py 从算法结果回写），这里按它补齐母题元信息。
        若某个母题没给数值，则该条不出现在快照里（宁可少一条，不要编一个数）。
        """
        if not top_motif_id:
            return []
        m = self.motifs.get(top_motif_id)
        if not m:
            return []
        sim = float(top_sim or 0.0)
        return [{
            "id": top_motif_id, "code": m["motif_code"], "name": m["name"],
            "similarity": sim, "distance": round(1.0 - sim, 4),
        }]

    def _seed_licenses(self, spec: dict) -> None:
        """
        写种子授权记录。

        ⚠️ 注意：这个方法**必须**在 _seed_one_asset 的审核流水之后单独调用。
        历史上这段代码曾被误缩进到 _snapshot_for 的 return 之后，变成永远不会执行
        的死代码 —— 表面看不出来（语法合法、无报错），但台账的「授权费合计」恒为 0，
        界面上少了最能说明商业价值的一块。这是 e2e 断言「license_record > 0」抓出来的。
        """
        aid = spec["asset_id"]
        for lic in spec.get("licenses") or []:
            lid = self.next_id("license")
            start = (_T0 - timedelta(days=float(lic.get("start_days_ago", 0)))).date()
            end_raw = lic.get("end_days_ago")
            end = (_T0 - timedelta(days=float(end_raw))).date() if end_raw is not None else None
            self.license_records[lid] = {
                "license_id": lid, "asset_id": aid,
                "licensor_id": spec["owner_id"],
                "license_type": lic.get("license_type") or "non_exclusive",
                "licensee_name": lic.get("licensee_name"),
                "licensee_user_id": lic.get("licensee_user_id"),
                "scope": lic.get("scope"), "territory": lic.get("territory"),
                "start_date": start, "end_date": end,
                "fee_cny": lic.get("fee_cny"),
                "status": lic.get("status") or "active",
                # 合同哈希：真实系统由合同 PDF 的 SHA-256 生成，这里用结构化摘要稳定派生
                "contract_hash": hashlib.sha256(
                    f"{aid}|{lic.get('licensee_name')}|{lic.get('fee_cny')}".encode()
                ).hexdigest(),
                "prev_license_id": lic.get("prev_license_id"),
                "created_at": _T0 - timedelta(days=float(lic.get("start_days_ago", 0))),
            }

    def _seed_approve_log(self, spec: dict, row: dict, created: datetime) -> None:
        """
        补一条「权属已定」的审计日志。

        种子作品跳过了真实的 approve_asset 调用，但界面上要展示「触发器留痕」，
        否则溯源链路校验会报「已终审却找不到审计节点」——而这条日志本身就是
        最能证明「审计是数据库自动写的」的证据，不该因为走种子就缺失。
        """
        if row["audit_status"] != "approved":
            return
        self.write_audit_log(
            row["asset_id"], "UPDATE", f"expert:{spec.get('expert_id') or 2}",
            {"right_level": "public", "audit_status": "pending",
             "motif_id": None, "owner_id": row["owner_id"],
             "version_no": row["version_no"] - 1, "updated_at": created},
            {"right_level": row["right_level"], "audit_status": "approved",
             "motif_id": row["motif_id"], "owner_id": row["owner_id"],
             "version_no": row["version_no"], "updated_at": row["updated_at"]},
            ["right_level", "audit_status"],
        )

    def seed_vector_count(self) -> int:
        """返回可为种子作品补齐向量的条数（母题向量另行 warm_motif_vectors）。"""
        return sum(1 for a in self.assets.values() if a["asset_id"] not in self.feature_vectors)

    def next_id(self, table: str) -> int:
        with self._lock:
            self._seq[table] += 1
            return self._seq[table]

    # ------------------------------------------------------- 模拟触发器 & 权限
    def write_audit_log(
        self, record_id: int, operation: str, actor: str,
        old_snapshot: dict | None, new_snapshot: dict | None,
        changed_fields: list[str] | None,
    ) -> int:
        """
        模拟 sql/02 里的 fn_asset_audit_trigger()：
        只记录 right_level / audit_status / motif_id / owner_id 的真实变更。
        """
        lid = self.next_id("log")
        self.audit_log[lid] = {
            "log_id": lid, "table_name": "asset", "record_id": record_id,
            "operation": operation, "actor": actor,
            "old_snapshot": old_snapshot, "new_snapshot": new_snapshot,
            "changed_fields": changed_fields, "logged_at": _now(),
        }
        return lid

    def audit_asset_change(self, old: dict, new: dict, actor: str) -> int | None:
        """逐列比对，只对权属/状态类变更留痕（与真实触发器逻辑一致）。"""
        watched = ("right_level", "audit_status", "motif_id", "owner_id")
        changed = [k for k in watched if old.get(k) != new.get(k)]
        if not changed:
            return None
        pick = lambda r: {**{k: r.get(k) for k in watched},
                          "version_no": r.get("version_no"),
                          "updated_at": r.get("updated_at")}
        return self.write_audit_log(
            new["asset_id"], "UPDATE", actor, pick(old), pick(new), changed
        )

    # ------------------------------------------------------------------ 母题向量
    def motif_vector(self, motif_id: int) -> np.ndarray | None:
        """
        懒加载母题向量：首次访问时用真实 AI 后端从参考图提取。
        对应真实系统里 `python -m app.cli backfill-motif-vectors` 的回填动作。
        """
        cached = self._vec_cache.get(f"m{motif_id}")
        if cached is not None:
            return cached

        m = self.motifs.get(motif_id)
        if not m or not m["reference_img"]:
            return None

        from app.config import BASE_DIR
        from pathlib import Path
        p = (BASE_DIR / "data" / m["reference_img"]).resolve()
        if not p.exists():
            log.warning("演示模式：母题参考图不存在 %s", p)
            return None

        feat = extract_from_bytes(p.read_bytes())
        vec = np.asarray(feat.vector, dtype=np.float64)
        self.motifs[motif_id]["base_vector"] = feat.vector
        self.motifs[motif_id]["vector_model"] = f"{feat.model_name}@{feat.model_version}"
        self._vec_cache[f"m{motif_id}"] = vec
        return vec

    def warm_motif_vectors(self) -> int:
        """预加载全部母题向量，避免首个用户等几秒。"""
        n = 0
        for mid in self.motifs:
            if self.motif_vector(mid) is not None:
                n += 1
        return n

    def warm_asset_vectors(self) -> int:
        """
        为种子作品补算特征向量（对应真库里 03_seed.sql 之后跑一次 backfill）。

        WHY 必须在启动时算：种子作品没有走过 upload → analyze 流程，
        feature_vector 表是空的。若不算，"相似资产"比对与上传新作品的查重
        都会漏掉这些老作品 —— 而这恰恰是最该被查出来的重复。

        用真实 AI 后端（与线上完全同一套代码），只在进程内缓存，不落盘。
        """
        from app.config import UPLOAD_DIR

        n = 0
        for aid, a in self.assets.items():
            if aid in self.feature_vectors:
                continue
            p = (UPLOAD_DIR / a["file_path"]).resolve()
            if not p.exists():
                continue
            try:
                feat = extract_from_bytes(p.read_bytes())
            except Exception as exc:  # noqa: BLE001
                log.warning("演示种子：作品 #%s 特征提取失败：%s", aid, exc)
                continue
            fv_id = self.next_id("fv")
            self.feature_vectors[aid] = {
                "fv_id": fv_id, "asset_id": aid,
                "model_name": feat.model_name, "model_version": feat.model_version,
                "embed_dim": feat.embed_dim, "embedding": feat.vector,
                "created_at": a["created_at"],
            }
            # 种子数据里没写模型版本时，用真实算出的一版补上
            if not a.get("ai_model_version") and a.get("ai_suggest_level"):
                a["ai_model_version"] = f"{feat.model_name}@{feat.model_version}"
            n += 1
        return n

    def warm_all(self) -> dict[str, int]:
        """启动时一次性预热母题与作品向量。"""
        return {
            "motif_vectors": self.warm_motif_vectors(),
            "asset_vectors": self.warm_asset_vectors(),
        }


STORE = DemoStore()


# =============================================================================
#  对外：演示模式开关
# =============================================================================
def is_enabled() -> bool:
    """当前是否处于演示模式。auto 由 main.py 在启动时根据数据库连通性锁定。"""
    return _FORCED_ON


_FORCED_ON = False


def activate() -> None:
    global _FORCED_ON
    _FORCED_ON = True


def deactivate() -> None:
    global _FORCED_ON
    _FORCED_ON = False


def mode_setting() -> str:
    """读取配置：1 / 0 / auto。"""
    import os
    return os.getenv("ICH_DEMO_MODE", "auto").strip().lower()


# =============================================================================
#  AI 分析（真实算法，仅检索改为 numpy 精确点积）
# =============================================================================
def _search_motifs(vec: np.ndarray, top_k: int) -> list[SimilarityHit]:
    """
    与 app/ai/similarity.search_motifs 语义一致：
    余弦距离 = 1 - 相似度；向量已 L2 归一化，故相似度 = 内积。
    差别：这里没有 HNSW 索引，是**精确全量扫描**（对 3~1000 条母题无性能问题）。
    """
    hits: list[SimilarityHit] = []
    for mid, m in STORE.motifs.items():
        mv = STORE.motif_vector(mid)
        if mv is None:
            continue
        s = float(np.clip(vec @ mv, 0.0, 1.0))
        hits.append(SimilarityHit(
            kind="public_motif", id=mid, code=m["motif_code"], name=m["name"],
            similarity=s, distance=1.0 - s, extra={"region": m.get("region")},
        ))
    hits.sort(key=lambda h: -h.similarity)
    return hits[:top_k]


def _search_assets(vec: np.ndarray, self_id: int, top_k: int) -> list[SimilarityHit]:
    hits: list[SimilarityHit] = []
    for aid, fv in STORE.feature_vectors.items():
        if aid == self_id:
            continue
        s = float(np.clip(vec @ np.asarray(fv["embedding"], dtype=np.float64), 0.0, 1.0))
        a = STORE.assets.get(aid)
        if not a:
            continue
        hits.append(SimilarityHit(
            kind="private_asset", id=aid, code=f"AS-{aid:04d}",
            name=a["title"], similarity=s, distance=1.0 - s,
            extra={"owner_id": a["owner_id"], "right_level": a["right_level"]},
        ))
    hits.sort(key=lambda h: -h.similarity)
    return hits[:top_k]


def _hit_dict(h: SimilarityHit) -> dict:
    return {
        "kind": h.kind, "id": h.id, "code": h.code, "name": h.name,
        "similarity": h.similarity, "distance": h.distance, "extra": h.extra,
    }


# =============================================================================
#  业务函数：签名与返回结构对齐 app/services.py
# =============================================================================
def _user(user_id: int) -> dict:
    u = STORE.users.get(user_id)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"用户 {user_id} 不存在")
    return u


def _asset_out(row: dict) -> dict:
    owner = STORE.users.get(row["owner_id"], {})
    return {**row, "owner_name": owner.get("display_name")}


def login(username: str, password: str) -> dict:
    """与 api.login 的库内校验等价：口令错与用户不存在返回同一句话，防枚举。"""
    for u in STORE.users.values():
        if u["username"] != username:
            continue
        expect = hashlib.sha256(f"{password}|{username}".encode()).hexdigest()
        # 用 compare_digest 防时序攻击（真实系统由 pgcrypto 在库内完成）
        if not hmac.compare_digest(expect, u["password_hash"]):
            break
        if not u["is_active"]:
            break
        return u
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或口令错误")


def create_asset(
    *, owner_id: int, title: str, description: str | None,
    file_bytes: bytes, mime_type: str, object_key: str, app_role: str,
) -> dict:
    """上传落库。file_hash 命中已有资产时幂等复用（与真实实现一致）。"""
    h = file_hash(file_bytes)
    with STORE._lock:
        for a in STORE.assets.values():
            if a["file_hash"] == h:
                return {"asset_id": a["asset_id"], "duplicate_of": a["asset_id"]}

        aid = STORE.next_id("asset")
        now = _now()
        row = {
            "asset_id": aid, "title": title, "owner_id": owner_id, "motif_id": None,
            "file_path": object_key, "file_hash": h, "file_size": len(file_bytes),
            "mime_type": mime_type,
            "right_level": "public", "audit_status": "draft",
            "ai_suggest_level": None, "ai_confidence": None, "ai_model_version": None,
            "version_no": 0, "description": description,
            "created_at": now, "updated_at": now,
        }
        STORE.assets[aid] = row
        # 模拟 AFTER INSERT 触发器
        STORE.write_audit_log(aid, "INSERT", app_role, None, dict(row), None)
        return {"asset_id": aid, "duplicate_of": None}


def get_asset(asset_id: int, app_role: str, viewer_id: int | None = None,
              is_privileged: bool = False) -> dict:
    a = STORE.assets.get(asset_id)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"资产 {asset_id} 不存在")
    # 传承人只能看自己的（真实系统靠视图过滤 + 列级权限，这里等价实现）
    if not is_privileged and viewer_id is not None and a["owner_id"] != viewer_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权查看他人作品")
    return _asset_out(a)


def list_my_assets(owner_id: int, app_role: str) -> list[dict]:
    """★ 返回字段必须与 services.list_my_assets 逐列一致 ★

    真实实现是 `SELECT asset_id, title, right_level, audit_status,
    ai_suggest_level, ai_confidence, version_no, created_at, updated_at
    FROM asset`。演示模式少返回一列，前端就会渲染出 `vundefined` ——
    这类"两种实现字段漂移"的 bug 在切库时才暴露，所以此处显式对齐，
    并由 tests/test_invariants.py 的字段一致性用例守住。
    """
    rows = [a for a in STORE.assets.values() if a["owner_id"] == owner_id]
    rows.sort(key=lambda a: a["created_at"], reverse=True)
    return [
        {
            "asset_id": a["asset_id"],
            "title": a["title"],
            "right_level": a["right_level"],
            "audit_status": a["audit_status"],
            "ai_suggest_level": a["ai_suggest_level"],
            "ai_confidence": a["ai_confidence"],
            "version_no": a.get("version_no", 0),
            "created_at": a["created_at"],
            "updated_at": a.get("updated_at", a["created_at"]),
            # 真实库由 LEFT JOIN public_motif，演示模式在这里查字典
            "motif_code": (STORE.motifs.get(a.get("motif_id") or 0) or {}).get("motif_code"),
            "motif_name": (STORE.motifs.get(a.get("motif_id") or 0) or {}).get("name"),
            # 真实库由子查询聚合，演示模式在这里算，语义同为"生效中的授权数"
            "license_count": sum(
                1 for l in STORE.license_records.values()
                if l["asset_id"] == a["asset_id"] and l.get("status", "active") == "active"
            ),
        }
        for a in rows
    ]


def analyze_asset(
    *, asset_id: int, app_role: str, world_app_role: str,
    viewer_id: int | None, is_privileged: bool, include_plan: bool = True,
) -> dict:
    """
    AI 分析：提特征 → 相似检索 → 分级建议 → 写 pending 流水。
    ★ 与真实实现一样，**绝不写 right_level**。
    """
    a = STORE.assets.get(asset_id)
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"资产 {asset_id} 不存在")

    from app.config import UPLOAD_DIR
    fpath = (UPLOAD_DIR / a["file_path"]).resolve()
    if not fpath.exists():
        raise HTTPException(status.HTTP_410_GONE, "原图文件已丢失，无法分析")

    feat = extract_from_bytes(fpath.read_bytes())
    vec = np.asarray(feat.vector, dtype=np.float64)

    motif_hits = _search_motifs(vec, settings.TOP_K)
    asset_hits = _search_assets(vec, asset_id, settings.TOP_K)
    suggestion = suggest_level(motif_hits, asset_hits)

    with STORE._lock:
        # 写入/覆盖特征向量（1:1）
        fv_id = STORE.next_id("fv")
        STORE.feature_vectors[asset_id] = {
            "fv_id": fv_id, "asset_id": asset_id,
            "model_name": feat.model_name, "model_version": feat.model_version,
            "embed_dim": feat.embed_dim, "embedding": feat.vector, "created_at": _now(),
        }

        sim_json = [
            {"id": h.id, "code": h.code, "name": h.name,
             "similarity": h.similarity, "distance": h.distance}
            for h in motif_hits
        ]
        audit_id = STORE.next_id("audit")
        STORE.audit_records[audit_id] = {
            "audit_id": audit_id, "asset_id": asset_id, "expert_id": None,
            "state": "pending", "ai_suggest_level": suggestion.level,
            "ai_confidence": suggestion.confidence, "expert_level": None,
            "similarity_json": sim_json, "opinion": None, "is_override": False,
            "created_at": _now(), "decided_at": None,
        }

        old = dict(a)
        a["ai_suggest_level"] = suggestion.level
        a["ai_confidence"] = suggestion.confidence
        a["ai_model_version"] = f"{feat.model_name}@{feat.model_version}"
        a["audit_status"] = "pending"      # ★ 注意：没有 right_level
        # 母题血缘：判为公有母题/改编衍生时记录依据的母题（与 services.analyze_asset 一致）
        a["motif_id"] = (motif_hits[0].id
                         if (motif_hits and suggestion.level in ("public", "derived")) else None)
        a["version_no"] += 1
        a["updated_at"] = _now()
        STORE.audit_asset_change(old, a, app_role)

    plan = (
        "演示模式（内存存储）：无 HNSW 索引，检索退化为 numpy 精确全量扫描\n"
        f"  候选母题数 = {len(STORE.motifs)}，特征维度 = {feat.embed_dim}\n"
        "  相似度 = clip(内积, 0, 1)（向量已 L2 归一化，故内积即余弦）\n"
        "  真实模式下的执行计划会是 Index Scan using idx_public_motif_hnsw"
    ) if include_plan else None

    return {
        "asset_id": asset_id, "audit_id": audit_id,
        "suggested_level": suggestion.level, "confidence": suggestion.confidence,
        "reason": suggestion.reason, "rule": suggestion.rule,
        "flagged_as": suggestion.flagged_as, "margin": suggestion.margin,
        "top_k": [_hit_dict(h) for h in motif_hits],
        "similar_assets": [_hit_dict(h) for h in asset_hits],
        "model": {
            "name": feat.model_name, "version": feat.model_version,
            "dim": feat.embed_dim, **current_backend_info(),
        },
        "plan": plan,
    }


def list_pending(limit: int = 100) -> list[dict]:
    """等价于 SELECT * FROM v_asset_pending_audit（取每个资产最新一条 pending）。"""
    out = []
    for a in STORE.assets.values():
        if a["audit_status"] != "pending":
            continue
        pend = [r for r in STORE.audit_records.values()
                if r["asset_id"] == a["asset_id"] and r["state"] == "pending"]
        pend.sort(key=lambda r: r["created_at"], reverse=True)
        ar = pend[0] if pend else None
        owner = STORE.users.get(a["owner_id"], {})
        sim = (ar or {}).get("similarity_json") or []
        out.append({
            "asset_id": a["asset_id"], "title": a["title"], "owner_id": a["owner_id"],
            "owner_name": owner.get("display_name", ""), "owner_org": owner.get("org_name"),
            "current_right_level": a["right_level"],
            "ai_suggest_level": a["ai_suggest_level"], "ai_confidence": a["ai_confidence"],
            "ai_model_version": a["ai_model_version"], "uploaded_at": a["created_at"],
            "audit_id": (ar or {}).get("audit_id"),
            "submitted_at": (ar or {}).get("created_at"),
            "top1_motif_name": sim[0]["name"] if sim else None,
            "top1_similarity": sim[0]["similarity"] if sim else None,
            "similarity_json": sim or None,
            "license_count": sum(1 for l in STORE.license_records.values()
                                 if l["asset_id"] == a["asset_id"]),
        })
    out.sort(key=lambda r: r["submitted_at"] or r["uploaded_at"], reverse=True)
    return out[:limit]


def approve_asset(*, asset_id: int, final_level: str, opinion: str,
                  expert_id: int, expert_name: str) -> dict:
    """
    终审 —— 全系统唯一写 right_level 的入口。

    在真实系统里这是「BEGIN → SELECT ... FOR UPDATE → 校验 → UPDATE audit_record
    → UPDATE asset（触发器留痕）→ COMMIT」。演示模式下用 STORE._lock 保证原子性，
    四步顺序与校验逻辑保持一致，任一步抛错则不做任何修改。
    """
    with STORE._lock:
        a = STORE.assets.get(asset_id)
        if not a:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"资产 {asset_id} 不存在")
        if a["audit_status"] == "approved":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"资产 {asset_id} 已终审通过，禁止重复终审（并发保护生效）")
        if a["audit_status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"资产 {asset_id} 当前状态为 {a['audit_status']}，只有 pending 状态可终审。"
                                f"请先执行 AI 分析。")

        pend = [r for r in STORE.audit_records.values()
                if r["asset_id"] == asset_id and r["state"] == "pending"]
        if not pend:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"资产 {asset_id} 没有待审流水（数据不一致），请重新执行 AI 分析")
        pend.sort(key=lambda r: r["created_at"], reverse=True)
        audit = pend[0]

        is_override = bool(audit["ai_suggest_level"] and audit["ai_suggest_level"] != final_level)

        # 步骤 4：写终审结论
        audit["state"] = "approved"
        audit["expert_id"] = expert_id
        audit["expert_level"] = final_level
        audit["opinion"] = opinion
        audit["is_override"] = is_override
        audit["decided_at"] = _now()

        # 步骤 5：改资产权属（模拟触发器留痕）
        old = dict(a)
        a["right_level"] = final_level
        a["audit_status"] = "approved"
        a["version_no"] += 1
        a["updated_at"] = _now()
        log_id = STORE.audit_asset_change(old, a, f"expert:{expert_id}")

    return {
        "asset_id": asset_id, "audit_id": audit["audit_id"],
        "right_level": final_level, "audit_status": "approved",
        "is_override": is_override, "expert_name": expert_name,
        "db_audit_log_ids": [log_id] if log_id else [],
        "message": f"终审完成：判定为 {final_level}"
                   + ("（专家推翻了 AI 建议）" if is_override else "（与 AI 建议一致）"),
    }


def reject_asset(*, asset_id: int, opinion: str, expert_id: int) -> dict:
    with STORE._lock:
        a = STORE.assets.get(asset_id)
        if not a:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"资产 {asset_id} 不存在")
        if a["audit_status"] != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                f"只有 pending 状态可驳回，当前 {a['audit_status']}")
        pend = [r for r in STORE.audit_records.values()
                if r["asset_id"] == asset_id and r["state"] == "pending"]
        if not pend:
            raise HTTPException(status.HTTP_409_CONFLICT, "没有待审流水可驳回")
        pend.sort(key=lambda r: r["created_at"], reverse=True)
        audit = pend[0]
        audit.update({
            "state": "rejected", "expert_id": expert_id, "opinion": opinion,
            "expert_level": audit["ai_suggest_level"], "decided_at": _now(),
        })
        old = dict(a)
        a["audit_status"] = "rejected"
        a["version_no"] += 1
        a["updated_at"] = _now()
        STORE.audit_asset_change(old, a, f"expert:{expert_id}")
    return {"asset_id": asset_id, "audit_id": audit["audit_id"],
            "message": "已驳回，资产退回待修改"}


def create_license(*, asset_id: int, payload: dict, actor_id: int,
                   app_role: str, is_privileged: bool) -> dict:
    with STORE._lock:
        a = STORE.assets.get(asset_id)
        if not a:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"资产 {asset_id} 不存在")
        if a["audit_status"] != "approved":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"资产尚未终审通过（当前 {a['audit_status']}），不能登记授权。"
                f"权属未定即授权，等于给出无法兑现的承诺。",
            )
        if not is_privileged and a["owner_id"] != actor_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "只能为自己的作品登记授权")

        lic_type = payload.get("license_type") or "non_exclusive"
        if lic_type == "exclusive":
            conflict = [l for l in STORE.license_records.values()
                        if l["asset_id"] == asset_id
                        and l["license_type"] == "exclusive" and l["status"] == "active"]
            if conflict:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "该资产已存在一份现行有效的独占授权（部分唯一索引 uq_lic_exclusive_active 拦截）；"
                    "请先撤销或等待其失效",
                )

        lid = STORE.next_id("license")
        STORE.license_records[lid] = {
            **payload, "license_id": lid, "asset_id": asset_id,
            "licensor_id": actor_id, "status": "active", "created_at": _now(),
        }
        return {"license_id": lid}


def list_licenses(asset_id: int) -> list[dict]:
    rows = [l for l in STORE.license_records.values() if l["asset_id"] == asset_id]
    rows.sort(key=lambda l: l["created_at"])
    out = []
    for l in rows:
        lic = STORE.users.get(l["licensor_id"], {})
        lee = STORE.users.get(l.get("licensee_user_id") or 0, {})
        out.append({**l, "licensor_name": lic.get("display_name"),
                    "licensee_name": l.get("licensee_name") or lee.get("display_name")})
    return out


def trace_asset(asset_id: int, app_role: str, viewer_id: int, is_privileged: bool) -> dict:
    """
    溯源链路 —— 对应视图 v_asset_trace_chain 的四类节点：
    root / audit / license / system_log。
    并做与应用层一致的链式完整性校验。
    """
    a = get_asset(asset_id, app_role, viewer_id, is_privileged)
    nodes: list[dict] = []

    nodes.append({
        "seq_no": 1, "event_at": a["created_at"], "event_type": "root",
        "summary": f"资产登记：{a['title']}",
        "actor_name": a.get("owner_name"), "actor_role": "heritor",
        "detail": {"asset_id": a["asset_id"], "title": a["title"],
                   "file_hash": a["file_hash"], "file_size": a["file_size"],
                   "owner": a.get("owner_name"), "right_level": a["right_level"]},
    })

    for ar in sorted(STORE.audit_records.values(), key=lambda r: r["created_at"]):
        if ar["asset_id"] != asset_id:
            continue
        label = {
            "pending": "提交 AI 预审（待专家终审）",
            "expert_review": "专家领取审核",
            "approved": "专家终审通过",
            "rejected": "专家终审驳回",
        }.get(ar["state"], ar["state"])
        expert = STORE.users.get(ar["expert_id"] or 0, {})
        nodes.append({
            "seq_no": 2, "event_at": ar.get("decided_at") or ar["created_at"],
            "event_type": "audit", "summary": label,
            "actor_name": expert.get("display_name") or "系统(AI 特征引擎)",
            "actor_role": expert.get("role") or "system",
            "detail": {"audit_id": ar["audit_id"], "state": ar["state"],
                       "ai_suggest_level": ar["ai_suggest_level"],
                       "ai_confidence": ar["ai_confidence"],
                       "expert_level": ar["expert_level"],
                       "is_override": ar["is_override"], "opinion": ar["opinion"],
                       "similarity_json": ar["similarity_json"]},
        })

    for l in sorted(STORE.license_records.values(), key=lambda x: x["created_at"]):
        if l["asset_id"] != asset_id:
            continue
        lic = STORE.users.get(l["licensor_id"], {})
        lee = STORE.users.get(l.get("licensee_user_id") or 0, {})
        nodes.append({
            "seq_no": 3, "event_at": l["created_at"], "event_type": "license",
            "summary": ("独占授权" if l["license_type"] == "exclusive" else "授权流转")
                       + " → " + (l.get("licensee_name") or lee.get("display_name") or "未知被授权方"),
            "actor_name": lic.get("display_name"), "actor_role": lic.get("role"),
            "detail": {"license_id": l["license_id"], "license_type": l["license_type"],
                       "licensee": l.get("licensee_name") or lee.get("display_name"),
                       "scope": l.get("scope"), "territory": l.get("territory"),
                       "start_date": str(l.get("start_date")), "end_date": str(l.get("end_date")),
                       "fee_cny": str(l.get("fee_cny")) if l.get("fee_cny") else None,
                       "status": l["status"], "contract_hash": l.get("contract_hash"),
                       "prev_license_id": l.get("prev_license_id")},
        })

    for s in sorted(STORE.audit_log.values(), key=lambda x: x["logged_at"]):
        if s["record_id"] != asset_id:
            continue
        nodes.append({
            "seq_no": 4, "event_at": s["logged_at"], "event_type": "system_log",
            "summary": "DB 自动审计：" + ",".join(s["changed_fields"] or ["-"]),
            "actor_name": s["actor"], "actor_role": "db_trigger",
            "detail": {"log_id": s["log_id"], "operation": s["operation"],
                       "changed_fields": s["changed_fields"],
                       "old_snapshot": s["old_snapshot"], "new_snapshot": s["new_snapshot"]},
        })

    nodes.sort(key=lambda n: (n["event_at"], n["seq_no"]))

    # 同 services.trace_asset：视图/内存实现给的 seq_no 是"节点类别"
    # （root=1 / audit=2 / license=3 / system_log=4），一条链上会有多个 system_log，
    # 直接展示就是 1→4→4→2→4→3。这里按实际发生时间重编 1..N。
    for i, n in enumerate(nodes, 1):
        n["seq_no"] = i

    # ---- 链式完整性校验（与真实实现一致的四项）----
    issues: list[str] = []
    if not (len(a["file_hash"]) == 64 and all(c in "0123456789abcdef" for c in a["file_hash"])):
        issues.append("资产登记节点的 file_hash 不是合法的 SHA-256")
    lic_nodes = [n for n in nodes if n["event_type"] == "license"]
    if lic_nodes:
        ids = {n["detail"]["license_id"] for n in lic_nodes}
        for n in lic_nodes:
            prev = n["detail"].get("prev_license_id")
            if prev is not None and prev not in ids:
                issues.append(f"授权 #{n['detail']['license_id']} 的 prev_license_id={prev} 在链中不存在")
    if not any(n["event_type"] == "audit" and n["detail"].get("state") in ("approved", "rejected")
               for n in nodes):
        if a["audit_status"] == "approved":
            issues.append("资产状态为已终审，但链路中未找到终审决策节点")
    if not any(n["event_type"] == "system_log" for n in nodes):
        issues.append("链路中缺少数据库触发器写入的审计节点（system_log）")

    return {
        "asset_id": asset_id, "title": a["title"], "file_hash": a["file_hash"],
        "right_level": a["right_level"], "audit_status": a["audit_status"],
        "is_chain_valid": not issues, "chain_issues": issues, "nodes": nodes,
    }


def get_ledger(app_role: str) -> dict:
    """等价于视图 v_asset_ledger + 汇总。"""
    rows = []
    for a in sorted(STORE.assets.values(), key=lambda x: x["created_at"], reverse=True):
        owner = STORE.users.get(a["owner_id"], {})
        motif = STORE.motifs.get(a["motif_id"] or 0)
        lics = [l for l in STORE.license_records.values() if l["asset_id"] == a["asset_id"]]
        active = [l for l in lics if l["status"] == "active"]
        fee = sum(float(l["fee_cny"]) for l in active if l.get("fee_cny"))
        logs = [s for s in STORE.audit_log.values() if s["record_id"] == a["asset_id"]]
        audits = [r for r in STORE.audit_records.values() if r["asset_id"] == a["asset_id"]]
        # 对应视图里那个 LATERAL 子查询：取最近一次带相似度快照的审核流水
        with_sim = [r for r in audits
                    if r.get("similarity_json") and len(r["similarity_json"]) > 0]
        with_sim.sort(key=lambda r: r["created_at"], reverse=True)
        top1_sim = (with_sim[0]["similarity_json"][0].get("similarity") if with_sim else None)
        rows.append({
            "asset_id": a["asset_id"], "title": a["title"],
            "owner_name": owner.get("display_name", ""), "owner_org": owner.get("org_name"),
            "right_level": a["right_level"], "audit_status": a["audit_status"],
            "ai_suggest_level": a["ai_suggest_level"], "ai_confidence": a["ai_confidence"],
            "top1_similarity": top1_sim,
            "ai_expert_agree": (a["ai_suggest_level"] == a["right_level"])
                                if a["ai_suggest_level"] else None,
            "motif_code": (motif or {}).get("motif_code"),
            "motif_name": (motif or {}).get("name"),
            "file_hash_short": a["file_hash"][:16], "version_no": a["version_no"],
            "created_at": a["created_at"], "updated_at": a["updated_at"],
            "license_cnt": len(lics), "active_license_cnt": len(active),
            "total_fee_cny": fee, "audit_times": len(audits),
            "db_log_cnt": len(logs),
            "last_log_at": max((s["logged_at"] for s in logs), default=None),
            "derivative_cnt": sum(1 for g in STORE.genealogy.values()
                                  if g["parent_asset_id"] == a["asset_id"]),
        })

    by_level: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for r in rows:
        by_level[r["right_level"]] = by_level.get(r["right_level"], 0) + 1
        by_status[r["audit_status"]] = by_status.get(r["audit_status"], 0) + 1

    decided = [r for r in rows if r["ai_expert_agree"] is not None]
    agree_rate = (sum(1 for r in decided if r["ai_expert_agree"]) / len(decided)) if decided else None

    return {
        "summary": {
            "total_assets": len(rows),
            "by_level": by_level,
            "by_status": by_status,
            "ai_agree_rate": agree_rate,
            "total_license_fee_cny": sum(r["total_fee_cny"] for r in rows),
            "pending_count": by_status.get("pending", 0),
            "db_audit_log_count": len(STORE.audit_log),
        },
        "rows": rows,
    }


def list_motifs(app_role: str) -> list[dict]:
    """母题库列表。字段与 services.list_motifs 保持一致，另加演示用的结构化元信息。"""
    out = []
    for mid, m in STORE.motifs.items():
        v = STORE.motif_vector(mid)      # 触发懒加载，让 has_vector 反映真实状态
        out.append({
            "motif_id": mid, "motif_code": m["motif_code"], "name": m["name"],
            "region": m["region"], "description": m["description"],
            "reference_img": m["reference_img"], "vector_model": m["vector_model"],
            "has_vector": v is not None, "created_at": m["created_at"],
            # ---- 结构化描述（展示页用）----
            "ethnic": m.get("ethnic"), "category": m.get("category"),
            "era": m.get("era"), "keywords": m.get("keywords") or [],
            "vector_dim": int(v.shape[0]) if v is not None else None,
            # 该母题在库中作为"血缘依据"被多少件已确权作品引用
            "asset_refs": sum(
                1 for a in STORE.assets.values()
                if a.get("motif_id") == mid and a["audit_status"] == "approved"
            ),
        })
    return out


def users(role: str | None = None) -> list[dict]:
    rows = []
    for u in STORE.users.values():
        if role and u["role"] != role:
            continue
        rows.append({k: u[k] for k in ("user_id", "username", "display_name", "role", "org_name")})
    rows.sort(key=lambda r: r["user_id"])
    return rows


def health_tables() -> dict[str, int]:
    """模拟 /api/health 的表行数统计。"""
    return {
        "app_user": len(STORE.users),
        "public_motif": len(STORE.motifs),
        "asset": len(STORE.assets),
        "feature_vector": len(STORE.feature_vectors),
        "audit_record": len(STORE.audit_records),
        "license_record": len(STORE.license_records),
        "asset_genealogy": len(STORE.genealogy),
        "system_audit_log": len(STORE.audit_log),
    }
