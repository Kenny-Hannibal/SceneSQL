import os
import re
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
_MULTI_STREAM_WORKER_SCRIPT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "services", "multi_stream_worker.py"
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


# ── rosbag 路径解析（camera 流用） ──

def _find_bag_file(bag_path: str) -> Optional[str]:
    """在给定路径中查找 bag.bag 文件。

    bag_path 可能是:
    - 直接包含 bag.bag 的目录
    - 包含子目录的父目录（子目录里有 bag.bag）
    - bag.bag 文件本身

    Returns: bag.bag 的完整路径，或 None
    """
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
                    logger.info("Found bag.bag in subdirectory: %s", candidate)
                    return candidate
    return None


def _extract_bag_id_from_input(input_str: str) -> Optional[str]:
    """从输入字符串中提取 bag_id（纯字母数字，长度 > 10）。"""
    if not input_str:
        return None
    if "/" not in input_str and len(input_str) > 10 and re.match(r"^[a-zA-Z0-9]+$", input_str):
        return input_str
    basename = os.path.basename(input_str.rstrip("/"))
    if len(basename) > 10 and re.match(r"^[a-zA-Z0-9]+$", basename):
        return basename
    if basename.endswith(".db") and len(basename) > 14:
        return basename[:-3]
    return None


def _resolve_rosbag_for_stream(bag_path: str) -> str:
    """为视频流解析 rosbag 路径。

    流程：
    1. bag_path 里直接有 bag.bag → 用它
    2. bag_path 看起来是 bag_id → dm_sdk 查 rosbag 路径
    3. 都不行 → 原样返回（让 stream_worker 报错）

    注意：前端现在应该传 rosbag_path（从 get_bag_info 返回的），
    此函数仅作为兜底，处理旧前端或 SQL 可视化按钮的路径解析。
    """
    # 1. 直接找到 bag.bag
    bag_file = _find_bag_file(bag_path)
    if bag_file:
        return bag_file

    # 2. 尝试 dm_sdk 解析 rosbag 路径
    bag_id = _extract_bag_id_from_input(bag_path)
    if bag_id:
        try:
            from tools.rosbag_path_resolver import RosbagPathResolver

            token = settings.DM_ACCESS_TOKEN
            if not token:
                logger.warning("DM_ACCESS_TOKEN not configured")
                return bag_path

            resolver = RosbagPathResolver(
                access_token=token,
                table=settings.DM_PROD_TABLE,
                oss_mount_map=settings.OSS_MOUNT_MAP,
            )
            info = resolver.resolve(bag_id)

            # 检查本地挂载路径
            if info.local_path and os.path.exists(info.local_path):
                local_bag = _find_bag_file(info.local_path)
                if local_bag:
                    logger.info("dm_sdk: bag_id=%s → rosbag=%s", bag_id, local_bag)
                    return local_bag

            # 本地不可用 → OSS 下载
            if info.oss_path:
                from app.services.frame_extractor import _download_bag_from_oss
                logger.info("dm_sdk: bag_id=%s → downloading rosbag from OSS: %s", bag_id, info.oss_path)
                local_dir = _download_bag_from_oss(info.oss_path, f"stream_{bag_id}", bag_id)
                if local_dir:
                    local_bag = _find_bag_file(local_dir)
                    if local_bag:
                        logger.info("dm_sdk: bag_id=%s → downloaded rosbag=%s", bag_id, local_bag)
                        return local_bag

            logger.warning("dm_sdk: bag_id=%s → rosbag not found", bag_id)
        except Exception as exc:
            logger.warning("dm_sdk resolve rosbag failed for bag_id=%s: %s", bag_id, exc)

    return bag_path


