#!/usr/bin/env python3
"""
stream_worker.py — 视频流式提取子进程

被 video.py 通过 subprocess.Popen 启动。
独立进程中执行 gsbag 读帧 + ffmpeg 编码，fMP4 chunks 写 stdout。
父进程从 pipe 读取并通过 StreamingResponse 转发给浏览器。

进程隔离的好处：
- 客户端断开后，父进程直接 kill 本子进程
- OS 回收所有资源（fd、mmap、gsbag 全局锁、线程），彻底解决锁不释放导致卡死的问题
- 不依赖 Python 层的 cleanup 逻辑

⚠ 关键设计：
- gsbag C 层在 import 时会向 fd 1 (stdout) 写 "init gsbag_reader_wrapper\n"
  这会污染 fMP4 二进制流。解决方案：import 前将 fd 1 重定向到 stderr，
  import 完成后恢复。这样 C 层的 init 消息走 stderr，不影响 stdout 上的 fMP4 数据。
- ffmpeg 的 stderr 必须被持续消耗（drain），否则缓冲区满后 ffmpeg 阻塞，
  导致整个 pipeline 死锁。本脚本用守护线程 drain ffmpeg stderr。
- 子进程继承父进程（uvicorn）的 LD_LIBRARY_PATH 等环境变量，
  确保 gsbag C .so 依赖链能被正确加载。
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

# ── 环境配置 ──
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
for proto_path in [
    os.path.join(PROJECT_ROOT, "scripts/proto"),
    os.path.join(PROJECT_ROOT, "scripts/proto/j6"),
]:
    if proto_path not in sys.path:
        sys.path.insert(0, proto_path)

# ── gsbag SDK 导入（避免 C 层 stdout 污染） ──
# gsbag C 层 import 时会向 stdout (fd 1) 写 "init gsbag_reader_wrapper\n"
# 这会污染后续的 fMP4 二进制流，所以 import 前先把 fd 1 重定向到 stderr
_stdout_fd = os.dup(1)  # 备份真实 stdout fd
os.dup2(2, 1)           # fd 1 → stderr（C 层的 init 消息走 stderr）

try:
    from gsbag import gsbag_reader
    _HAS_GSBAG = True
except ImportError:
    gsbag_reader = None
    _HAS_GSBAG = False

# 恢复真实 stdout
os.dup2(_stdout_fd, 1)
os.close(_stdout_fd)

try:
    from j6.image_encode import boleidl_pb2 as image_encode_boleidl_pb2
    _HAS_PROTO = True
except ImportError:
    image_encode_boleidl_pb2 = None
    _HAS_PROTO = False

import yaml

logger = logging.getLogger("stream_worker")
logging.basicConfig(level=logging.INFO, format="[stream_worker] %(message)s",
                    stream=sys.stderr)  # 日志走 stderr，不污染 stdout


# ── 配置加载 ──

def _load_video_config():
    """加载 video_config.yaml"""
    config_paths = [
        os.path.join(os.path.dirname(__file__), "..", "config", "video_config.yaml"),
        os.path.join(PROJECT_ROOT, "visualizer/backend/config/video_config.yaml"),
    ]
    for p in config_paths:
        if os.path.exists(p):
            with open(p) as f:
                return yaml.safe_load(f) or {}
    return {}


def _get_bag_time_range(bag_path):
    """读取 metadata.yaml 获取 bag 时间范围"""
    metadata_path = os.path.join(bag_path, "metadata.yaml")
    if not os.path.exists(metadata_path):
        return None, None
    try:
        with open(metadata_path) as f:
            meta = yaml.safe_load(f)
        start = meta.get("starting_time", {}).get("nanoseconds_since_epoch")
        end = meta.get("ending_time", {}).get("nanoseconds_since_epoch")
        return start, end
    except Exception:
        return None, None


def _get_topic_fps(bag_path, topic):
    """从 metadata.yaml 获取指定 topic 的帧率"""
    metadata_path = os.path.join(bag_path, "metadata.yaml")
    if not os.path.exists(metadata_path):
        return None
    try:
        with open(metadata_path) as f:
            meta = yaml.safe_load(f)
        for tinfo in meta.get("topics_with_message_count", []):
            if tinfo.get("topic_metadata", {}).get("name") == topic:
                msg_count = tinfo.get("message_count", 0)
                start = meta.get("starting_time", {}).get("nanoseconds_since_epoch")
                end = meta.get("ending_time", {}).get("nanoseconds_since_epoch")
                if msg_count > 0 and start and end and end > start:
                    duration_s = (end - start) / 1e9
                    return round(msg_count / duration_s, 2)
        return None
    except Exception:
        return None


def _get_fps_for_topic(topic, config):
    """从配置获取 topic 对应的帧率"""
    topic_fps = config.get("topic_fps", {})
    if topic in topic_fps:
        return topic_fps[topic]
    for pattern, fps in topic_fps.items():
        if pattern.replace("*", "") in topic:
            return fps
    return 10.0  # 默认


def _resolve_bag_path(bag_path):
    """
    解析 bag 路径：支持绝对路径、OSS mount map 映射、ROSBAG_MOUNT_BASE 前缀。
    复用 video_extractor.py 的完整逻辑。
    """
    # 已经是本地绝对路径且存在
    if os.path.isabs(bag_path) and os.path.exists(bag_path):
        return bag_path

    # 尝试通过 OSS_MOUNT_MAP 解析
    mount_map_str = os.environ.get("OSS_MOUNT_MAP", "")
    if mount_map_str:
        try:
            from tools.rosbag_path_resolver import _parse_oss_mount_map, _oss_to_local
            mount_map = _parse_oss_mount_map(mount_map_str)
            local = _oss_to_local(bag_path, mount_map)
            if local and os.path.exists(local):
                return local
        except ImportError:
            pass

    # 尝试 ROSBAG_MOUNT_BASE 前缀
    rosbag_mount = os.environ.get("ROSBAG_MOUNT_BASE", "/mnt")
    if rosbag_mount and not bag_path.startswith("/"):
        candidate = os.path.join(rosbag_mount, bag_path)
        if os.path.exists(candidate):
            return candidate

    # 原样返回（后续 os.path.exists 检查会捕获）
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


# ── ffmpeg stderr drain 线程 ──

def _drain_stderr(proc_stderr, log_prefix="ffmpeg"):
    """
    持续读取 ffmpeg stderr 并输出到 worker 的 stderr（诊断日志）。
    不消耗会导致 ffmpeg 阻塞死锁。
    """
    try:
        while True:
            line = proc_stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore").rstrip()
            if text:
                logger.info("[%s] %s", log_prefix, text)
    except Exception:
        pass


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

    # SIGPIPE: 父进程关闭 pipe 后不崩溃，正常退出
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,   # ffmpeg 的 fMP4 输出 → 本 worker 读取
        stderr=subprocess.PIPE,   # ffmpeg 诊断日志 → drain 线程消耗
    )

    # 启动 stderr drain 线程（防止 ffmpeg stderr 缓冲区满导致死锁）
    drain_thread = threading.Thread(
        target=_drain_stderr, args=(process.stderr, "ffmpeg"), daemon=True
    )
    drain_thread.start()

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
            # 直接写 fd 1（stdout），父进程的 pipe 端读取
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

        # 等 drain 线程结束
        drain_thread.join(timeout=2)

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
