"""
帧提取器 — 从 bag 的 HEVC 流中抽帧为 JPEG 图片

流程：
  1. 通过 gsbag_reader 读取 HEVC 帧
  2. 用 ffmpeg 解码为 JPEG 图片
  3. 按 sample_fps 采样，最多 max_frames 张

用途：VL Embedding 验证闭环 — 从 SQL 查询结果提取视频帧做视觉编码
"""

import os
import sys
import logging
import subprocess
import shutil
import threading
from typing import List, Optional, Dict
from pathlib import Path

import yaml

from app.core.config import settings
from app.core.exceptions import ExtractionFailedException

# 确保项目根目录在 sys.path
PROJECT_ROOT = str(settings.PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
for proto_path in [os.path.join(PROJECT_ROOT, "scripts/proto"),
                    os.path.join(PROJECT_ROOT, "scripts/proto/j6")]:
    if proto_path not in sys.path:
        sys.path.insert(0, proto_path)

# gsbag SDK — 可选依赖
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

# 输出目录：抽帧 JPEG 存放于此
FRAME_OUTPUT_DIR = os.path.join(settings.VIDEO_OUTPUT_DIR, "frames")
os.makedirs(FRAME_OUTPUT_DIR, exist_ok=True)

# ── 批量抽帧任务注册表 ──
_batch_tasks: Dict[str, Dict] = {}
_batch_tasks_lock = threading.Lock()


# ── Bag 路径解析 ──

class BagResolveResult:
    """bag 路径解析结果"""
    def __init__(self, local_path: Optional[str] = None, oss_path: Optional[str] = None):
        self.local_path = local_path  # 本地可访问路径（如果存在）
        self.oss_path = oss_path      # OSS 路径（用于 ossutil 下载回退）


def _resolve_bag_path_via_dm(bag_id: str) -> BagResolveResult:
    """通过 dm_sdk 解析 bag_id → 本地路径 + OSS 路径"""
    try:
        from tools.rosbag_path_resolver import RosbagPathResolver
        resolver = RosbagPathResolver(
            access_token=settings.DM_ACCESS_TOKEN,
            prod_table=settings.DM_PROD_TABLE,
            oss_mount_map=settings.OSS_MOUNT_MAP,
        )
        info = resolver.resolve(bag_id)
        result = BagResolveResult(
            local_path=info.local_path if info.local_path and os.path.exists(info.local_path) else None,
            oss_path=info.oss_path,
        )
        if result.local_path:
            logger.info("Resolved bag_id=%s → local=%s", bag_id, result.local_path)
        elif result.oss_path:
            logger.info("Resolved bag_id=%s → oss=%s (local not mounted)", bag_id, result.oss_path)
        return result
    except Exception as exc:
        logger.error("Failed to resolve bag_id=%s via dm_sdk: %s", bag_id, exc)
        return BagResolveResult()


def _resolve_bag_path_local(bag_id: str) -> BagResolveResult:
    """通过 ROSBAG_MOUNT_BASE 本地查找 bag_id"""
    mount_base = settings.ROSBAG_MOUNT_BASE
    if not mount_base:
        return BagResolveResult()
    candidate = os.path.join(mount_base, bag_id)
    if os.path.exists(candidate):
        return BagResolveResult(local_path=candidate)
    return BagResolveResult()


def _download_bag_from_oss(oss_path: str, task_id: str, bag_id: str) -> Optional[str]:
    """通过 ossutil 下载 camera.bag + metadata.yaml 到临时目录

    只下载视频提取必需的文件，不下载整个 bag 目录。
    返回临时 bag 目录路径（包含 metadata.yaml + camera.bag）

    OSS 目录结构：
      {bag_dir}/metadata.yaml
      {bag_dir}/camera.bag       ← gsbag_reader 根据 metadata.yaml 的 relative_file_paths 定位
      {bag_dir}/default.bag      ← 不需要
      {bag_dir}/lidar.bag        ← 不需要
    """
    tmp_dir = os.path.join(FRAME_OUTPUT_DIR, "_bag_staging", task_id, bag_id.replace("/", "_"))
    os.makedirs(tmp_dir, exist_ok=True)

    # oss_path 格式: oss://bucket/path/to/bag_dir/  (可能带或不带尾部斜杠)
    oss_prefix = oss_path.rstrip("/")
    if not oss_prefix.endswith("/"):
        oss_prefix += "/"

    # 下载 metadata.yaml（小文件，秒下）
    metadata_key = f"{oss_prefix}metadata.yaml"
    meta_dest = os.path.join(tmp_dir, "metadata.yaml")
    if not os.path.exists(meta_dest):
        cmd = ["ossutil64", "cp", metadata_key, meta_dest]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning("[%s] Failed to download metadata.yaml: %s", task_id, result.stderr)

    # 下载 camera.bag（约 1-2GB，较慢）
    # camera.bag 在 OSS 上位于 {bag_dir}/camera.bag（不是子目录）
    camera_bag_key = f"{oss_prefix}camera.bag"
    camera_bag_dest = os.path.join(tmp_dir, "camera.bag")
    if not os.path.exists(camera_bag_dest):
        logger.info("[%s] Downloading camera.bag from OSS: %s → %s", task_id, camera_bag_key, camera_bag_dest)
        cmd = ["ossutil64", "cp", camera_bag_key, camera_bag_dest]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error("[%s] Failed to download camera.bag: %s", task_id, result.stderr)
            return None

    # 验证
    if os.path.exists(camera_bag_dest) and os.path.exists(meta_dest):
        logger.info("[%s] Bag downloaded successfully to %s", task_id, tmp_dir)
        return tmp_dir
    return None


def _get_bag_time_range(bag_path: str):
    """从 metadata.yaml 读取 bag 的起止时间戳（纳秒）"""
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
    """从 metadata.yaml 读取指定 topic 的帧率"""
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


def _find_default_camera_topic(bag_path: str) -> Optional[str]:
    """从 metadata.yaml 中查找默认 camera topic"""
    metadata_path = os.path.join(bag_path, "metadata.yaml")
    if not os.path.exists(metadata_path):
        return None
    # 优先列表：前视宽120°(fw120)优先，前视30°(ft30)次之
    PREFERRED_TOPICS = [
        "/gac/cam/orig_fw120_encoded",
        "/gac/cam/fw120_encoded",
        "/gac/cam/orig_ft30_encoded",
        "/gac/cam/ft30_encoded",
        "/camera/front_wide/compressed",
    ]
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f)
        info = meta.get("gacbag_bagfile_information", {})
        available_topics = []
        for t in info.get("topics_with_message_count", []):
            tm = t.get("topic_metadata", {})
            name = tm.get("name", "")
            if name:
                available_topics.append(name)
        # 优先匹配
        for pref in PREFERRED_TOPICS:
            for avail in available_topics:
                if pref in avail or avail.startswith(pref):
                    return avail
        # fallback: 找任何 camera/cam 相关的 encoded topic
        for avail in available_topics:
            if ("cam" in avail or "camera" in avail) and "encoded" in avail:
                return avail
        return available_topics[0] if available_topics else None
    except Exception:
        return None


