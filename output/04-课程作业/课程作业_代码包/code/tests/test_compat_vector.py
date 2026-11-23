"""兼容层（sql/00_compat_vector.sql）的离线验证。

为什么需要它：降级模式的核心承诺是
    「自定义 <=> 运算符与 pgvector 的 cosine 距离语义完全一致」
这个承诺如果不验证，就等于没写。没有数据库也能验证的部分有：

  1) 文本字面量 → 向量的解析（'[0.1,0.2,...]' 与 '{0.1,0.2,...}' 两种写法）
  2) cosine 距离公式与 numpy 实现的数值一致性
  3) L2 归一化后，内积 == 1 - cosine距离（这是检索正确性的数学前提）
  4) SQL 文件里的 psql 条件分支结构正确（\\if 必有 \\endif，变量都设过）

用法：
    python tests/test_compat_vector.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"

PASS = 0
FAIL = 0


def check(label: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}" + (f"  {extra}" if extra else ""))
    else:
        FAIL += 1
        print(f"  ✗ {label}  ← 失败  {extra}")


def section(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


# =============================================================================
section("1. 降级模式 ich_to_vector 的解析逻辑（Python 等价实现）")


def ich_to_vector(txt: str) -> list[float]:
    """与 sql/00 里 PL/pgSQL 版逐行对应的实现，用来验证解析行为一致。"""
    s = txt.strip().strip("[]{}")
    return [float(x) for x in s.split(",")]


raw = [0.1, -0.25, 0.3333, 0.9, -0.7]
bracket = "[" + ",".join(f"{x:.7f}" for x in raw) + "]"     # vector_literal 的产出格式
brace = "{" + ",".join(f"{x:.7f}" for x in raw) + "}"

a = ich_to_vector(bracket)
b = ich_to_vector(brace)
check("'[...]' 格式可解析", len(a) == len(raw), f"{len(a)} 维")
check("'{...}' 格式同样可解析（两种写法等价）", a == b)
check("解析结果与原始数值一致（7 位小数内）",
      all(abs(x - y) < 1e-6 for x, y in zip(a, raw)))


# =============================================================================
section("2. cosine 距离公式与 numpy 实现一致")


def ich_cosine_distance(a: list[float], b: list[float]) -> float:
    """与 sql/00 里 PL/pgSQL 版对应的实现：1 - (A·B)/(|A||B|)"""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 1.0
    return 1.0 - dot / (na * nb)


rng = np.random.default_rng(42)
for trial in range(5):
    u = rng.normal(size=512)
    v = rng.normal(size=512)
    d_sql = ich_cosine_distance(list(u), list(v))
    d_np = 1.0 - float(u @ v / (np.linalg.norm(u) * np.linalg.norm(v)))
    check(f"随机向量对 #{trial + 1}：SQL 公式 == numpy", abs(d_sql - d_np) < 1e-9,
          f"差 {abs(d_sql - d_np):.2e}")

# 边界：完全相同 / 完全相反 / 正交
same = [0.1] * 512
opp = [-0.1] * 512
orth = [1.0, 0.0] + [0.0] * 510
orth2 = [0.0, 1.0] + [0.0] * 510
check("相同向量距离 = 0", abs(ich_cosine_distance(same, same)) < 1e-12)
check("相反向量距离 = 2", abs(ich_cosine_distance(same, opp) - 2.0) < 1e-12)
check("正交向量距离 = 1", abs(ich_cosine_distance(orth, orth2) - 1.0) < 1e-12)
check("零向量不除零（返回 1.0）", ich_cosine_distance([0.0] * 512, same) == 1.0)


# =============================================================================
section("3. L2 归一化后：内积 == 1 - cosine 距离（检索正确性的数学前提）")


def l2_normalize(v: list[float]) -> list[float]:
    n = sum(x * x for x in v) ** 0.5
    return v if n == 0 else [x / n for x in v]


u = list(rng.normal(size=512))
v = list(rng.normal(size=512))
un, vn = l2_normalize(u), l2_normalize(v)

inner = sum(x * y for x, y in zip(un, vn))
sim_from_dist = 1.0 - ich_cosine_distance(un, vn)
check("归一化后 内积 == 1-距离", abs(inner - sim_from_dist) < 1e-9,
      f"内积={inner:.9f}  1-d={sim_from_dist:.9f}")
check("归一化后范数 = 1", abs(sum(x * x for x in un) ** 0.5 - 1.0) < 1e-9)

print("\n  这一步为什么关键：")
print("  similarity.py 里用 `GREATEST(0.0, LEAST(1.0, 1.0 - (<=>)))` 当相似度。")
print("  只有当向量做过 L2 归一化时，(<=>) 才等于纯角度差，相似度才落在 [0,1]。")
print("  否则模长会污染结果 —— 一张整体偏暗的图会被误判为「更不相似」。")


# =============================================================================
section("4. SQL 脚本的 psql 条件分支结构")

compat = (SQL_DIR / "00_compat_vector.sql").read_text(encoding="utf-8")
init = (SQL_DIR / "01_init.sql").read_text(encoding="utf-8")
idx = (SQL_DIR / "02_index_trigger_view.sql").read_text(encoding="utf-8")
seed = (SQL_DIR / "03_seed.sql").read_text(encoding="utf-8")

check("00_compat_vector.sql 存在且非空", len(compat) > 1000, f"{len(compat)} 字符")
check("定义了 ich_vector_is_native()", "FUNCTION ich_vector_is_native()" in compat)
check("定义了 ich_cosine_distance()", "FUNCTION ich_cosine_distance" in compat)
check("定义了 l2_normalize()", "FUNCTION l2_normalize" in compat)
check("定义了 vector_dims()", "FUNCTION vector_dims" in compat)
check("定义了 ich_to_vector()", "FUNCTION ich_to_vector" in compat)
check("创建了 <=> 运算符", "CREATE OPERATOR <=>" in compat)
check("降级对象全在 IF NOT ich_vector_is_native() 保护内", compat.count("ich_vector_is_native()") >= 2,
      "避免与 pgvector 的同名运算符冲突")

for name, text in (("01_init.sql", init), ("02_index_trigger_view.sql", idx), ("03_seed.sql", seed)):
    n_if = len(re.findall(r"^\\if\b", text, flags=re.M))
    n_endif = len(re.findall(r"^\\endif\b", text, flags=re.M))
    check(f"{name}: \\if 与 \\endif 配对", n_if == n_endif, f"if={n_if} endif={n_endif}")

check("01_init.sql 用 \\i 引入兼容层", "\\i 00_compat_vector.sql" in init)
check("01_init.sql 向量列用 :ICH_VEC_TYPE 变量", init.count(":ICH_VEC_TYPE") >= 2,
      f"{init.count(':ICH_VEC_TYPE')} 处（母题表 + 特征表）")
check("02 索引脚本自身也探测模式（可单独执行）", "\\gset" in idx)
check("02 HNSW 索引被 \\if 保护", "\\if :use_native" in idx and "hnsw" in idx.lower())
check("03 种子脚本向量字面量按模式转换", ":ICH_CAST" in seed)
check("母题表有维度 CHECK（降级模式补的类型约束）", "ck_public_motif_vdim" in init)
check("特征表有维度 CHECK", "ck_fv_vdim" in init)

# 反向检查：不该出现未受模式保护的 ::vector 强转（会绕过 cast_vec / 模式分支）
bare = []
for f in ("01_init.sql", "02_index_trigger_view.sql", "03_seed.sql"):
    text = (SQL_DIR / f).read_text(encoding="utf-8")
    lines = text.splitlines()
    # 找出所有处于 `IF v_native THEN` 分支内的行号（fn_motif_vector_normalize 里的合法用法）
    protected: set[int] = set()
    depth_start = None
    for i, line in enumerate(lines):
        if "IF v_native THEN" in line:
            depth_start = i
        elif depth_start is not None and line.strip() == "END IF;":
            protected.update(range(depth_start, i + 1))
            depth_start = None

    for i, line in enumerate(lines):
        s = line.strip()
        if "::vector" not in s or s.startswith("--") or "ICH_CAST" in s:
            continue
        if i in protected:
            continue
        bare.append(f"{f}:{i + 1}  {s[:50]}")
check("SQL 中无未受保护的 ::vector 强转", not bare, "; ".join(bare[:3]))

check("触发器 fn_motif_vector_normalize 按模式分支归一化",
      "IF v_native THEN" in idx and "unnest(NEW.base_vector)" in idx,
      "原生走 ::real[]，降级直接 unnest")


# =============================================================================
section("5. 应用层 cast_vec 的行为")
sys.path.insert(0, str(ROOT))
import app.ai.similarity as sim  # noqa: E402

sim._NATIVE = True
native_expr = sim.cast_vec("%(q)s")
sim._NATIVE = False
fallback_expr = sim.cast_vec("%(q)s")
sim._NATIVE = None      # 复位，避免影响其他测试

check("原生模式生成 ::vector 强转", native_expr == "%(q)s::vector", native_expr)
check("降级模式生成 ich_to_vector() 包装", fallback_expr == "ich_to_vector(%(q)s)", fallback_expr)

src = (ROOT / "app" / "ai" / "similarity.py").read_text(encoding="utf-8")
check("search_motifs 不再硬编码 ::vector", "m.base_vector <=> {v}" in src)
check("_apply_vector_settings 在降级模式跳过 HNSW 参数",
      "if not is_native():\n        return" in src)

svc = (ROOT / "app" / "services.py").read_text(encoding="utf-8")
check("services 的向量写入走 cast_vec", svc.count("cast_vec(") >= 2, f"{svc.count('cast_vec(')} 处")


# =============================================================================
print(f"\n{'=' * 70}")
print(f"  结果：{PASS} 项通过 / {FAIL} 项失败")
print(f"{'=' * 70}")
print("\n提醒：本测试验证的是**公式与脚本结构**，不等价于在真实 PostgreSQL 上执行。")
print("      降级模式的实际可用性仍需要在装有 PostgreSQL 15+ 的机器上跑一遍 sql/01~03。")
sys.exit(0 if FAIL == 0 else 1)
