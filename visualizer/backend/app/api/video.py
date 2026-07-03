import os
import uuid
import logging
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Query, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from app.models.schemas import (
    ExtractRequest, ExtractResponse, VideoStatus,
    ExtractBatchRequest, ExtractBatchResponse, ClipTaskResult, FrameTaskStatus,
)
from app.services.video_extractor import extract_topic_to_mp4, extract_topic_hevc_stream, get_task
from app.services.frame_extractor import (
    extract_frames_from_bag,
    _resolve_bag_path_via_dm, _resolve_bag_path_local,
    _find_default_camera_topic, _download_bag_from_oss,
    create_batch_task, update_batch_clip, update_batch_status, get_batch_task,
    FRAME_OUTPUT_DIR,
)
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
async def stream_hevc(
    request: Request,
    bag_path: str = Query(..., description="Bag path"),
    topic: str = Query(..., description="Camera topic"),
    start_ts: Optional[int] = Query(None, description="Start timestamp (ns)"),
    end_ts: Optional[int] = Query(None, description="End timestamp (ns)"),
    fps: Optional[float] = Query(None, description="Override FPS"),
):
    """Stream HEVC remuxed to fMP4 for MSE playback. No local file is created.
    
    修复：通过 Request.is_disconnected() 检测客户端断开，
    将 stop_event 传入 generator，确保客户端关闭页面后后端资源立即释放，
    避免新旧 stream 冲突导致卡死。
    """
    logger.info(
        "Starting HEVC stream: bag=%s topic=%s range=[%s, %s]",
        bag_path, topic, start_ts, end_ts,
    )
    try:
        stream_gen, stop_event = extract_topic_hevc_stream(bag_path, topic, start_ts, end_ts, fps)
        # 启动后台任务：轮询客户端断开状态，一旦断开立即 set stop_event
        async def _watch_disconnect():
            while not stop_event.is_set():
                if await request.is_disconnected():
                    logger.info("[stream] Client disconnected, signaling stop_event")
                    stop_event.set()
                    return
                await asyncio.sleep(0.5)
        import asyncio
        # 把 watch 任务挂到 FastAPI 的 background（不阻塞响应）
        # 注意：不能用 BackgroundTasks，因为它在响应完成后才执行；
        # 我们需要在响应发送期间就监控断开。
        # 使用 asyncio.ensure_future 在当前 event loop 中启动监控协程。
        _watch_task = asyncio.ensure_future(_watch_disconnect())
        
        response = StreamingResponse(
            stream_gen,
            media_type="video/mp4",
            headers={
                "Content-Disposition": 'inline; filename="stream.mp4"',
            },
        )
        # 在响应发送完成后取消监控任务
        response.background = _watch_task.cancel
        return response
    except Exception as exc:
        logger.exception("HEVC stream failed")
        from app.core.exceptions import AppException
        raise AppException(str(exc), 500)


# ── VL 验证：批量提取 + 抽帧 API ──

DEFAULT_CAMERA_TOPIC = "/gac/cam/ft30_encoded"


