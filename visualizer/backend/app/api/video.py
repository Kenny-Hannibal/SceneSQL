import os
import uuid
import logging
import threading
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Query
from fastapi.responses import FileResponse, StreamingResponse
from app.models.schemas import ExtractRequest, ExtractResponse, VideoStatus
from app.services.video_extractor import extract_topic_to_mp4, extract_topic_hevc_stream, get_task
from app.core.config import settings

router = APIRouter(prefix="/api/video", tags=["video"])
logger = logging.getLogger(__name__)

# 全局串行锁：HEVC 流式播放同时只允许一个 stream 在处理，
# 避免多个 gsbag_reader / ffmpeg 进程并发导致资源冲突和卡死。
_hevc_stream_lock = threading.Lock()


@router.post("/extract", response_model=ExtractResponse)
def extract_video(req: ExtractRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())[:8]
    logger.info(
        "Starting extraction task=%s bag=%s topic=%s range=[%s, %s]",
        task_id, req.bag_path, req.topic, req.start_ts, req.end_ts,
    )
    background_tasks.add_task(
        extract_topic_to_mp4,
        req.bag_path,
        req.topic,
        task_id,
        start_ts=req.start_ts,
        end_ts=req.end_ts,
        fps=req.fps,
    )
    return ExtractResponse(
        task_id=task_id,
        status="pending",
        message="Extraction started",
    )


@router.get("/status/{task_id}", response_model=VideoStatus)
def video_status(task_id: str):
    task = get_task(task_id)
    if not task:
        return VideoStatus(task_id=task_id, status="not_found", message="Task not found")
    return VideoStatus(
        task_id=task_id,
        status=task["status"],
        video_url=f"/api/video/file/{task_id}" if task.get("video_path") else None,
        progress=task.get("progress", 0.0),
        message=task.get("message", ""),
    )


@router.get("/file/{task_id}")
def video_file(task_id: str):
    task = get_task(task_id)
    if not task or task.get("status") != "completed":
        from app.core.exceptions import AppException
        raise AppException("Video not ready or not found", 404)
    video_path = task.get("video_path")
    if not video_path or not os.path.exists(video_path):
        from app.core.exceptions import AppException
        raise AppException("Video file not found", 404)
    return FileResponse(video_path, media_type="video/mp4", filename=f"{task_id}.mp4")


@router.get("/stream-hevc")
def stream_hevc(
    bag_path: str = Query(..., description="Bag path"),
    topic: str = Query(..., description="Camera topic"),
    start_ts: Optional[int] = Query(None, description="Start timestamp (ns)"),
    end_ts: Optional[int] = Query(None, description="End timestamp (ns)"),
    fps: Optional[float] = Query(None, description="Override FPS"),
):
    """Stream HEVC remuxed to fMP4 for MSE playback. No local file is created."""
    logger.info(
        "Starting HEVC stream: bag=%s topic=%s range=[%s, %s]",
        bag_path, topic, start_ts, end_ts,
    )

    def _locked_generator():
        # 等待上一个 stream 完全结束（包括 feed 线程释放），避免资源冲突
        with _hevc_stream_lock:
            yield from extract_topic_hevc_stream(bag_path, topic, start_ts, end_ts, fps)

    try:
        return StreamingResponse(
            _locked_generator(),
            media_type="video/mp4",
            headers={
                "Content-Disposition": 'inline; filename="stream.mp4"',
                "Connection": "close",
            },
        )
    except Exception as exc:
        logger.exception("HEVC stream failed")
        from app.core.exceptions import AppException
        raise AppException(str(exc), 500)
