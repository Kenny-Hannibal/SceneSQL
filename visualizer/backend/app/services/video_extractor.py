import os
import sys
import logging
import subprocess
import threading
from typing import List, Optional, Dict
from pathlib import Path

import yaml

from app.core.config import settings
from app.core.exceptions import ExtractionFailedException

# Ensure project root and tools are on sys.path
PROJECT_ROOT = str(settings.PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# j6 protobuf modules need both scripts/proto (for j6.image_encode) and scripts/proto/j6 (for Comm submodule)
for proto_path in [os.path.join(PROJECT_ROOT, "scripts/proto"), os.path.join(PROJECT_ROOT, "scripts/proto/j6")]:
    if proto_path not in sys.path:
        sys.path.insert(0, proto_path)

# gsbag SDK — 可选依赖（本机无 gsbag 时优雅降级）
try:
    from gsbag import gsbag_reader
    _HAS_GSBAG = True
except ImportError:
    gsbag_reader = None
    _HAS_GSBAG = False

try:
    from j6.image_encode import boleidl_pb2 as image_encode_boleidl_pb2
    _HAS_PROTO = True
except ImportError:
    image_encode_boleidl_pb2 = None
    _HAS_PROTO = False

logger = logging.getLogger(__name__)

# Task registry for tracking extraction jobs (in-memory; for production consider Redis + Celery)
_tasks: Dict[str, Dict] = {}

VIDEO_OUTPUT_DIR = settings.VIDEO_OUTPUT_DIR
os.makedirs(VIDEO_OUTPUT_DIR, exist_ok=True)

# ── Video configuration loader ──
_DEFAULT_VIDEO_CONFIG = {
    "input_fps": 10,
    "output_fps": None,  # None = 自动跟随 input_fps
    "crf": 23,
    "preset": "fast",
    "topic_fps_overrides": {},
}

_video_config_cache: Optional[Dict] = None
_video_config_mtime: float = 0.0


def _load_video_config() -> Dict:
    """从 video_config.yaml 读取配置，支持热更新（文件修改后自动重载）。"""
    global _video_config_cache, _video_config_mtime
    config_path = Path(__file__).resolve().parent.parent.parent / "video_config.yaml"
    try:
        mtime = config_path.stat().st_mtime
    except OSError:
        return dict(_DEFAULT_VIDEO_CONFIG)

    if _video_config_cache is not None and mtime == _video_config_mtime:
        return _video_config_cache

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
        merged = dict(_DEFAULT_VIDEO_CONFIG)
        for k, v in user_cfg.items():
            if k in merged:
                merged[k] = type(merged[k])(v)
            elif k == "topic_fps_overrides":
                merged[k] = v if isinstance(v, dict) else {}
        _video_config_cache = merged
        _video_config_mtime = mtime
        logger.info("Loaded video config from %s: %s", config_path, merged)
    except Exception as exc:
        logger.warning("Failed to load video_config.yaml, using defaults: %s", exc)
        merged = dict(_DEFAULT_VIDEO_CONFIG)
        _video_config_cache = merged
    return _video_config_cache


def _get_fps_for_topic(topic: str, config: Dict) -> int:
    """根据 topic 名称返回帧率，优先使用 topic_fps_overrides。"""
    overrides = config.get("topic_fps_overrides", {})
    if topic in overrides:
        return int(overrides[topic])
    # 前缀匹配（如 /camera/front_center 匹配 /camera/front_center/compressed）
    for prefix, fps in overrides.items():
        if topic.startswith(prefix):
            return int(fps)
    return int(config.get("input_fps", 10))


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


def _get_topic_fps(bag_path: str, topic: str) -> Optional[float]:
    """从 metadata.yaml 读取指定 topic 的帧率（message_freq）。"""
    import yaml
    metadata_path = os.path.join(bag_path, "metadata.yaml")
    if not os.path.exists(metadata_path):
        return None
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f)
        info = meta.get("gacbag_bagfile_information", {})
        for t in info.get("topics_with_message_count", []):
            tm = t.get("topic_metadata", {})
            if tm.get("name") == topic:
                return float(t.get("message_freq", 0))
    except Exception:
        pass
    return None


