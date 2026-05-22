import os
import sys
import logging
import subprocess
from typing import List, Optional, Dict

from app.core.config import settings
from app.core.exceptions import ExtractionFailedException

# Ensure proto paths are available (absolute path from project root parent)
_PROTO_BASE = settings.PROJECT_ROOT.parent / "data_mining/UBM_mining/ubm_data_mining/gsbag_parser/proto/v4.8.3"
sys.path.append(str(_PROTO_BASE))
sys.path.append(str(_PROTO_BASE / "j6"))

from gsbag import gsbag_reader
from j6.image_encode import boleidl_pb2 as image_encode_boleidl_pb2

logger = logging.getLogger(__name__)

# Task registry for tracking extraction jobs (in-memory; for production consider Redis + Celery)
_tasks: Dict[str, Dict] = {}

VIDEO_OUTPUT_DIR = settings.VIDEO_OUTPUT_DIR
os.makedirs(VIDEO_OUTPUT_DIR, exist_ok=True)


def get_task(task_id: str) -> Optional[Dict]:
    return _tasks.get(task_id)


def _update_task(task_id: str, **kwargs) -> None:
    if task_id not in _tasks:
        _tasks[task_id] = {}
    _tasks[task_id].update(kwargs)


def _get_bag_time_range(bag_path: str):
    """从 metadata.yaml 读取 bag 的起止时间戳（纳秒），用于 clamp 视频提取范围。"""
    import yaml
    metadata_path = os.path.join(bag_path, "metadata.yaml")
    if not os.path.exists(metadata_path):
        return None, None
    try:
        with open(metadata_path, "r") as f:
            meta = yaml.safe_load(f)
        info = meta.get("gacbag_bagfile_information", {})
        duration_sec = info.get("duration", {}).get("seconds", 0)
        start_time_obj = info.get("start_time")
        start_time_ns = None
        end_time_ns = None
        if start_time_obj and isinstance(start_time_obj, dict):
            s = start_time_obj.get("seconds", 0) or 0
            ns = start_time_obj.get("nanoseconds", 0) or 0
            start_time_ns = int(s) * 1_000_000_000 + int(ns)
        elif start_time_obj and isinstance(start_time_obj, (int, float)):
            start_time_ns = int(start_time_obj)
        if start_time_ns is not None:
            end_time_ns = start_time_ns + int(duration_sec * 1_000_000_000)
        return start_time_ns, end_time_ns
    except Exception:
        return None, None


def extract_topic_to_mp4(
    bag_path: str,
    topic: str,
    task_id: str,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
) -> str:
    """Extract HEVC frames from a bag topic and transcode to H.264 MP4.

    Optimizations:
      - set_topic_filter avoids scanning non-target topics.
      - start_ts/end_ts allow extracting a time slice (nanoseconds).
      - Single-pass bag read: frames are buffered in memory (HEVC payloads only).
      - Frames are piped directly into ffmpeg to skip temp-file I/O.
      - Input framerate -r 10 is set so ffmpeg treats raw HEVC correctly.

    Time range clamping:
      - If start_ts < bag start → clamp to bag start
      - If end_ts > bag end → clamp to bag end
      - Prevents "No frames found" when SQL result time range exceeds bag range
    """
    # Clamp start_ts / end_ts to bag's actual time range
    bag_start, bag_end = _get_bag_time_range(bag_path)
    clamped = False
    if start_ts is not None and bag_start is not None and start_ts < bag_start:
        logger.info("[%s] start_ts %d < bag_start %d, clamping to bag_start", task_id, start_ts, bag_start)
        start_ts = bag_start
        clamped = True
    if end_ts is not None and bag_end is not None and end_ts > bag_end:
        logger.info("[%s] end_ts %d > bag_end %d, clamping to bag_end", task_id, end_ts, bag_end)
        end_ts = bag_end
        clamped = True

    logger.info(
        "[%s] Starting extraction: bag=%s topic=%s range=[%s, %s]%s",
        task_id, bag_path, topic, start_ts, end_ts,
        " (clamped)" if clamped else "",
    )
    _update_task(task_id, status="processing", progress=0.0, message="Opening bag...")

    try:
        reader = gsbag_reader.GsBagReader(bag_path)
        reader.set_topic_filter([topic])
    except Exception as exc:
        logger.exception("[%s] Failed to open bag", task_id)
        _update_task(task_id, status="failed", message=f"Failed to open bag: {exc}")
        raise ExtractionFailedException(str(exc))

    _update_task(task_id, message="Reading frames from bag...")

    # Single-pass read: collect HEVC payloads in memory
    hevc_frames: List[bytes] = []
    skipped_decode_errors = 0
    for m in reader.read_messages():
        ts = m.timestamp
        if start_ts is not None and ts < start_ts:
            continue
        if end_ts is not None and ts > end_ts:
            continue

        try:
            msg = image_encode_boleidl_pb2.Image()
            image_data = []
            gsbag_reader.HobotMessageSerializer.deserialize_image(m, msg, image_data)
            if image_data:
                hevc_frames.append(image_data[0])
        except Exception as exc:
            skipped_decode_errors += 1
            if skipped_decode_errors <= 5:
                logger.warning("[%s] Frame decode error: %s", task_id, exc)

    total = len(hevc_frames)
    if total == 0:
        _update_task(task_id, status="failed", progress=0.0, message="No frames found for topic / time range")
        raise ExtractionFailedException(f"No frames found for topic {topic} in the specified range")

    if skipped_decode_errors > 0:
        logger.info("[%s] Skipped %d decode-error frames", task_id, skipped_decode_errors)

    logger.info("[%s] %d frames collected, starting ffmpeg pipe", task_id, total)
    _update_task(task_id, message=f"Transcoding {total} frames to MP4...")

    # Build ffmpeg command: read HEVC raw from stdin, output H.264 MP4
    mp4_path = os.path.join(VIDEO_OUTPUT_DIR, f"{task_id}.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-r", "10",               # input framerate (raw HEVC has no container timestamps)
        "-f", "hevc",
        "-i", "-",                # read from stdin pipe
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-r", "10",               # output framerate
        mp4_path,
    ]

    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        for idx, frame in enumerate(hevc_frames):
            process.stdin.write(frame)
            if idx % 100 == 0:
                progress = min((idx + 1) / total * 80.0, 80.0)
                _update_task(task_id, progress=progress)

        process.stdin.close()
        _update_task(task_id, message="Finalizing video...", progress=90.0)
        returncode = process.wait()
        stderr = process.stderr.read().decode("utf-8", errors="ignore") if process.stderr else ""

        if returncode != 0:
            logger.error("[%s] ffmpeg failed: %s", task_id, stderr)
            _update_task(task_id, status="failed", progress=0.0, message=f"ffmpeg failed: {stderr}")
            raise ExtractionFailedException(stderr)

        logger.info("[%s] Extraction completed: %s (%d frames)", task_id, mp4_path, total)
        _update_task(
            task_id,
            status="completed",
            progress=100.0,
            message="Done",
            video_path=mp4_path,
            frames=total,
        )
        return mp4_path

    except Exception:
        # Ensure ffmpeg process is cleaned up on any error
        try:
            process.stdin.close()
        except Exception:
            pass
        process.wait()
        if os.path.exists(mp4_path):
            os.remove(mp4_path)
        raise