# ── 核心：HEVC 帧抽取 + ffmpeg 解码为 JPEG ──

def extract_frames_from_bag(
    bag_path: str,
    topic: str,
    output_dir: str,
    start_ts: Optional[int] = None,
    end_ts: Optional[int] = None,
    sample_fps: float = 1.0,
    max_frames: int = 10,
    task_id: str = "",
) -> List[str]:
    """
    从 bag 中读取 HEVC 帧 → ffmpeg 解码 → 按 sample_fps 采样输出 JPEG

    返回：JPEG 文件路径列表
    """
    if not _HAS_GSBAG:
        raise RuntimeError("gsbag SDK not available")
    if not _HAS_PROTO:
        raise RuntimeError("protobuf module not available")

    # Clamp 时间范围
    bag_start, bag_end = _get_bag_time_range(bag_path)
    if start_ts is not None and bag_start is not None and start_ts < bag_start:
        start_ts = bag_start
    if end_ts is not None and bag_end is not None and end_ts > bag_end:
        end_ts = bag_end

    # 读取 HEVC 帧
    reader = gsbag_reader.GsBagReader(bag_path)
    reader.set_topic_filter([topic])

    hevc_frames: List[bytes] = []
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
        except Exception:
            pass

    total = len(hevc_frames)
    if total == 0:
        logger.warning("[%s] No frames found for topic=%s range=[%s,%s]",
                       task_id, topic, start_ts, end_ts)
        return []

    # 获取帧率以计算采样间隔
    meta_fps = _get_topic_fps(bag_path, topic)
    input_fps = meta_fps if meta_fps and meta_fps > 0 else 10.0

    # 计算采样：每 (input_fps / sample_fps) 帧取一帧
    sample_interval = max(1, int(input_fps / sample_fps))
    sampled_indices = list(range(0, total, sample_interval))
    # 限制最大帧数
    if len(sampled_indices) > max_frames:
        # 均匀采样
        step = len(sampled_indices) / max_frames
        sampled_indices = [sampled_indices[int(i * step)] for i in range(max_frames)]

    logger.info("[%s] Total %d frames, sampling %d at interval=%d (input_fps=%.1f, sample_fps=%.1f)",
                task_id, total, len(sampled_indices), sample_interval, input_fps, sample_fps)

    os.makedirs(output_dir, exist_ok=True)

    # 用 ffmpeg 解码每帧为 JPEG
    # 方案：将所有 HEVC 帧写入管道，ffmpeg 解码输出 JPEG 序列
    # 但由于需要采样，更高效的做法：先全部 pipe 给 ffmpeg 生成所有帧，
    # 然后只保留采样的帧
    jpeg_dir = os.path.join(output_dir, "raw")
    os.makedirs(jpeg_dir, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
        "-r", str(int(input_fps)),
        "-f", "hevc",
        "-i", "-",
        "-c:v", "mjpeg",
        "-q:v", "2",        # 高质量 JPEG (2=best, 31=worst)
        os.path.join(jpeg_dir, "frame_%06d.jpg"),
    ]

    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    try:
        for idx, frame in enumerate(hevc_frames):
            process.stdin.write(frame)
        process.stdin.close()
        returncode = process.wait()
        stderr = process.stderr.read().decode("utf-8", errors="ignore") if process.stderr else ""

        if returncode != 0:
            logger.error("[%s] ffmpeg JPEG decode failed: %s", task_id, stderr[:2000])
            raise RuntimeError(f"ffmpeg JPEG decode failed: {stderr[:500]}")

    except Exception as exc:
        try:
            process.stdin.close()
        except Exception:
            pass
        process.wait()
        raise

    # 收集输出的 JPEG 文件
    all_frames = sorted([
        os.path.join(jpeg_dir, f)
        for f in os.listdir(jpeg_dir)
        if f.endswith(".jpg") or f.endswith(".jpeg")
    ])

    if not all_frames:
        logger.warning("[%s] ffmpeg produced 0 JPEG frames", task_id)
        return []

    # 采样：选择指定索引的帧
    result_paths = []
    for i, sample_idx in enumerate(sampled_indices):
        if sample_idx < len(all_frames):
            src = all_frames[sample_idx]
            dst = os.path.join(output_dir, f"frame_{i:04d}.jpg")
            shutil.copy2(src, dst)
            result_paths.append(dst)

    # 清理临时 raw 目录
    try:
        shutil.rmtree(jpeg_dir)
    except Exception:
        pass

    logger.info("[%s] Extracted %d sampled JPEG frames to %s", task_id, len(result_paths), output_dir)
    return result_paths


