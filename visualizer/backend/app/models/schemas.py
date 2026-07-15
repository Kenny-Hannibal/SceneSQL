from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class BagInfo(BaseModel):
    bag_path: str
    topics: List[Dict]
    duration_sec: float
    message_count: int
    start_time_ns: Optional[int] = None
    end_time_ns: Optional[int] = None


class ExtractRequest(BaseModel):
    bag_path: str
    topic: str
    output_filename: Optional[str] = None
    start_ts: Optional[int] = None   # nanoseconds
    end_ts: Optional[int] = None     # nanoseconds
    fps: Optional[float] = None      # topic 帧率（从 metadata 读取，不传则自动检测）


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
    page: int = 1  # 分页页码（从 1 开始）
    page_size: int = 50  # 每页行数
    max_workers: int = 32  # 批量查询并发数


class AgentQueryResponse(BaseModel):
    sql: str
    explanation: str
    columns: List[str]
    rows: List[Dict[str, Any]]
    error: Optional[str] = None
    scanned_dbs: int = 0
    matched_dbs: int = 0
    total_rows: int = 0  # 总行数（用于分页）
    page: int = 1
    page_size: int = 50
    correction_rounds: int = 0
    max_corrections_exceeded: bool = False


class ExecuteSQLRequest(BaseModel):
    sql: str
    db_path: Optional[str] = None
    batch_id: Optional[str] = None
    query_mode: Optional[str] = None
    db_limit: int = 30
    result_limit: int = 100
    page: int = 1
    page_size: int = 50
    max_workers: int = 32


# ── VL 验证：批量视频提取 + 抽帧 ──

class VideoClipSpec(BaseModel):
    """单条视频片段规格：bag_id + 时间范围"""
    bag_id: str                    # 回灌 bag_id（db 文件名），用于 resolve-bag-path
    start_ts: Optional[int] = None  # 纳秒
    end_ts: Optional[int] = None    # 纳秒
    topic: Optional[str] = None     # camera topic，不传则用默认 /gac/cam/ft30_encoded

class ExtractBatchRequest(BaseModel):
    """批量提取 + 抽帧请求"""
    clips: List[VideoClipSpec]
    sample_fps: float = 1.0        # 抽帧采样帧率（每秒抽几帧），默认 1fps
    max_frames_per_clip: int = 10   # 每段视频最多抽多少帧
    resolve_bag_path: bool = True   # 是否通过 dm_sdk 解析 bag_id→本地路径

class ClipTaskResult(BaseModel):
    """单条 clip 的处理结果"""
    bag_id: str
    status: str                     # pending / processing / completed / failed
    frame_count: int = 0
    frame_urls: List[str] = []      # 帧图片下载 URL 列表
    message: str = ""

class ExtractBatchResponse(BaseModel):
    """批量提取响应"""
    task_id: str
    clips: List[ClipTaskResult]
    status: str = "pending"
    message: str = ""

class FrameTaskStatus(BaseModel):
    """抽帧任务状态查询"""
    task_id: str
    status: str
    clips: List[ClipTaskResult] = []
    message: str = ""
