#!/usr/bin/env python3
"""
stream_worker.py — 视频流式提取子进程

被 video.py 通过 subprocess.Popen 启动。
独立进程中执行 gsbag 读帧 + ffmpeg 编码，结果写 stdout。
父进程从 pipe 读取并通过 StreamingResponse 转发给浏览器。

进程隔离的好处：
- 客户端断开后，父进程直接 kill 本子进程
- OS 回收所有资源（fd、mmap、gsbag 全局锁、线程），彻底解决锁不释放导致卡死的问题
- 不依赖 Python 层的 cleanup 逻辑
"""
import os
import sys
import gc
import json
import logging
import queue
import subprocess
import threading
import signal
from typing import Optional
from pathlib import Path

# ── 环境配置（与 video_extractor.py 共用） ──
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
for proto_path in [
    os.path.join(PROJECT_ROOT, "scripts/proto"),
    os.path.join(PROJECT_ROOT, "scripts/proto/j6"),
]:
    if proto_path not in sys.path:
        sys.path.insert(0, proto_path)

# gsbag SDK
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

import yaml

logger = logging.getLogger("stream_worker")
logging.basicConfig(level=logging.INFO, format="[stream_worker] %(message)s")


# ── Video config loader（从 video_config.yaml） ──
_DEFAULT_VIDEO_CONFIG = {
    "input_fps": 10,
    "output_fps": None,
    "crf": 23,
    "preset": "fast",
    "topic_fps_overrides": {},
}

_video_config_cache = None
_video_config_mtime = 0.0


def _load_video_config():
    global _video_config_cache, _video_config_mtime
    config_path = Path(__file__).resolve().parent.parent.parent.parent / "video_config.yaml"
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
    except Exception:
        merged = dict(_DEFAULT_VIDEO_CONFIG)
        _video_config_cache = merged
    return _video_config_cache


def _get_fps_for_topic(topic, config):
    overrides = config.get("topic_fps_overrides", {})
    if topic in overrides:
        return int(overrides[topic])
    for prefix, fps in overrides.items():
        if topic.startswith(prefix):
            return int(fps)
    return int(config.get("input_fps", 10))


def _get_topic_fps(bag_path, topic):
    """从 bag metadata 读取 topic 帧率"""
    metadata_path = os.path.join(bag_path, "metadata.yaml")
    if not os.path.exists(metadata_path):
        return None
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        topics_info = meta.get("topics", {})
        if topic in topics_info:
            freq = topics_info[topic].get("message_freq")
            if freq and freq > 0:
                return int(freq)
    except Exception:
        pass
    return None


def _get_bag_time_range(bag_path):
    metadata_path = os.path.join(bag_path, "metadata.yaml")
    if not os.path.exists(metadata_path):
        return None, None
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f) or {}
        start = meta.get("start_time")
        end = meta.get("end_time")
        return start, end
    except Exception:
        return None, None


def _resolve_bag_path(bag_path):
    """解析 bag 路径，支持 OSS 挂载路径转换"""
    if not bag_path:
        return None
    # OSS 路径转换
    if bag_path.startswith("oss://"):
        mount_map_str = os.getenv("OSS_MOUNT_MAP", "")
        for mapping in mount_map_str.split(","):
            if ":" not in mapping:
                continue
            oss_prefix, local_base = mapping.split(":", 1)
            if bag_path.startswith(oss_prefix):
                relative = bag_path[len(oss_prefix):].lstrip("/")
                local_path = os.path.join(local_base, relative)
                if os.path.exists(local_path):
                    return local_path
    # 直接路径
    if os.path.exists(bag_path):
        return bag_path
    # 挂载点查找
    rosbag_mount = os.getenv("ROSBAG_MOUNT_BASE", "")
    if rosbag_mount and not bag_path.startswith("/"):
        candidate = os.path.join(rosbag_mount, bag_path)
        if os.path.exists(candidate):
            return candidate
    return bag_path


def _clamp_time_range(bag_path, start_ts, end_ts):
    bag_start, bag_end = _get_bag_time_range(bag_path)
    if start_ts is not None and bag_start is not None and start_ts < bag_start:
        start_ts = bag_start
    if end_ts is not None and bag_end is not None and end_ts > bag_end:
        end_ts = bag_end
    return start_ts, end_ts


def _get_fps_config(bag_path, topic, fps):
    config = _load_video_config()
    meta_fps = _get_topic_fps(bag_path, topic)
    if fps is not None and fps > 0:
        input_fps = fps
    elif meta_fps is not None and meta_fps > 0:
        input_fps = meta_fps
    else:
        input_fps = _get_fps_for_topic(topic, config)
    output_fps_cfg = config.get("output_fps")
    output_fps = int(output_fps_cfg if output_fps_cfg is not None else input_fps)
    crf = int(config.get("crf", 23))
    preset = str(config.get("preset", "fast"))
    return input_fps, output_fps, crf, preset


# ── 主流式提取逻辑 ──