# ── 批量任务管理 ──

def create_batch_task(task_id: str, clip_count: int) -> None:
    """初始化批量任务状态"""
    with _batch_tasks_lock:
        _batch_tasks[task_id] = {
            "status": "pending",
            "clips": [None] * clip_count,  # 占位
            "message": "Initialized",
        }


def update_batch_clip(task_id: str, clip_idx: int, result: Dict) -> None:
    """更新批量任务中单条 clip 的结果"""
    with _batch_tasks_lock:
        if task_id in _batch_tasks:
            _batch_tasks[task_id]["clips"][clip_idx] = result
            # 检查是否全部完成
            all_done = all(c is not None and c.get("status") in ("completed", "failed")
                          for c in _batch_tasks[task_id]["clips"])
            if all_done:
                _batch_tasks[task_id]["status"] = "completed"


def update_batch_status(task_id: str, status: str, message: str = "") -> None:
    with _batch_tasks_lock:
        if task_id in _batch_tasks:
            _batch_tasks[task_id]["status"] = status
            if message:
                _batch_tasks[task_id]["message"] = message


def get_batch_task(task_id: str) -> Optional[Dict]:
    with _batch_tasks_lock:
        return _batch_tasks.get(task_id)


def cleanup_batch_task(task_id: str) -> None:
    """清理任务数据（保留帧文件，只清内存状态）"""
    with _batch_tasks_lock:
        _batch_tasks.pop(task_id, None)
