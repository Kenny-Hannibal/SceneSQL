import os
import uuid
import logging
from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import FileResponse
from app.models.schemas import ExtractRequest, ExtractResponse, VideoStatus
from app.services.video_extractor import extract_topic_to_mp4, get_task
from app.core.config import settings

router = APIRouter(prefix="/api/video", tags=["video"])
logger = logging.getLogger(__name__)


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
