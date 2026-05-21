from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class BagInfo(BaseModel):
    bag_path: str
    topics: List[Dict]
    duration_sec: float
    message_count: int


class ExtractRequest(BaseModel):
    bag_path: str
    topic: str
    output_filename: Optional[str] = None
    start_ts: Optional[int] = None   # nanoseconds
    end_ts: Optional[int] = None     # nanoseconds


class ExtractResponse(BaseModel):
    task_id: str
    status: str
    video_url: Optional[str] = None
    message: str


class VideoStatus(BaseModel):
    task_id: str
    status: str  # pending, processing, completed, failed
    video_url: Optional[str] = None
    progress: float = 0.0
    message: str = ""


# Agent schemas
class AgentQueryRequest(BaseModel):
    question: str
    db_path: Optional[str] = None  # 可选，留空则使用 batch_id + query_mode
    batch_id: Optional[str] = None  # 批次 ID（db_path 为空时使用）
    query_mode: Optional[str] = None  # "sqlite" | "parquet"（db_path 为空时使用）
    db_limit: int = 30  # 批量查询时最多扫描的 DB 数量
    result_limit: int = 100  # 单条 SQL 返回的最大行数


class AgentQueryResponse(BaseModel):
    sql: str
    explanation: str
    columns: List[str]
    rows: List[Dict[str, Any]]
    error: Optional[str] = None
    scanned_dbs: int = 0
    matched_dbs: int = 0