def _spawn_stream_worker(mode, bag_path, topic, start_ts, end_ts, fps):
    """
    启动 stream_worker.py 子进程，返回 Popen 对象。

    bag_path 应该是 rosbag 路径（含 bag.bag），
    如果不是，会通过 _resolve_rosbag_for_stream 兜底解析。
    """
    resolved_path = _resolve_rosbag_for_stream(bag_path)

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
    bag_path: str = Query(..., description="Rosbag path (should contain bag.bag)"),
    topic: str = Query(..., description="Camera topic"),
    start_ts: Optional[int] = Query(None, description="Start timestamp (ns)"),
    end_ts: Optional[int] = Query(None, description="End timestamp (ns)"),
    fps: Optional[float] = Query(None, description="Override FPS"),
):
    """Stream HEVC remuxed to fMP4 for MSE playback.

    bag_path 应该是 rosbag 路径（从 get_bag_info 返回的 rosbag_path 字段），
    如果没有 bag.bag，会自动通过 dm_sdk 解析 rosbag 路径作为兜底。
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
    bag_path: str = Query(..., description="Rosbag path (should contain bag.bag)"),
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


@router.get("/stream-multi")
async def stream_multi(
    request: Request,
    bag_path: str = Query(..., description="Rosbag path (should contain bag.bag)"),
    topics: str = Query(..., description="Comma-separated camera topics"),
    mode: str = Query("hevc", description="hevc or h264"),
    start_ts: Optional[int] = Query(None, description="Start timestamp (ns)"),
    end_ts: Optional[int] = Query(None, description="End timestamp (ns)"),
    fps: Optional[float] = Query(None, description="Override FPS"),
):
    """多topic共享Reader流式播放。

    1个gsbag_reader读bag，按topic路由到N个ffmpeg进程。
    fMP4输出复用为单条二进制流，协议：
      [topic_idx:1byte][data_len:4bytes LE][fMP4_data:data_len bytes]

    前端demux后分别喂入各topic的MSE SourceBuffer。
    """
    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    if not topic_list:
        from app.core.exceptions import AppException
        raise AppException("No topics specified", 400)
    if mode not in ("hevc", "h264"):
        from app.core.exceptions import AppException
        raise AppException(f"Invalid mode: {mode}", 400)

    logger.info(
        "Starting multi-stream: bag=%s topics=%s mode=%s range=[%s, %s]",
        bag_path, topic_list, mode, start_ts, end_ts,
    )
    try:
        resolved_path = _resolve_rosbag_for_stream(bag_path)

        cmd = [sys.executable, _MULTI_STREAM_WORKER_SCRIPT,
               "--bag-path", resolved_path,
               "--topics", ",".join(topic_list),
               "--mode", mode]
        if start_ts is not None:
            cmd.extend(["--start-ts", str(start_ts)])
        if end_ts is not None:
            cmd.extend(["--end-ts", str(end_ts)])
        if fps is not None:
            cmd.extend(["--fps", str(fps)])

        logger.info("Spawning multi-stream worker: %s", " ".join(cmd))
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=None,
        )

        # 检查子进程是否立刻失败
        import time
        time.sleep(0.1)
        if process.poll() is not None:
            stdout_out = process.stdout.read().decode("utf-8", errors="ignore") if process.stdout else ""
            logger.error("Multi-stream worker exited immediately: %s", stdout_out)
            raise RuntimeError(f"Multi-stream worker failed to start: {stdout_out[:500]}")

        # 客户端断开后 kill 子进程
        async def _watch_disconnect():
            while process.poll() is None:
                if await request.is_disconnected():
                    logger.info("[multi-stream] Client disconnected, killing worker (pid=%s)", process.pid)
                    _kill_worker(process)
                    return
                await asyncio.sleep(0.5)

        _watch_task = asyncio.ensure_future(_watch_disconnect())

        response = StreamingResponse(
            _stream_from_worker(process, request),
            media_type="application/octet-stream",
            headers={
                "X-Multi-Stream": "true",
                "X-Topics": ",".join(topic_list),
                "Content-Disposition": 'inline; filename="multi_stream.bin"',
            },
        )

        async def _on_finish():
            _watch_task.cancel()
            if process.poll() is None:
                _kill_worker(process)

        response.background = _on_finish
        return response
    except Exception as exc:
        logger.exception("Multi-stream failed")
        from app.core.exceptions import AppException
        raise AppException(str(exc), 500)


# ── VL 验证：批量提取 + 抽帧 API ──

DEFAULT_CAMERA_TOPIC = "/gac/cam/orig_fw120_encoded"


def _process_batch_clips(task_id: str, req: ExtractBatchRequest) -> None:
    """后台任务：逐条处理 clips

    入口2（SQL结果可视化）：bag_id → dm_sdk 查 rosbag 路径 → 抽帧
    这部分逻辑和旧 dsw 一致，使用 frame_extractor 中已有的
    _resolve_bag_path_via_dm / _resolve_bag_path_local / _download_bag_from_oss。
    """
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
            # 1. 解析 bag 路径（rosbag 路径，用于 camera）
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

            # 3. 确定 camera topic
            topic = clip.topic
            if not topic:
                topic = _find_default_camera_topic(bag_path)
            if not topic:
                topic = DEFAULT_CAMERA_TOPIC

            # 4. 抽帧
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

            # 5. 生成帧下载 URL
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
