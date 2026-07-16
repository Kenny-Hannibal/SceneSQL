import os
import sys
import uuid
import json
import logging
import asyncio
import subprocess
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Query, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from app.models.schemas import (
    ExtractRequest, ExtractResponse, VideoStatus,
    ExtractBatchRequest, ExtractBatchResponse, ClipTaskResult, FrameTaskStatus,
)
from app.services.video_extractor import extract_topic_to_mp4, get_task
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

# stream_worker.py 的路径（在 app/services/ 下，不是 app/api/services/）
_STREAM_WORKER_SCRIPT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "services", "stream_worker.py"
))


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


def _resolve_rosbag_path(bag_path: str) -> str:
    """如果 bag_path 是目录且不含 bag.bag，自动查找子目录中的 bag.bag"""
    if os.path.isfile(bag_path):
        return bag_path
    # 直接目录下有 bag.bag
    direct = os.path.join(bag_path, "bag.bag")
    if os.path.isfile(direct):
        return direct
    # 搜索一级子目录
    if os.path.isdir(bag_path):
        for entry in sorted(os.listdir(bag_path)):
            sub = os.path.join(bag_path, entry)
            if os.path.isdir(sub):
                candidate = os.path.join(sub, "bag.bag")
                if os.path.isfile(candidate):
                    logger.info("Resolved rosbag: %s -> %s", bag_path, candidate)
                    return candidate
    return bag_path


def _spawn_stream_worker(mode, bag_path, topic, start_ts, end_ts, fps):
    """
    启动 stream_worker.py 子进程，返回 Popen 对象。

    子进程负责 gsbag 读帧 + ffmpeg 编码，fMP4 chunks 写 stdout。
    父进程从 process.stdout 读取并通过 StreamingResponse 转发给浏览器。
    客户端断开后，父进程 kill 子进程，OS 回收所有资源（包括 gsbag 全局锁）。
    """
    # 自动解析 rosbag 路径（em bin 目录 -> 子目录中的 bag.bag）
    resolved_path = _resolve_rosbag_path(bag_path)
    cmd = [sys.executable, _STREAM_WORKER_SCRIPT,
           "--mode", mode,
           "--bag-path", resolved_path,
           "--topic", topic]
    if start_ts is not None:
        cmd.extend(["--start-ts", str(start_ts)])
    if end_ts is not None:
        cmd.extend(["--end-ts", str(end_ts)])
    if fps is not None:
        cmd.extend(["--fps", str(fps)])

    logger.info("Spawning stream worker: %s", " ".join(cmd))
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,   # fMP4 数据流 → 父进程 StreamingResponse
        stderr=None,              # 子进程日志直接继承 uvicorn stderr，避免 PIPE 死锁
    )

    # 检查子进程是否立刻失败（如 import 错误）
    import time
    time.sleep(0.1)
    if process.poll() is not None:
        # 无法读取 stderr（因为没设 PIPE），从 stdout 读错误信息
        stdout_out = process.stdout.read().decode("utf-8", errors="ignore") if process.stdout else ""
        logger.error("Stream worker exited immediately: %s", stdout_out)
        raise RuntimeError(f"Stream worker failed to start: {stdout_out[:500]}")

    return process


def _stream_from_worker(process, request):
    """
    从 worker 子进程的 stdout 读取 fMP4 chunks 并 yield。
    客户端断开后由外层逻辑 kill 子进程。
    """
    try:
        while True:
            chunk = process.stdout.read(262144)
            if not chunk:
                break
            yield chunk
    except Exception as exc:
        logger.debug("Stream read error: %s", exc)
    finally:
        # 无论正常结束还是异常，都确保子进程被终止
        logger.info("Stream ended, ensuring worker process is terminated (pid=%s)", process.pid)
        _kill_worker(process)


def _kill_worker(process, timeout=3):
    """
    终止 worker 子进程并等待其退出。
    先 SIGTERM，超时后 SIGKILL，确保进程不会残留。
    """
    if process.poll() is not None:
        return  # 已经退出
    try:
        process.terminate()  # SIGTERM
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("Worker process (pid=%s) did not exit after SIGTERM, sending SIGKILL", process.pid)
            process.kill()  # SIGKILL
            process.wait(timeout=2)
    except Exception as exc:
        logger.warning("Error killing worker process: %s", exc)