def run_stream(mode, bag_path, topic, start_ts, end_ts, fps):
    """
    在子进程中执行 gsbag 读帧 + ffmpeg 编码，fMP4 chunks 写 stdout。

    mode: 'hevc' 或 'h264'
    """
    if not _HAS_GSBAG:
        _write_error_json("gsbag SDK not available")
        sys.exit(1)

    bag_path = _resolve_bag_path(bag_path)
    if not bag_path or not os.path.exists(bag_path):
        _write_error_json(f"Bag path not found: {bag_path}")
        sys.exit(1)

    start_ts, end_ts = _clamp_time_range(bag_path, start_ts, end_ts)
    input_fps, output_fps, crf, preset = _get_fps_config(bag_path, topic, fps)

    # gsbag reader
    reader = gsbag_reader.GsBagReader(bag_path)
    reader.set_topic_filter([topic])

    # ffmpeg 命令
    if mode == "hevc":
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-r", str(input_fps), "-f", "hevc", "-i", "-",
            "-c:v", "copy",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-f", "mp4", "pipe:1",
        ]
    else:  # h264
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-r", str(input_fps), "-f", "hevc", "-i", "-",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-r", str(output_fps),
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-f", "mp4", "pipe:1",
        ]

    # SIGPIPE: 父进程关闭 pipe 后我们不崩溃，正常退出
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    frame_queue = queue.Queue(maxsize=60)

    # 读帧线程
    def feed_input():
        skipped = 0
        total = 0
        try:
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
                        frame_queue.put(image_data[0], timeout=1.0)
                        total += 1
                except (BrokenPipeError, OSError):
                    break
                except Exception as exc:
                    skipped += 1
                    if skipped <= 5:
                        logger.warning("Frame decode error: %s", exc)
        except Exception as exc:
            logger.debug("Feed thread exited: %s", exc)
        finally:
            logger.info("Feed done: %d frames, %d errors", total, skipped)
            try:
                frame_queue.put(None, timeout=1.0)
            except Exception:
                pass

    # 写 ffmpeg stdin 线程
    def write_input():
        written = 0
        try:
            while True:
                frame = frame_queue.get()
                if frame is None:
                    break
                process.stdin.write(frame)
                written += 1
        except (BrokenPipeError, OSError):
            pass
        finally:
            logger.info("Writer done: %d frames", written)
            try:
                process.stdin.close()
            except Exception:
                pass

    feeder = threading.Thread(target=feed_input)
    writer = threading.Thread(target=write_input)
    feeder.start()
    writer.start()

    # 主循环：从 ffmpeg stdout 读取 fMP4 chunks，写自身 stdout → 父进程读取
    try:
        while True:
            chunk = process.stdout.read(262144)
            if not chunk:
                break
            # 写到 stdout（父进程的 pipe 端）
            os.write(sys.stdout.fileno(), chunk)
    except (BrokenPipeError, OSError):
        # 父进程已关闭读取端（客户端断开），正常退出
        pass
    finally:
        # 清理：kill ffmpeg，关闭 reader
        logger.info("Stream ended, cleaning up...")
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait(timeout=2)
        except Exception:
            pass

        # 等线程结束（最多3秒）
        feeder.join(timeout=3)
        writer.join(timeout=3)

        # 关闭 reader（释放 gsbag 锁）
        try:
            if hasattr(reader, 'close'):
                reader.close()
        except Exception:
            pass
        del reader
        gc.collect()

        # 读 ffmpeg stderr（诊断用）
        try:
            import selectors as _sel
            _err_sel = _sel.DefaultSelector()
            _err_sel.register(process.stderr, _sel.EVENT_READ)
            ready = _err_sel.select(timeout=1)
            if ready:
                stderr_data = process.stderr.read(65536)
                _err_sel.close()
                stderr_text = stderr_data.decode("utf-8", errors="ignore")
                if stderr_text:
                    logger.warning("ffmpeg stderr: %s", stderr_text[:2000])
        except Exception:
            pass

        logger.info("Worker process exiting cleanly")


def _write_error_json(msg):
    """向 stderr 写入 JSON 错误信息，父进程可解析"""
    try:
        err = json.dumps({"error": msg})
        sys.stderr.write(err + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def main():
    """
    命令行入口。参数通过命令行传入：

    python stream_worker.py --mode hevc --bag-path /path/to/bag --topic /cam/ft30 \\
        [--start-ts 123] [--end-ts 456] [--fps 10]
    """
    import argparse
    parser = argparse.ArgumentParser(description="Stream worker subprocess")
    parser.add_argument("--mode", choices=["hevc", "h264"], required=True)
    parser.add_argument("--bag-path", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--start-ts", type=int, default=None)
    parser.add_argument("--end-ts", type=int, default=None)
    parser.add_argument("--fps", type=float, default=None)
    args = parser.parse_args()

    run_stream(args.mode, args.bag_path, args.topic, args.start_ts, args.end_ts, args.fps)


if __name__ == "__main__":
    main()