def _process_batch_clips(task_id: str, req: ExtractBatchRequest) -> None:
    """后台任务：逐条处理 clips"""
    import time
    update_batch_status(task_id, "processing", "Starting batch extraction")

    for idx, clip in enumerate(req.clips):
        clip_result = {
            "bag_id": clip.bag_id,
            "status": "processing",
            "frame_count": 0,
            "frame_urls": [],
            "message": "",
        }
        update_batch_clip(task_id, idx, clip_result)

        try:
            # 1. 解析 bag 路径
            bag_path = None
            oss_path = None

            if req.resolve_bag_path:
                dm_result = _resolve_bag_path_via_dm(clip.bag_id)
                bag_path = dm_result.local_path
                oss_path = dm_result.oss_path

            if not bag_path:
                local_result = _resolve_bag_path_local(clip.bag_id)
                bag_path = local_result.local_path
                if not oss_path:
                    oss_path = local_result.oss_path

            if not bag_path:
                # 最后尝试：当作直接路径
                if os.path.exists(clip.bag_id):
                    bag_path = clip.bag_id

            # 2. 本地路径不存在时，尝试 OSS 下载
            if not bag_path and oss_path:
                update_batch_clip(task_id, idx, {
                    **clip_result, "message": f"Downloading from OSS: {oss_path}"
                })
                bag_path = _download_bag_from_oss(oss_path, f"{task_id}/clip{idx}", clip.bag_id)

            if not bag_path:
                clip_result.update(status="failed", message=f"Cannot resolve bag_id={clip.bag_id} (local not mounted, OSS not available)")
                update_batch_clip(task_id, idx, clip_result)
                continue

            # 2. 确定 camera topic
            topic = clip.topic
            if not topic:
                topic = _find_default_camera_topic(bag_path)
            if not topic:
                topic = DEFAULT_CAMERA_TOPIC

            # 3. 抽帧
            clip_output_dir = os.path.join(FRAME_OUTPUT_DIR, task_id, f"clip_{idx:03d}")
            frame_paths = extract_frames_from_bag(
                bag_path=bag_path,
                topic=topic,
                output_dir=clip_output_dir,
                start_ts=clip.start_ts,
                end_ts=clip.end_ts,
                sample_fps=req.sample_fps,
                max_frames=req.max_frames_per_clip,
                task_id=f"{task_id}/clip{idx}",
            )

            # 4. 生成帧下载 URL
            frame_urls = []
            for fi, fpath in enumerate(frame_paths):
                fname = os.path.basename(fpath)
                frame_urls.append(f"/api/video/frames/{task_id}/{idx}/{fname}")

            clip_result.update(
                status="completed",
                frame_count=len(frame_paths),
                frame_urls=frame_urls,
                message=f"Extracted {len(frame_paths)} frames from {topic}",
            )
            update_batch_clip(task_id, idx, clip_result)

        except Exception as exc:
            logger.exception("[%s] clip %d failed", task_id, idx)
            clip_result.update(status="failed", message=str(exc))
            update_batch_clip(task_id, idx, clip_result)

    update_batch_status(task_id, "completed", "Batch extraction done")


@router.post("/extract-batch", response_model=ExtractBatchResponse)
def extract_batch(req: ExtractBatchRequest, background_tasks: BackgroundTasks):
    """批量提取视频 + 抽帧：输入 SQL 查询结果列表（bag_id + 时间范围），自动解析路径、提取帧、输出 JPEG"""
    task_id = str(uuid.uuid4())[:8]
    logger.info("Starting batch extraction task=%s with %d clips", task_id, len(req.clips))

    # 初始化任务状态
    clip_results = [
        ClipTaskResult(bag_id=c.bag_id, status="pending")
        for c in req.clips
    ]
    create_batch_task(task_id, len(req.clips))

    # 启动后台处理
    background_tasks.add_task(_process_batch_clips, task_id, req)

    return ExtractBatchResponse(
        task_id=task_id,
        clips=clip_results,
        status="pending",
        message="Batch extraction started",
    )


@router.get("/extract-batch/{task_id}", response_model=FrameTaskStatus)
def batch_status(task_id: str):
    """查询批量抽帧任务状态"""
    task = get_batch_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Batch task {task_id} not found")
    clips = [
        ClipTaskResult(**c) if c else ClipTaskResult(bag_id="unknown", status="pending")
        for c in task["clips"]
    ]
    return FrameTaskStatus(
        task_id=task_id,
        status=task["status"],
        clips=clips,
        message=task.get("message", ""),
    )


@router.get("/frames/{task_id}/{clip_idx}/{filename}")
def serve_frame(task_id: str, clip_idx: int, filename: str):
    """下载抽帧 JPEG 图片"""
    # 安全检查：防止路径穿越
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    fpath = os.path.join(FRAME_OUTPUT_DIR, task_id, f"clip_{clip_idx:03d}", filename)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(fpath, media_type="image/jpeg", filename=filename)
