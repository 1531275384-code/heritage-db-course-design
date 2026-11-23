"""故意制造不同步，验证漂移检测真的会失败（跑完自动还原）

为什么要做这个实验
------------------
一个从不失败的检查等于没有检查。漂移检测只有在「SQL 改了但没重新生成」
时真的报错，才值得信任。本脚本故意加一条约束、故意改掉触发器判断条件，
确认两条路径都会失败，最后按字节还原并确认指纹回到原值。

按字节读写（newline=""）的原因：Windows 上文本模式的换行符转换会改写文件，
导致「还原成功但指纹变了」，把一次干净的实验变成一次数据污染。
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_raw(p: Path) -> str:
    with p.open("r", encoding="utf-8", newline="") as f:
        return f.read()


def write_raw(p: Path, text: str) -> None:
    with p.open("w", encoding="utf-8", newline="") as f:
        f.write(text)


def run(args):
    return subprocess.run(
        [PY] + args, cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def experiment(name, path, transform, expect_in_output):
    orig = read_raw(path)
    before = sha(path)
    try:
        mutated = transform(orig)
        assert mutated != orig, f"{name}: 变换没有生效"
        write_raw(path, mutated)
        proc = run(["-m", "scripts.gen_rules", "--check"])
        caught = proc.returncode != 0 and expect_in_output in (proc.stdout + proc.stderr)
        print(f"\n[{name}]")
        print(f"  SQL 改动后 --check 退出码 = {proc.returncode}（期望非 0）")
        print(f"  输出命中关键提示 = {expect_in_output in (proc.stdout + proc.stderr)}")
        first = [l for l in (proc.stdout + proc.stderr).splitlines() if l.strip()][:2]
        for l in first:
            print(f"    | {l[:110]}")
        print(f"  => {'实验成立：不同步会被抓住' if caught else '✗ 实验失败：不同步没被发现'}")
        return caught
    finally:
        write_raw(path, orig)
        assert sha(path) == before, f"{name}: 还原失败！文件内容不一致"


ok1 = experiment(
    "改 CHECK：新增一条约束但不重新生成",
    ROOT / "sql/01_init.sql",
    lambda t: t.replace(
        "    CONSTRAINT ck_asset_version     CHECK (version_no >= 0),",
        "    CONSTRAINT ck_asset_version     CHECK (version_no >= 0),\n"
        "    CONSTRAINT ck_probe_temp        CHECK (file_size < 999999999),",
    ),
    "不同步",
)

ok2 = experiment(
    "改触发器：把「未终审不得授权」的判断条件改掉",
    ROOT / "sql/02_index_trigger_view.sql",
    lambda t: t.replace(
        "IF v_status <> 'approved' THEN",
        "IF v_status NOT IN ('approved', 'rejected') THEN",
    ),
    "语义不再匹配",
)

# 还原后必须重新一致
proc = run(["-m", "scripts.gen_rules", "--check"])
print(f"\n[还原校验] --check 退出码 = {proc.returncode}（期望 0）")
print((proc.stdout or proc.stderr).strip()[:160])

sys.exit(0 if (ok1 and ok2 and proc.returncode == 0) else 1)