def _resolve_bag_path(bag_path: str, task_id: str) -> str:
    """确保 bag_path 是本地可访问路径。如果是 OSS 路径，尝试下载到临时目录。"""
    # 已经是本地绝对路径
    if os.path.isabs(bag_path) and os.path.exists(bag_path):
        return bag_path

    # 尝试通过 OSS_MOUNT_MAP 解析
    mount_map_str = os.environ.get("OSS_MOUNT_MAP", "")
    if mount_map_str:
        from tools.rosbag_path_resolver import _parse_oss_mount_map, _oss_to_local
        mount_map = _parse_oss_mount_map(mount_map_str)
        local = _oss_to_local(bag_path, mount_map)
        if local and os.path.exists(local):
            logger.info("[%s] Resolved OSS path to local: %s", task_id, local)
            return local

    # 尝试 ossutil 下载到临时目录
    tmp_dir = f"/tmp/bag_staging/{task_id}"
    os.makedirs(tmp_dir, exist_ok=True)

    # 如果 bag_path 是相对路径（如 gacrnd-ali-collect-a02-j6/...），构造完整 OSS 路径
    oss_url = bag_path
    if not bag_path.startswith("oss://"):
        oss_url = f"oss://{bag_path}"

    logger.info("[%s] Downloading bag from OSS: %s -> %s", task_id, oss_url, tmp_dir)
    _update_task(task_id, message="Downloading bag from OSS...")

    # ossutil cp -r 会将源目录复制到目标目录下，形成嵌套目录
    # 例如：cp -r oss://bucket/a/b/c /tmp/staging/ -> /tmp/staging/c/...
    # 我们需要的是 /tmp/staging/ 下的实际内容
    cmd = ["ossutil64", "cp", "-r", oss_url, tmp_dir]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error("[%s] ossutil download failed: %s", task_id, result.stderr)
        _update_task(task_id, status="failed", message=f"Failed to download bag from OSS: {result.stderr}")
        return

    # 找到下载后的实际 bag 目录（处理 ossutil 创建的嵌套目录）
    bag_name = os.path.basename(bag_path)
    candidates = [
        os.path.join(tmp_dir, bag_name),  # 直接子目录
        os.path.join(tmp_dir, bag_name, bag_name),  # 嵌套子目录（ossutil cp -r 行为）
    ]
    local_bag = None
    for c in candidates:
        if os.path.exists(c):
            local_bag = c
            break

    if not local_bag:
        _update_task(task_id, status="failed", message=f"Downloaded bag not found in {tmp_dir}")
        return

    logger.info("[%s] Bag downloaded to: %s", task_id, local_bag)
    return local_bag


def extract_topic_hevc_stream(
    bag_path: str,
    topic: str,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    fps: Optional[float] = None,
):
    """Extract HEVC frames from a bag topic and remux to fMP4 stream (no disk write, no decode).

    Yields fMP4 chunks suitable for Media Source Extensions (MSE) playback.
    """
    bag_path = _resolve_bag_path(bag_path, "stream")
    if not bag_path:
        raise RuntimeError("Failed to resolve bag path")

    bag_start, bag_end = _get_bag_time_range(bag_path)
    if start_ts is not None and bag_start is not None and start_ts < bag_start:
        start_ts = bag_start
    if end_ts is not None and bag_end is not None and end_ts > bag_end:
        end_ts = bag_end

    config = _load_video_config()
    meta_fps = _get_topic_fps(bag_path, topic)
    if fps is not None and fps > 0:
        input_fps = fps
    elif meta_fps is not None and meta_fps > 0:
        input_fps = meta_fps
    else:
        input_fps = _get_fps_for_topic(topic, config)

    if not _HAS_GSBAG:
        raise RuntimeError("gsbag SDK not available (not installed on this machine)")
    reader = gsbag_reader.GsBagReader(bag_path)
    reader.set_topic_filter([topic])

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

    total = len(hevc_frames)
    if total == 0:
        raise RuntimeError(f"No frames found for topic {topic} in the specified range")

    if skipped_decode_errors > 0:
        logger.info("[stream] Skipped %d decode-error frames", skipped_decode_errors)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-r", str(input_fps),
        "-f", "hevc",
        "-i", "-",
        "-c:v", "copy",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1",
    ]

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def feed_input():
        try:
            for frame in hevc_frames:
                process.stdin.write(frame)
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except Exception:
                pass

    feeder = threading.Thread(target=feed_input)
    feeder.start()

    try:
        while True:
            chunk = process.stdout.read(262144)
            if not chunk:
                break
            yield chunk
    finally:
        feeder.join()
        returncode = process.wait()
        try:
            stderr_data = process.stderr.read().decode("utf-8", errors="ignore")
            if stderr_data:
                logger.warning("[stream] ffmpeg stderr: %s", stderr_data)
            if returncode != 0:
                logger.error("[stream] ffmpeg exited with code %d", returncode)
        except Exception:
            pass


