"""规则对拍：同一组边界样本，一边喂真实数据库，一边喂 Python 规则，比对判定是否一致。

要防的事
--------
`sim/rules.py` 是从 SQL 自动生成的，`sim/verdict.py` 是照规则表求值的。
但「生成正确」和「求值正确」是两件事：解析对了不代表语义对。
例如 NUMERIC(5,4) 在 CHECK 之前会先把 1.00004 取整成 1.0000，库因此放行；
Python 若拿原始值判 `<= 1`，结论就相反。这类偏差不会报错，只会让仿真内核
安静地和真实系统分叉 —— 而这正是「规则单一真相」要消灭的东西。

三层样本，只有第一层参与「一致率」
----------------------------------
1. rule        行内规则 + 触发器语义。两侧都判，必须 100% 一致。
2. permission  权限边界。Python 的规则模型不管 GRANT，只断言「库按预期拒绝」，
               并同时断言 Python 不因此报规则违反 —— 权限不是规则，别混为一谈。
3. db_only     唯一性、外键。判定依赖库里已有哪些行，Python 侧有意不判，
               只断言「真写一次确实被拒」。把它们塞进规则层才是自欺欺人。

判定只看「接受 / 拒绝」，不看报错码。原因：同一个样本可能先被触发器拦、
也可能先被 CHECK 拦，顺序属于实现细节；但「库到底收不收这条数据」不含糊。

用法
----
    python -m tests.test_rules_parity              # 连默认便携库
    PG_DSN="host=... dbname=..." python -m tests.test_rules_parity
    python -m tests.test_rules_parity --self-check # 额外做一次「故意让步」验证

前置：`python -m scripts.pg_run --reset` 建库并灌种子（需要种子里的账号与外键目标）。
本脚本全程在一个事务里跑，结束无条件 ROLLBACK，不留任何数据。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sim.verdict as V  # noqa: E402
from sim.ruleast import And, Cmp, Col, IsNull, Lit, Or  # noqa: E402

DEFAULT_DSN = "host=127.0.0.1 port=55432 user=postgres dbname=heritage"
DSN = os.environ.get("PG_DSN", DEFAULT_DSN)

TS = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
HEX64 = "a" * 64
PK = {
    "asset": "asset_id",
    "license_record": "license_id",
    "audit_record": "audit_id",
    "app_user": "user_id",
    "public_motif": "motif_id",
    "asset_genealogy": "genealogy_id",
    "system_audit_log": "log_id",
    "feature_vector": "fv_id",
}

# 夹具资产的状态，供 gate 规则取父行
ASSET_FIXTURES: dict[int, dict[str, Any]] = {
    900001: {"audit_status": "draft", "right_level": "public", "owner_id": 1},
    900002: {"audit_status": "pending", "right_level": "public", "owner_id": 1},
    900003: {"audit_status": "approved", "right_level": "derived", "owner_id": 1},
    900004: {"audit_status": "rejected", "right_level": "public", "owner_id": 1},
}

# 种子里已有的资产状态。只用于 Python 侧查 gate 的父行，不重复插入。
SEED_ASSET_STATUS: dict[int, str] = {
    1001: "approved", 1002: "approved", 1003: "pending", 1004: "approved",
    1005: "approved", 1006: "pending", 1007: "draft", 1008: "approved",
}


def parent_of(asset_id: Any) -> dict[str, Any] | None:
    """按 asset_id 还原父行状态。两侧必须看到同一份事实。"""
    spec = ASSET_FIXTURES.get(asset_id)
    if spec:
        return {"audit_status": spec["audit_status"]}
    status = SEED_ASSET_STATUS.get(asset_id)
    return {"audit_status": status} if status else None


# =============================================================================
#  样本
# =============================================================================
@dataclass(frozen=True)
class Sample:
    name: str
    layer: str                                  # rule / permission / db_only
    table: str
    event: str                                  # INSERT / UPDATE / DELETE
    expect: str                                 # accept / reject
    new: dict[str, Any] | None = None
    old: dict[str, Any] | None = None
    parent: dict[str, Any] | None = None
    changes: tuple[str, ...] = ()               # UPDATE 实际要写的列
    role: str | None = None                     # 以哪个业务角色执行
    gate: str | None = None                     # 声明哪个终审事务标记（approve_asset/reject_asset）
    sqlstate: str | None = None                 # 期望的 SQLSTATE（辅助层用）
    note: str = ""


# ---------------------------------------------------------------- 行构造函数
def asset_row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = dict(
        asset_id=900010, title="对拍样本", owner_id=1, motif_id=1,
        file_path="samples/parity.jpg", file_hash=HEX64, file_size=1024,
        mime_type="image/jpeg", right_level="public", audit_status="draft",
        ai_suggest_level=None, ai_confidence=None, ai_model_version=None,
        version_no=0, description=None, created_at=TS, updated_at=TS,
    )
    row.update(kw)
    return row


def license_row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = dict(
        license_id=900110, asset_id=900003, licensor_id=1, licensee_user_id=2,
        licensee_name=None, license_type="display", scope=None, territory=None,
        start_date=date(2026, 1, 1), end_date=None, fee_cny=Decimal("0"),
        contract_hash=None, status="active", prev_license_id=None, created_at=TS,
    )
    row.update(kw)
    return row


def audit_row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = dict(
        audit_id=900210, asset_id=900003, expert_id=None, state="pending",
        ai_suggest_level=None, ai_confidence=None, expert_level=None,
        similarity_json=None, opinion=None, is_override=False,
        created_at=TS, decided_at=None,
    )
    row.update(kw)
    return row


def genealogy_row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = dict(
        genealogy_id=900310, parent_asset_id=900001, child_asset_id=900002,
        relation_type="adapted", similarity=None, note=None,
        created_by=1, created_at=TS,
    )
    row.update(kw)
    return row


def approved_audit(**kw: Any) -> dict[str, Any]:
    base = dict(expert_id=2, state="approved", expert_level="derived",
                decided_at=TS, opinion="对拍样本")
    base.update(kw)
    return audit_row(**base)


def build_samples() -> list[Sample]:
    S: list[Sample] = []

    # ---------------------------------------------------------- 1 资产行内规则
    S += [
        Sample("合法草稿资产", "rule", "asset", "INSERT", "accept", new=asset_row()),
        Sample("title 为空", "rule", "asset", "INSERT", "reject", new=asset_row(title=None)),
        Sample("title 恰 200 字", "rule", "asset", "INSERT", "accept", new=asset_row(title="汉" * 200)),
        Sample("title 201 字", "rule", "asset", "INSERT", "reject", new=asset_row(title="汉" * 201)),
        Sample("file_hash 大写字母", "rule", "asset", "INSERT", "reject", new=asset_row(file_hash="A" * 64)),
        Sample("file_hash 少一位", "rule", "asset", "INSERT", "reject", new=asset_row(file_hash="a" * 63)),
        Sample("file_size 为 0", "rule", "asset", "INSERT", "reject", new=asset_row(file_size=0)),
        Sample("file_size 为 1", "rule", "asset", "INSERT", "accept", new=asset_row(file_size=1)),
        Sample("置信度下界 0", "rule", "asset", "INSERT", "accept",
               new=asset_row(ai_suggest_level="public", ai_confidence=Decimal("0"))),
        Sample("置信度上界 1", "rule", "asset", "INSERT", "accept",
               new=asset_row(ai_suggest_level="derived", ai_confidence=Decimal("1"))),
        Sample("置信度 -0.0001", "rule", "asset", "INSERT", "reject",
               new=asset_row(ai_suggest_level="derived", ai_confidence=Decimal("-0.0001"))),
        Sample("置信度 1.0001", "rule", "asset", "INSERT", "reject",
               new=asset_row(ai_suggest_level="derived", ai_confidence=Decimal("1.0001"))),
        Sample("置信度 1.00004（NUMERIC 取整）", "rule", "asset", "INSERT", "accept",
               new=asset_row(ai_suggest_level="derived", ai_confidence=Decimal("1.00004")),
               note="库把它存成 1.0000；Python 必须先取整才判得对"),
        Sample("置信度 -0.00004（NUMERIC 取整）", "rule", "asset", "INSERT", "accept",
               new=asset_row(ai_suggest_level="derived", ai_confidence=Decimal("-0.00004")),
               note="库把它存成 0.0000"),
        Sample("只给 AI 级别不给置信度", "rule", "asset", "INSERT", "reject",
               new=asset_row(ai_suggest_level="derived", ai_confidence=None)),
        Sample("AI 级别与置信度都为空", "rule", "asset", "INSERT", "accept",
               new=asset_row(ai_suggest_level=None, ai_confidence=None)),
        Sample("AI 级别非法枚举", "rule", "asset", "INSERT", "reject",
               new=asset_row(ai_suggest_level="bogus", ai_confidence=Decimal("0.5"))),
        Sample("audit_status 非法枚举", "rule", "asset", "INSERT", "reject",
               new=asset_row(audit_status="done")),
        Sample("权属级别 original", "rule", "asset", "INSERT", "accept",
               new=asset_row(right_level="original")),
        Sample("版本号为负", "rule", "asset", "INSERT", "reject", new=asset_row(version_no=-1)),
        Sample("版本号 -0.4（整数取整）", "rule", "asset", "INSERT", "accept",
               new=asset_row(version_no=-0.4), note="库四舍五入成 0"),
        Sample("改标题（合法 UPDATE）", "rule", "asset", "UPDATE", "accept",
               old=asset_row(asset_id=900001, audit_status="draft"),
               new=asset_row(asset_id=900001, audit_status="draft", title="改过的标题"),
               changes=("title",)),
    ]

    # ------------------------------------------------------- 2 授权行内与四种类型
    S += [
        Sample("授权类型 display", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_type="display")),
        Sample("授权类型 exclusive", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900111, asset_id=1008, license_type="exclusive"),
               note="1008 上已有非独占授权，独占位是空的"),
        Sample("授权类型 non_exclusive", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900112, license_type="non_exclusive")),
        Sample("授权类型 derivative", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900113, license_type="derivative")),
        Sample("授权类型非法", "rule", "license_record", "INSERT", "reject",
               new=license_row(license_type="rental")),
        Sample("状态 expired", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900114, status="expired")),
        Sample("状态非法", "rule", "license_record", "INSERT", "reject",
               new=license_row(license_id=900115, status="paused")),
        Sample("结束早于开始", "rule", "license_record", "INSERT", "reject",
               new=license_row(license_id=900116, end_date=date(2025, 12, 31))),
        Sample("结束等于开始", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900117, end_date=date(2026, 1, 1))),
        Sample("结束为空（UNKNOWN 不算违反）", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900118, end_date=None),
               note="三值逻辑：NULL 比较得 UNKNOWN，SQL 视为通过"),
        Sample("授权费为负", "rule", "license_record", "INSERT", "reject",
               new=license_row(license_id=900119, fee_cny=Decimal("-1"))),
        Sample("授权费为 0", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900120, fee_cny=Decimal("0"))),
        Sample("授权费 -0.004（取整为 0）", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900121, fee_cny=Decimal("-0.004"))),
        Sample("授权费 -0.005（取整为 -0.01）", "rule", "license_record", "INSERT", "reject",
               new=license_row(license_id=900122, fee_cny=Decimal("-0.005"))),
        Sample("被授权方两种标识都缺", "rule", "license_record", "INSERT", "reject",
               new=license_row(license_id=900123, licensee_user_id=None, licensee_name=None)),
        Sample("只有外部机构名", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900124, licensee_user_id=None, licensee_name="某博物馆")),
        Sample("自己授权给自己", "rule", "license_record", "INSERT", "reject",
               new=license_row(license_id=900125, licensor_id=1, licensee_user_id=1)),
        Sample("被授权方为空但授权方是自己", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900126, licensor_id=1, licensee_user_id=None,
                               licensee_name="某公司")),
        Sample("合同摘要格式非法", "rule", "license_record", "INSERT", "reject",
               new=license_row(license_id=900127, contract_hash="xyz")),
        Sample("合同摘要合法", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900128, contract_hash=HEX64)),
    ]

    # ------------------------------------------------------------ 3 未终审不得授权
    for aid, status in ((900001, "draft"), (900002, "pending"), (900004, "rejected")):
        S.append(
            Sample(f"给 {status} 资产登记授权", "rule", "license_record", "INSERT", "reject",
                   new=license_row(license_id=900130 + aid % 10, asset_id=aid),
                   parent={**ASSET_FIXTURES[aid]},
                   note="gate 触发器：父行必须已终审通过")
        )
    S.append(
        Sample("给 approved 资产登记授权", "rule", "license_record", "INSERT", "accept",
               new=license_row(license_id=900139, asset_id=900003),
               parent={**ASSET_FIXTURES[900003]},
               note="反向对照：只有这一条能过，说明前面三条不是「一律拒绝」")
    )

    # ---------------------------------------------------------- 4 审核状态机与不可篡改
    S += [
        Sample("待审记录（合法）", "rule", "audit_record", "INSERT", "accept", new=audit_row()),
        Sample("终审通过记录（合法）", "rule", "audit_record", "INSERT", "accept",
               new=approved_audit(audit_id=900211)),
        Sample("终审通过但缺专家", "rule", "audit_record", "INSERT", "reject",
               new=approved_audit(audit_id=900212, expert_id=None)),
        Sample("终审通过但缺裁决时间", "rule", "audit_record", "INSERT", "reject",
               new=approved_audit(audit_id=900213, decided_at=None)),
        Sample("审核状态非法", "rule", "audit_record", "INSERT", "reject",
               new=audit_row(audit_id=900214, state="finish")),
        Sample("待审迁移到专家评审", "rule", "audit_record", "UPDATE", "accept",
               old={"audit_id": 900200, "state": "pending"},
               new={**audit_row(audit_id=900200, state="expert_review")},
               changes=("state",)),
        Sample("已通过的记录再改（非法跃迁）", "rule", "audit_record", "UPDATE", "reject",
               old={"audit_id": 900201, "state": "approved"},
               new={**approved_audit(audit_id=900201, state="pending")},
               changes=("state",),
               note="guard 触发器：已终审不可改，无论改成什么"),
        Sample("已驳回的记录再改", "rule", "audit_record", "UPDATE", "reject",
               old={"audit_id": 900202, "state": "rejected"},
               new={**approved_audit(audit_id=900202)},
               changes=("state",)),
        Sample("删除审核流水", "rule", "audit_record", "DELETE", "reject",
               old={"audit_id": 900200}),
        Sample("修改审计日志", "rule", "system_audit_log", "UPDATE", "reject",
               old={"log_id": -1, "operation": "INSERT"},
               new=dict(log_id=-1, table_name="asset", record_id=1, operation="DELETE",
                        actor="tester", old_snapshot=None, new_snapshot=None,
                        changed_fields=None, logged_at=TS),
               changes=("operation",)),
        Sample("删除审计日志", "rule", "system_audit_log", "DELETE", "reject",
               old={"log_id": -1}),
        Sample("删除授权记录", "rule", "license_record", "DELETE", "reject",
               old={"license_id": 900100}),
    ]

    # -------------------------------------------------------------- 5 谱系行内规则
    S += [
        Sample("合法谱系边", "rule", "asset_genealogy", "INSERT", "accept",
               new=genealogy_row(parent_asset_id=900002, child_asset_id=900003)),
        Sample("谱系自指", "rule", "asset_genealogy", "INSERT", "reject",
               new=genealogy_row(genealogy_id=900311, parent_asset_id=900002, child_asset_id=900002)),
        Sample("谱系关系非法", "rule", "asset_genealogy", "INSERT", "reject",
               new=genealogy_row(genealogy_id=900312, relation_type="copied")),
        Sample("谱系相似度越界", "rule", "asset_genealogy", "INSERT", "reject",
               new=genealogy_row(genealogy_id=900313, similarity=Decimal("1.5"))),
    ]

    # ------------------------------------------------------------- 6 权限边界（辅助层）
    S += [
        Sample("传承人改权属级别", "permission", "asset", "UPDATE", "reject",
               old={"asset_id": 900001},
               new=asset_row(asset_id=900001, right_level="original"),
               changes=("right_level",), role="ich_heritor", sqlstate="42501",
               note="列级授权：传承人对 right_level 只有 SELECT"),
        # 这条是本项目最要紧的一道边界：audit_status 必须给传承人写权限
        # （草稿、提交待审），所以列级权限堵不住"自己把状态推到已确权"，
        # 只能由 trg_asset_status_guard 按角色拒绝。样本刻意不含 right_level ——
        # 含了会先被列权限挡下，测出来的就不是这条规则。
        Sample("传承人把状态推到已确权", "permission", "asset", "UPDATE", "reject",
               old={"asset_id": 900001},
               new=asset_row(asset_id=900001, audit_status="approved"),
               changes=("audit_status",), role="ich_heritor", sqlstate="42501",
               note="角色闸门：approved 是专家终审结论，传承人只能推到 pending"),
        Sample("传承人提交待审", "permission", "asset", "UPDATE", "accept",
               old={"asset_id": 900001},
               new=asset_row(asset_id=900001, audit_status="pending"),
               changes=("audit_status",), role="ich_heritor",
               note="正向对照：闸门不能把合法的 draft → pending 也拦掉"),
        Sample("传承人改标题", "permission", "asset", "UPDATE", "accept",
               old={"asset_id": 900001},
               new=asset_row(asset_id=900001, audit_status="draft", title="传承人改的"),
               changes=("title",), role="ich_heritor"),
        Sample("传承人新建草稿", "permission", "asset", "INSERT", "accept",
               new=asset_row(asset_id=900015), role="ich_heritor"),
        Sample("传承人自建「已终审 + 原创」资产", "permission", "asset", "INSERT", "reject",
               new=asset_row(asset_id=900020, audit_status="approved", right_level="original"),
               role="ich_heritor", sqlstate="42501",
               note="上传只能落 draft + public：这条曾经是缺口，现在由闸门的 INSERT 分支拒绝"),
        Sample("专家登记授权", "permission", "license_record", "INSERT", "reject",
               new=license_row(license_id=900140), role="ich_expert", sqlstate="42501",
               note="专家对 license_record 只有 SELECT"),
        Sample("专家直改终态（不走终审事务）", "permission", "asset", "UPDATE", "reject",
               old={"asset_id": 900002},
               new=asset_row(asset_id=900002, audit_status="approved", right_level="derived"),
               changes=("audit_status", "right_level"), role="ich_expert", sqlstate="42501",
               note="终审闸门：没有 ich.finalize_gate 标记的写入一律拒绝"),
        Sample("专家终审改状态", "permission", "asset", "UPDATE", "accept",
               old={"asset_id": 900002},
               new=asset_row(asset_id=900002, audit_status="approved", right_level="derived"),
               changes=("audit_status", "right_level"), role="ich_expert",
               gate="approve_asset",
               note="approve_asset 的写法：同事务声明终审标记；同时验证审计触发器以 DEFINER 写日志"),
        Sample("传承人改审核记录", "permission", "audit_record", "UPDATE", "reject",
               old={"audit_id": 900200},
               new=audit_row(audit_id=900200, opinion="我自己改"),
               changes=("opinion",), role="ich_heritor", sqlstate="42501"),
        Sample("管理员登记授权", "permission", "license_record", "INSERT", "accept",
               new=license_row(license_id=900141, asset_id=900003), role="ich_admin"),
    ]

    # ------------------------------------------------------- 7 唯一性与外键（辅助层）
    S += [
        Sample("用户名重复", "db_only", "app_user", "INSERT", "reject",
               new=dict(user_id=900400, username="heritor01", display_name="重名",
                        role="heritor", password_hash="x", org_name=None,
                        is_active=True, created_at=TS),
               sqlstate="23505", note="Python 侧有意不判唯一性"),
        Sample("同一资产两笔独占授权", "db_only", "license_record", "INSERT", "reject",
               new=license_row(license_id=900150, license_type="exclusive", status="active"),
               sqlstate="23505", note="部分唯一索引只作用于 exclusive+active"),
        Sample("同一资产第二笔非独占授权", "db_only", "license_record", "INSERT", "accept",
               new=license_row(license_id=900151, license_type="non_exclusive", status="active"),
               note="反向对照：唯一索引不该误伤非独占"),
        Sample("资产所有者不存在", "db_only", "asset", "INSERT", "reject",
               new=asset_row(asset_id=900016, owner_id=999999), sqlstate="23503"),
        Sample("谱系边重复", "db_only", "asset_genealogy", "INSERT", "reject",
               new=genealogy_row(genealogy_id=900314, parent_asset_id=900001, child_asset_id=900002),
               sqlstate="23505"),
        Sample("授权链指向前一笔不存在", "db_only", "license_record", "INSERT", "reject",
               new=license_row(license_id=900152, prev_license_id=999999), sqlstate="23503"),
    ]

    return S


# =============================================================================
#  已知缺口探针
# =============================================================================
def build_gap_probes() -> list[Sample]:
    """曾经两侧都不拦、只能如实登记的业务规则。现在这里是空的。

    上一轮挂在这儿的正是两条自我定级路径：
        · 传承人自建「已终审 + 原创」资产
        · 传承人自己把 audit_status 改成 approved
    trg_asset_status_guard 上线后，两条都由数据库拒绝。缺口一关闭，探针就该
    升级成断言 —— 它们已经搬到 build_samples() 的 permission 层（expect=reject），
    从"只报事实"变成"不拒就是红"。

    这个函数留成空壳，不是忘了删：缺口位置得留着，否则下一次发现新缺口时，
    没人想得起来该往哪儿放，只能又写进注释里 —— 那才是真的会丢。
    """
    return []


# =============================================================================
#  夹具
# =============================================================================
def _insert_sql(table: str, row: dict[str, Any]) -> tuple[str, list[Any]]:
    cols = list(row)
    sql = f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({", ".join(["%s"] * len(cols))})'
    return sql, [row[c] for c in cols]


def insert_row(cur, table: str, row: dict[str, Any]) -> None:
    sql, params = _insert_sql(table, row)
    cur.execute(sql, params)


def setup_fixtures(cur) -> dict[str, int]:
    """建立对拍需要的存量行。全程不提交，结束时统一回滚。"""
    for aid, spec in ASSET_FIXTURES.items():
        insert_row(cur, "asset", asset_row(
            asset_id=aid, title=f"对拍夹具 {aid}",
            audit_status=spec["audit_status"], right_level=spec["right_level"],
            owner_id=spec["owner_id"],
        ))
    insert_row(cur, "license_record",
               license_row(license_id=900100, asset_id=900003, license_type="exclusive"))
    insert_row(cur, "audit_record", audit_row(audit_id=900200, state="pending"))
    insert_row(cur, "audit_record", approved_audit(audit_id=900201))
    insert_row(cur, "audit_record", approved_audit(audit_id=900202, state="rejected"))
    insert_row(cur, "asset_genealogy", genealogy_row(genealogy_id=900300))

    cur.execute("SELECT min(log_id) FROM system_audit_log")
    log_id = cur.fetchone()[0]
    return {"log_id": log_id or 0}


# =============================================================================
#  两侧判定
# =============================================================================
def db_attempt(cur, s: Sample, log_id: int) -> tuple[str, str | None, str]:
    """真跑一次。无论成败都回滚到保存点，库保持干净。

    只捕获数据库报错（psycopg2.Error）。样本本身写错（缺 NEW、缺 changes）
    属于脚本缺陷，直接抛出去 —— 把开发错误当成「库拒绝了」会让对拍假通过。
    """
    pk = PK[s.table]

    if s.event == "INSERT":
        if s.new is None:
            raise AssertionError(f"样本 {s.name}: INSERT 缺 new")
        sql, params = _insert_sql(s.table, s.new)
    elif s.event == "UPDATE":
        if s.new is None or not s.changes:
            raise AssertionError(f"样本 {s.name}: UPDATE 缺 new/changes")
        sets = ", ".join(f"{c} = %s" for c in s.changes)
        key = s.new[pk]
        if s.table == "system_audit_log" and key in (-1, None):
            key = log_id
        sql = f"UPDATE {s.table} SET {sets} WHERE {pk} = %s"
        params = [s.new[c] for c in s.changes] + [key]
    elif s.event == "DELETE":
        old = dict(s.old or {})
        if pk not in old:
            raise AssertionError(f"样本 {s.name}: DELETE 缺 old.{pk}")
        key = old[pk]
        if s.table == "system_audit_log" and key in (-1, None):
            key = log_id
        sql, params = f"DELETE FROM {s.table} WHERE {pk} = %s", [key]
    else:
        raise AssertionError(f"未知事件 {s.event}")

    cur.execute("SAVEPOINT parity")
    try:
        if s.role:
            cur.execute(f"SET LOCAL ROLE {s.role}")
        if s.gate:
            # 终审闸门：approve_asset()/reject_asset() 在同一事务里声明的标记。
            # 没这个标记，专家角色也写不进终态 —— 这正是"终审只有一条路"的实现。
            cur.execute("SELECT set_config('ich.finalize_gate', %s, true)", (s.gate,))
        cur.execute(sql, params)
        empty = cur.rowcount == 0 and s.event in ("UPDATE", "DELETE")
        cur.execute("ROLLBACK TO SAVEPOINT parity")
        if empty:
            return "reject", None, "没有匹配的行（夹具缺失）"
        return "accept", None, ""
    except psycopg2.Error as exc:
        cur.execute("ROLLBACK TO SAVEPOINT parity")
        code = getattr(exc, "pgcode", None)
        first = (str(exc).strip().splitlines() or [""])[0][:96]
        return "reject", code, first


def py_verdict(s: Sample) -> tuple[str, list[str]]:
    new = s.new
    old = s.old
    if s.table == "system_audit_log" and old and old.get("log_id") == -1:
        old = {"log_id": 1}      # 占位：判定只看触发器，不看具体 id

    # gate 规则需要父行。样本没显式给时按外键值查夹具 —— 真实库是自己查的，
    # Python 侧必须拿到同一份事实，否则会把「父行存在且已终审」误判成「父行不存在」。
    parent = s.parent
    if parent is None and s.table == "license_record" and s.event == "INSERT" and new:
        parent = parent_of(new.get("asset_id"))

    v = V.check_operation(s.table, s.event, old=old, new=new, parent=parent)
    return ("reject" if v else "accept"), [x.rule for x in v]


# =============================================================================
#  跑一轮
# =============================================================================
def run_once(cur, samples: list[Sample], log_id: int, verbose: bool) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []

    for s in samples:
        db_v, code, msg = db_attempt(cur, s, log_id)
        py_v, rules = py_verdict(s)
        rows.append(dict(s=s, db=db_v, code=code, msg=msg, py=py_v, rules=rules))

    rule_rows = [r for r in rows if r["s"].layer == "rule"]
    parity_bad = [r for r in rule_rows if r["py"] != r["db"]]
    expect_bad = [r for r in rows if r["s"].layer != "gap" and r["db"] != r["s"].expect]

    if verbose:
        for r in rows:
            s = r["s"]
            mark = "✗" if r["py"] != r["db"] else "·"
            print(f"  {mark} {s.name:<28} py={r['py']:<6} db={r['db']:<6} "
                  f"{r['code'] or ''} {','.join(r['rules'])}")
            if s.note:
                print(f"      {s.note}")
            if r["py"] != r["db"]:
                print(f"      库返回：{r['msg']}")

    return dict(rows=rows, rule=rule_rows, parity_bad=parity_bad, expect_bad=expect_bad)


def report(res: dict[str, Any]) -> bool:
    rows = res["rows"]
    rule_rows = res["rule"]
    parity_bad = res["parity_bad"]
    expect_bad = res["expect_bad"]

    def layer(name: str) -> list[dict[str, Any]]:
        return [r for r in rows if r["s"].layer == name]

    ok_rule = len(rule_rows) - len(parity_bad)
    rate = (ok_rule / len(rule_rows) * 100) if rule_rows else 0.0
    print(f"\n[1 规则对拍]  {ok_rule}/{len(rule_rows)} 一致（{rate:.1f}%）")
    for r in parity_bad:
        s = r["s"]
        print(f"  ✗ {s.name}")
        print(f"      表/事件：{s.table} {s.event}")
        print(f"      Python：{r['py']}" + (f"（{', '.join(r['rules'])}）" if r["rules"] else ""))
        print(f"      数据库：{r['db']}" + (f"（{r['code']}）" if r["code"] else ""))
        if r["msg"]:
            print(f"      库返回：{r['msg']}")
        if s.note:
            print(f"      提示：{s.note}")

    perm = layer("permission")
    perm_ok = [r for r in perm if r["db"] == r["s"].expect]
    print(f"\n[2 权限边界]  {len(perm_ok)}/{len(perm)} 符合预期（Python 不判权限，只核对库）")
    for r in perm:
        s = r["s"]
        if r["db"] == s.expect:
            continue
        print(f"  ✗ {s.name}：期望 {s.expect}，实际 {r['db']}（{r['code']}）")
        if s.note:
            print(f"      {s.note}")

    db_only = layer("db_only")
    db_ok = [r for r in db_only if r["db"] == r["s"].expect]
    print(f"\n[3 唯一性与外键]  {len(db_ok)}/{len(db_only)} 符合预期（Python 有意不判，靠真写一次确认）")
    for r in db_only:
        s = r["s"]
        if r["db"] == s.expect:
            continue
        print(f"  ✗ {s.name}：期望 {s.expect}，实际 {r['db']}（{r['code']}）")

    if expect_bad:
        print(f"\n[样本表自身有问题]  {len(expect_bad)} 条与预期不符（可能是样本写错，也可能是库的行为变了）")
        for r in expect_bad:
            s = r["s"]
            print(f"  ! {s.name}：预期 {s.expect}，实际 {r['db']}（{r['code']}）{r['msg']}")

    gaps = layer("gap")
    if gaps:
        print(f"\n[4 已知缺口]  {len(gaps)} 条：两侧都不拦，此处只报事实，不计入一致率")
        for r in gaps:
            s = r["s"]
            if r["db"] == "reject":
                print(f"  ✓ {s.name}：数据库已拒绝（缺口已修，可升级为断言）")
            else:
                print(f"  ! {s.name}：数据库放行（{r['code'] or '无报错'}）")
                if s.note:
                    print(f"      {s.note}")
    else:
        print("\n[4 已知缺口]  无 —— 上一轮登记的两条自我定级路径已被数据库闸门关闭，"
              "探针已升级为权限层断言")

    aux_bad = len(perm) - len(perm_ok) + len(db_only) - len(db_ok)
    total = len(rows) - len(gaps)
    failed = len(parity_bad) + len(expect_bad) + aux_bad
    print(f"\n  断言：{total - failed} 项通过 / {failed} 项失败（共 {total} 组样本，另 {len(gaps)} 条缺口探针）")

    good = not parity_bad and not expect_bad and aux_bad == 0 and len(rule_rows) >= 40
    print()
    print("=" * 74)
    print(f"  对拍：{len(rule_rows)} 组规则样本，{ok_rule} 组一致"
          + ("　→　一致率 100%" if not parity_bad else f"　→　{len(parity_bad)} 组不一致"))
    print(f"  辅助：权限 {len(perm_ok)}/{len(perm)}　唯一性与外键 {len(db_ok)}/{len(db_only)}")
    print("=" * 74)

    if not good:
        if len(rule_rows) < 40:
            print(f"\n样本数不足：规则层只有 {len(rule_rows)} 组，要求 ≥ 40。")
        print("\n提示：对拍失败说明两侧判定不一致，不要改样本去迁就实现。")
        print("      先判断哪一侧对 —— 通常以真实库为准，改 sim/verdict.py 或规则提取器。")
    else:
        print("\n规则层 100% 一致。")
    return good


# =============================================================================
#  自检：故意让步，确认对拍真的会红
# =============================================================================
def perturb_confidence() -> None:
    """把 Python 侧的置信度上界从 1 改成 0.9999。

    这是一次真实的语义偏差（不是伪造结果）：库仍然接受 1，
    Python 会开始拒绝它。若对拍有效，R「置信度上界 1」必须变成不一致。
    """
    for table, rules in V._CHECKS.items():
        for i, rule in enumerate(rules):
            if rule.name == "ck_asset_confidence":
                pred = Or(
                    IsNull(Col("ai_confidence")),
                    And(
                        Cmp(Col("ai_confidence"), ">=", Lit(0)),
                        Cmp(Col("ai_confidence"), "<=", Lit(Decimal("0.9999"))),
                    ),
                )
                rules[i] = replace(rule, pred=pred)


def self_check(cur, samples: list[Sample], log_id: int) -> bool:
    print("\n[自检] 故意把置信度上界改成 0.9999，确认对拍会因此失败")
    saved = {t: list(rs) for t, rs in V._CHECKS.items()}
    try:
        perturb_confidence()
        res = run_once(cur, samples, log_id, verbose=False)
        caught = len(res["parity_bad"])
        names = [r["s"].name for r in res["parity_bad"]]
        print(f"  不一致样本数 = {caught}（期望 ≥ 1）")
        for n in names:
            print(f"    · {n}")
        ok = caught >= 1
        print(f"  {'✓ 自检成立：对拍能抓住语义偏差' if ok else '✗ 自检失败：改了语义却仍然全绿，对拍是摆设'}")
        return ok
    finally:
        for t, rs in saved.items():
            V._CHECKS[t] = rs


# =============================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="真实库与 Python 规则对拍")
    ap.add_argument("--dsn", default=DSN)
    ap.add_argument("-v", "--verbose", action="store_true", help="逐条打印两侧判定")
    ap.add_argument("--self-check", action="store_true", help="额外做一次故意让步的验证")
    args = ap.parse_args()

    samples = build_samples()
    gaps = build_gap_probes()
    rule_n = sum(1 for s in samples if s.layer == "rule")
    print(f"样本：{len(samples)} 组（规则 {rule_n} / 权限 "
          f"{sum(1 for s in samples if s.layer == 'permission')} / "
          f"唯一性与外键 {sum(1 for s in samples if s.layer == 'db_only')}）"
          f"，另 {len(gaps)} 条缺口探针")
    samples = samples + gaps
    print(f"连接：{args.dsn}")

    conn = psycopg2.connect(args.dsn)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            fx = setup_fixtures(cur)
        print(f"夹具已建立（未提交）：资产 {len(ASSET_FIXTURES)} / 授权 1 / 审核 3 / 谱系 1")

        with conn.cursor() as cur:
            res = run_once(cur, samples, fx["log_id"], args.verbose)
            ok = report(res)

            if args.self_check:
                with conn.cursor() as cur2:
                    ok = self_check(cur2, samples, fx["log_id"]) and ok

        conn.rollback()
        print("\n已回滚，未写入任何数据。")

        counts = [
            ("asset", "SELECT count(*) FROM asset"),
            ("license_record", "SELECT count(*) FROM license_record"),
        ]
        with conn.cursor() as cur:
            for label, sql in counts:
                cur.execute(sql)
                print(f"  回滚后 {label} 行数 = {cur.fetchone()[0]}")
        conn.rollback()
        return 0 if ok else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
