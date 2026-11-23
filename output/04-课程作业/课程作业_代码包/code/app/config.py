"""
全局配置。所有配置项均可用环境变量覆盖，便于部署到不同环境。

也支持项目根目录的 `.env` 文件（同名变量：环境变量优先，其次 .env，最后默认值）。
之所以自己写 20 行而不引入 python-dotenv：本项目只有一个用途——
让「换数据库连接」不用每次 export 一串变量；为此多一个依赖不划算。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent          # 工程根目录


def _load_dotenv(path: Path) -> None:
    """
    极简 .env 解析：KEY=VALUE，忽略空行与 # 注释，支持两端引号。
    不覆盖已存在的环境变量（显式 export 应当优先于文件）。
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv(BASE_DIR / ".env")

SQL_DIR = BASE_DIR / "sql"
UPLOAD_DIR = Path(os.getenv("ICH_UPLOAD_DIR", BASE_DIR / "data" / "uploads"))
STATIC_DIR = BASE_DIR / "app" / "static"


class Settings:
    """轻量配置容器（不引入 pydantic-settings，减少依赖）。"""

    # ---------- 数据库 ----------
    # 说明：后端以 ich_app 连接，再通过 SET LOCAL ROLE 切换到业务角色，
    #       实现「数据库层 GRANT + 应用层 JWT」双重鉴权。
    DB_HOST: str = os.getenv("ICH_DB_HOST", "127.0.0.1")
    DB_PORT: int = int(os.getenv("ICH_DB_PORT", "5432"))
    DB_NAME: str = os.getenv("ICH_DB_NAME", "ich_heritage")
    DB_USER: str = os.getenv("ICH_DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("ICH_DB_PASSWORD", "")

    # 连接池大小：演示环境 5 个足够；生产按并发压测结果调整
    DB_POOL_MIN: int = int(os.getenv("ICH_DB_POOL_MIN", "2"))
    DB_POOL_MAX: int = int(os.getenv("ICH_DB_POOL_MAX", "10"))

    # ---------- 鉴权 ----------
    # 演示用固定密钥；生产必须通过环境变量注入随机值，切勿使用默认值
    JWT_SECRET: str = os.getenv("ICH_JWT_SECRET", "dev-only-secret-change-me-in-production")
    JWT_ALG: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("ICH_JWT_EXPIRE_MINUTES", "720"))

    # ---------- AI 特征引擎 ----------
    # clip    : 使用 open_clip / transformers 的 CLIP ViT-B/32 图像编码器（推荐）
    # resnet  : 使用 torchvision ResNet-50 去掉分类头，取 2048 维后降维到 512
    # builtin : 纯 numpy 的「方向梯度+颜色直方图」基线特征（无需 torch，离线可用）
    # auto    : 依次尝试 clip → resnet → builtin
    FEATURE_BACKEND: str = os.getenv("ICH_FEATURE_BACKEND", "auto")
    FEATURE_DIM: int = 512
    TOP_K: int = int(os.getenv("ICH_TOPK", "5"))

    # 分级建议阈值（可调）。语义见 app/ai/classifier.py 的说明。
    SIM_PUBLIC_THRESHOLD: float = float(os.getenv("ICH_SIM_PUBLIC", "0.92"))
    SIM_DERIVED_THRESHOLD: float = float(os.getenv("ICH_SIM_DERIVED", "0.80"))

    # ---------- 上传 ----------
    MAX_UPLOAD_MB: int = int(os.getenv("ICH_MAX_UPLOAD_MB", "20"))
    ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/bmp", "image/tiff"}


settings = Settings()
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