def extract_topic_to_mp4(
    bag_path: str,
    topic: str,
    task_id: str,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    fps: Optional[float] = None,
) -> str:
    """Extract HEVC frames from a bag topic and transcode to H.264 MP4.

    Optimizations:
      - set_topic_filter avoids scanning non-target topics.
      - start_ts/end_ts allow extracting a time slice (nanoseconds).
      - Single-pass bag read: frames are buffered in memory (HEVC payloads only).
      - Frames are piped directly into ffmpeg to skip temp-file I/O.
      - Input framerate from bag metadata (message_freq), fallback to config.

    Time range clamping:
      - If start_ts < bag start → clamp to bag start
      - If end_ts > bag end → clamp to bag end
      - Prevents "No frames found" when SQL result time range exceeds bag range
    """
    # 解析 bag_path（支持 OSS 路径自动下载）
    bag_path = _resolve_bag_path(bag_path, task_id)

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

    # 帧率优先级：外部传入 > bag metadata > video_config.yaml > 默认 10
    config = _load_video_config()
    meta_fps = _get_topic_fps(bag_path, topic)
    if fps is not None and fps > 0:
        input_fps = fps
        fps_source = "request"
    elif meta_fps is not None and meta_fps > 0:
        input_fps = meta_fps
        fps_source = "metadata"
    else:
        input_fps = _get_fps_for_topic(topic, config)
        fps_source = "config_fallback"
    # 输出帧率：配置显式指定 > 跟随 input_fps
    output_fps_cfg = config.get("output_fps")
    output_fps = int(output_fps_cfg if output_fps_cfg is not None else input_fps)
    crf = int(config.get("crf", 23))
    preset = str(config.get("preset", "fast"))
    logger.info("[%s] FPS source=%s input_fps=%.2f output_fps=%d crf=%d preset=%s", task_id, fps_source, input_fps, output_fps, crf, preset)

    try:
        if not _HAS_GSBAG:
            raise RuntimeError("gsbag SDK not available (not installed on this machine)")
        reader = gsbag_reader.GsBagReader(bag_path)
        reader.set_topic_filter([topic])
    except Exception as exc:
        logger.exception("[%s] Failed to open bag", task_id)
        _update_task(task_id, status="failed", message=f"Failed to open bag: {exc}")
        return

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
        _update_task(task_id, status="failed", progress=0.0, message=f"No frames found for topic {topic} in the specified range")
        return

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
        "-r", str(input_fps),   # input framerate (from config)
        "-f", "hevc",
        "-i", "-",                # read from stdin pipe
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-r", str(output_fps),    # output framerate (from config)
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
            return

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

    except Exception as exc:
        # Ensure ffmpeg process is cleaned up on any error
        try:
            process.stdin.close()
        except Exception:
            pass
        process.wait()
        if os.path.exists(mp4_path):
            os.remove(mp4_path)
        logger.exception("[%s] Extraction failed", task_id)
        _update_task(task_id, status="failed", message=f"Extraction failed: {exc}")
        return
