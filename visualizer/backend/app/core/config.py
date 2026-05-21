import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Rosbag Visualizer API"
    VERSION: str = "1.0.0"
    ENV: str = os.getenv("ENV", "development")

    # CORS
    CORS_ORIGINS: List[str] = ["*"]

    # Paths
    PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent.parent.parent
    VIDEO_OUTPUT_DIR: Path = Path(os.getenv("VIDEO_OUTPUT_DIR", "/tmp/rosbag_videos"))
    GSBAG_SDK: Path = Path(os.getenv("GSBAG_SDK", str(PROJECT_ROOT / "three_party/gsbag_x86_Release_4.2.18_20260227_Linux")))

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = int(os.getenv("PORT", "30001"))

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # --------------------------------------------
    # Database & Path Resolution (shared with Agent)
    # --------------------------------------------
    SQLITE_DB_PATH: Optional[str] = None
    """SQLite DB 路径，支持本地路径或 oss:// 路径"""

    ETL_BASE_PATH: Optional[str] = None
    """Parquet ETL 输出根目录，默认从环境变量 / .env 读取"""

    ETL_BATCH_ID: Optional[str] = None
    """默认激活的 ETL 批次 ID"""

    QUERY_MODE: str = "sqlite"
    """Agent 默认查询模式: sqlite | parquet"""

    OSS_MOUNT_MAP: Optional[str] = None
    """OSS 挂载映射，格式：oss_prefix:local_path,oss_prefix2:local_path2"""

    ROSBAG_MOUNT_BASE: Optional[str] = None
    """本地 rosbag 挂载基础路径"""

    DM_ACCESS_TOKEN: Optional[str] = None
    """dm_sdk access token"""

    DM_PROD_TABLE: str = "ubm_vehicle_module_bin"
    """产线数据表（回灌后的 bag 信息，用于追溯原始 bag）"""

    # --------------------------------------------
    # LLM / Agent
    # --------------------------------------------
    AGENT_MAIN_MODEL: str = "gpt-4o"
    AGENT_FALLBACK_MODEL: str = "gpt-4o"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"


settings = Settings()