@router.get("/stream-hevc")
async def stream_hevc(
    request: Request,
    bag_path: str = Query(..., description="Bag path"),
    topic: str = Query(..., description="Camera topic"),
    start_ts: Optional[int] = Query(None, description="Start timestamp (ns)"),
    end_ts: Optional[int] = Query(None, description="End timestamp (ns)"),
    fps: Optional[float] = Query(None, description="Override FPS"),
):
    """Stream HEVC remuxed to fMP4 for MSE playback.

    进程隔离架构：通过 subprocess 启动 stream_worker.py，
    gsbag 读帧 + ffmpeg 编码在子进程中执行。
    客户端断开后 kill 子进程，OS 回收所有资源（fd/mmap/gsbag 全局锁），
    彻底解决锁不释放导致下次请求卡死的问题。
    """
    logger.info(
        "Starting HEVC stream: bag=%s topic=%s range=[%s, %s]",
        bag_path, topic, start_ts, end_ts,
    )
    try:
        process = _spawn_stream_worker("hevc", bag_path, topic, start_ts, end_ts, fps)

        # 后台监控：客户端断开后立即 kill 子进程
        async def _watch_disconnect():
            while process.poll() is None:
                if await request.is_disconnected():
                    logger.info("[stream] Client disconnected, killing worker (pid=%s)", process.pid)
                    _kill_worker(process)
                    return
                await asyncio.sleep(0.5)

        _watch_task = asyncio.ensure_future(_watch_disconnect())

        response = StreamingResponse(
            _stream_from_worker(process, request),
            media_type="video/mp4",
            headers={
                "Content-Disposition": 'inline; filename="stream.mp4"',
            },
        )

        async def _on_finish():
            _watch_task.cancel()
            # 兜底：如果客户端断开但 watch 还没来得及 kill，在响应结束后也确保 kill
            if process.poll() is None:
                _kill_worker(process)

        response.background = _on_finish
        return response
    except Exception as exc:
        logger.exception("HEVC stream failed")
        from app.core.exceptions import AppException
        raise AppException(str(exc), 500)


@router.get("/stream-h264")
async def stream_h264(
    request: Request,
    bag_path: str = Query(..., description="Bag path"),
    topic: str = Query(..., description="Camera topic"),
    start_ts: Optional[int] = Query(None, description="Start timestamp (ns)"),
    end_ts: Optional[int] = Query(None, description="End timestamp (ns)"),
    fps: Optional[float] = Query(None, description="Override FPS"),
):
    """Stream H.264 transcoded fMP4 for MSE playback.

    进程隔离架构：与 /stream-hevc 相同，但 ffmpeg 输出 H.264 编码。
    """
    logger.info(
        "Starting H.264 stream: bag=%s topic=%s range=[%s, %s]",
        bag_path, topic, start_ts, end_ts,
    )
    try:
        process = _spawn_stream_worker("h264", bag_path, topic, start_ts, end_ts, fps)

        async def _watch_disconnect():
            while process.poll() is None:
                if await request.is_disconnected():
                    logger.info("[h264-stream] Client disconnected, killing worker (pid=%s)", process.pid)
                    _kill_worker(process)
                    return
                await asyncio.sleep(0.5)

        _watch_task = asyncio.ensure_future(_watch_disconnect())

        response = StreamingResponse(
            _stream_from_worker(process, request),
            media_type="video/mp4",
            headers={
                "Content-Disposition": 'inline; filename="stream_h264.mp4"',
            },
        )

        async def _on_finish():
            _watch_task.cancel()
            if process.poll() is None:
                _kill_worker(process)

        response.background = _on_finish
        return response
    except Exception as exc:
        logger.exception("H.264 stream failed")
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

    clip_results = [
        ClipTaskResult(bag_id=c.bag_id, status="pending")
        for c in req.clips
    ]
    create_batch_task(task_id, len(req.clips))

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
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    fpath = os.path.join(FRAME_OUTPUT_DIR, task_id, f"clip_{clip_idx:03d}", filename)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(fpath, media_type="image/jpeg", filename=filename)
