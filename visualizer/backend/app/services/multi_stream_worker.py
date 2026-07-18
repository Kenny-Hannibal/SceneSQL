#!/usr/bin/env python3
"""
multi_stream_worker.py — 多topic共享Reader流式提取子进程

架构：
  1个gsbag_reader读bag，按topic_name路由到N个ffmpeg进程。
  所有ffmpeg的fMP4输出复用到stdout，协议：
    [topic_idx:1byte][data_len:4bytes][fMP4_data:data_len bytes]

前端demux：读取5字节header，按topic_idx分发到对应SourceBuffer。

与 stream_worker.py 的区别：
  - stream_worker: 1 topic → 1 gsbag_reader → 1 ffmpeg（单topic，N个进程=N×bag读取）
  - multi_stream_worker: N topics → 1 gsbag_reader → N ffmpeg（共享读取，1×bag读取）
"""

import os
import sys
import gc
import struct
import logging
import subprocess
import threading
import signal
import queue
import yaml
from pathlib import Path
from typing import List, Dict, Optional

# ── 环境配置 ──
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
for proto_path in [
    os.path.join(PROJECT_ROOT, 'scripts/proto'),
    os.path.join(PROJECT_ROOT, 'scripts/proto/j6'),
]:
    if proto_path not in sys.path:
        sys.path.insert(0, proto_path)

# ── gsbag SDK 导入（避免 C 层 stdout 污染） ──
_stdout_fd = os.dup(1)
os.dup2(2, 1)  # fd 1 → stderr

try:
    from gsbag import gsbag_reader
    _HAS_GSBAG = True
except ImportError:
    gsbag_reader = None
    _HAS_GSBAG = False

os.dup2(_stdout_fd, 1)
os.close(_stdout_fd)

try:
    from j6.image_encode import boleidl_pb2 as image_encode_boleidl_pb2
    _HAS_PROTO = True
except ImportError:
    image_encode_boleidl_pb2 = None
    _HAS_PROTO = False

logger = logging.getLogger('multi_stream_worker')
logging.basicConfig(level=logging.INFO, format='[multi_stream_worker] %(message)s',
                    stream=sys.stderr)


# ── 配置加载（复用 stream_worker 的逻辑） ──

def _load_video_config():
    config_paths = [
        os.path.join(os.path.dirname(__file__), '..', 'config', 'video_config.yaml'),
        os.path.join(PROJECT_ROOT, 'visualizer/backend/config/video_config.yaml'),
    ]
    for p in config_paths:
        if os.path.exists(p):
            with open(p) as f:
                return yaml.safe_load(f) or {}
    return {}


def _get_topic_fps(bag_path, topic):
    metadata_path = os.path.join(bag_path, 'metadata.yaml')
    if not os.path.exists(metadata_path):
        return None
    try:
        with open(metadata_path) as f:
            meta = yaml.safe_load(f)
        info = meta.get('gacbag_bagfile_information', meta)
        for tinfo in info.get('topics_with_message_count', []):
            tm = tinfo.get('topic_metadata', {})
            if tm.get('name') == topic:
                freq = tinfo.get('message_freq')
                if freq and freq > 0:
                    return float(freq)
                msg_count = tinfo.get('message_count', 0)
                start = info.get('starting_time', {}).get('nanoseconds_since_epoch')
                end = info.get('ending_time', {}).get('nanoseconds_since_epoch')
                if msg_count > 0 and start and end and end > start:
                    duration_s = (end - start) / 1e9
                    return round(msg_count / duration_s, 2)
        return None
    except Exception:
        return None


def _get_fps_for_topic(topic, config):
    topic_fps = config.get('topic_fps', {})
    if topic in topic_fps:
        return topic_fps[topic]
    for pattern, fps in topic_fps.items():
        if pattern.replace('*', '') in topic:
            return fps
    return 10.0


def _resolve_bag_path(bag_path):
    if os.path.isabs(bag_path) and os.path.exists(bag_path):
        return bag_path
    mount_map_str = os.environ.get('OSS_MOUNT_MAP', '')
    if mount_map_str:
        try:
            from tools.rosbag_path_resolver import _parse_oss_mount_map, _oss_to_local
            mount_map = _parse_oss_mount_map(mount_map_str)
            local = _oss_to_local(bag_path, mount_map)
            if local and os.path.exists(local):
                return local
        except ImportError:
            pass
    return bag_path


def _find_bag_file(bag_path):
    """查找 bag.bag 文件"""
    if os.path.isfile(bag_path):
        return bag_path
    direct = os.path.join(bag_path, 'bag.bag')
    if os.path.isfile(direct):
        return direct
    if os.path.isdir(bag_path):
        for entry in sorted(os.listdir(bag_path)):
            sub = os.path.join(bag_path, entry)
            if os.path.isdir(sub):
                candidate = os.path.join(sub, 'bag.bag')
                if os.path.isfile(candidate):
                    return candidate
    return None


# ── ffmpeg stderr drain ──

def _drain_stderr(proc_stderr, log_prefix):
    try:
        while True:
            line = proc_stderr.readline()
            if not line:
                break
            text = line.decode('utf-8', errors='ignore').rstrip()
            if text:
                logger.info('[%s] %s', log_prefix, text)
    except Exception:
        pass


# ── 复用协议：写入 stdout ──
# [topic_idx:1byte][data_len:4bytes(Little-Endian)][data:data_len bytes]

def _write_muxed(stdout_fd, topic_idx, data):
    """写入一条复用帧到 stdout"""
    header = struct.pack('<BI', topic_idx, len(data))
    os.write(stdout_fd, header + data)


# ── 主流式提取逻辑 ──

def run_multi_stream(bag_path, topics, mode, start_ts, end_ts, fps):
    """
    多topic共享Reader流式提取。

    Args:
        bag_path: bag目录路径（含 bag.bag）
        topics: list of topic names
        mode: 'hevc' or 'h264'
        start_ts, end_ts: 时间范围过滤（纳秒）
        fps: 帧率覆盖
    """
    if not _HAS_GSBAG:
        _write_error('gsbag SDK not available')
        sys.exit(1)
    if not _HAS_PROTO:
        _write_error('protobuf module not available')
        sys.exit(1)

    bag_path = _resolve_bag_path(bag_path)
    bag_file = _find_bag_file(bag_path)
    if not bag_file or not os.path.exists(bag_file):
        _write_error(f'Bag file not found: {bag_path}')
        sys.exit(1)

    n_topics = len(topics)
    topic_to_idx = {t: i for i, t in enumerate(topics)}
    logger.info('Starting multi-stream: %d topics, mode=%s, bag=%s', n_topics, mode, bag_file)

    # 1. gsbag reader — 过滤全部需要的 topic
    reader = gsbag_reader.GsBagReader(bag_file)
    reader.set_topic_filter(topics)

    # 2. 每个topic的FPS配置
    config = _load_video_config()
    topic_fps_map = {}
    for topic in topics:
        meta_fps = _get_topic_fps(bag_path, topic)
        if fps is not None and fps > 0:
            topic_fps_map[topic] = fps
        elif meta_fps is not None and meta_fps > 0:
            topic_fps_map[topic] = meta_fps
        else:
            topic_fps_map[topic] = _get_fps_for_topic(topic, config)

    # 3. 创建 N 个 ffmpeg 进程
    ffmpeg_procs = []
    ffmpeg_stdins = []
    for i, topic in enumerate(topics):
        input_fps = topic_fps_map.get(topic, 10.0)

        if mode == 'hevc':
            cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-r', str(int(input_fps)), '-f', 'hevc', '-i', '-',
                '-c:v', 'copy',
                '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
                '-f', 'mp4', 'pipe:1',
            ]
        else:  # h264
            output_fps = int(input_fps)
            crf = int(config.get('crf', 23))
            preset = str(config.get('preset', 'fast'))
            keyint = int(input_fps * 2)
            cmd = [
                'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
                '-r', str(int(input_fps)), '-f', 'hevc', '-i', '-',
                '-c:v', 'libx264', '-preset', preset, '-crf', str(crf),
                '-tune', 'zerolatency',
                '-bf', '0',
                '-g', str(keyint), '-keyint_min', str(keyint),
                '-pix_fmt', 'yuv420p', '-r', str(output_fps),
                '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
                '-f', 'mp4', 'pipe:1',
            ]

        logger.info('Topic[%d] %s: fps=%.1f, cmd=%s', i, topic, input_fps, ' '.join(cmd[:8]))
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,      # 从本进程接收 HEVC 帧
            stdout=subprocess.PIPE,      # fMP4 输出 → 本进程读取并复用
            stderr=subprocess.PIPE,      # 诊断日志 → drain 线程消耗
        )
        ffmpeg_procs.append(proc)
        ffmpeg_stdins.append(proc.stdin)

        # 启动 stderr drain 线程
        drain = threading.Thread(
            target=_drain_stderr,
            args=(proc.stderr, f'ffmpeg-{topic.split("/")[-1]}'),
            daemon=True,
        )
        drain.start()

    # 4. 读帧线程：gsbag → 按 topic 分发到对应 ffmpeg.stdin
    feed_error = [None]  # 用 list 包装以便线程间传递

    def feed_worker():
        skipped = [0]
        total = [0]
        try:
            for m in reader.read_messages():
                ts = m.timestamp
                if start_ts is not None and ts < start_ts:
                    continue
                if end_ts is not None and ts > end_ts:
                    continue

                topic_name = m.topic_name
                idx = topic_to_idx.get(topic_name)
                if idx is None:
                    continue

                try:
                    msg = image_encode_boleidl_pb2.Image()
                    image_data = []
                    gsbag_reader.HobotMessageSerializer.deserialize_image(m, msg, image_data)
                    if image_data:
                        ffmpeg_stdins[idx].write(image_data[0])
                        total[0] += 1
                except (BrokenPipeError, OSError):
                    break
                except Exception as exc:
                    skipped[0] += 1
                    if skipped[0] <= 5:
                        logger.warning('Frame decode error (topic=%s): %s', topic_name, exc)
        except Exception as exc:
            feed_error[0] = exc
            logger.error('Feed worker error: %s', exc)
        finally:
            logger.info('Feed done: %d frames, %d errors', total[0], skipped[0])
            # 关闭所有 ffmpeg stdin，让 ffmpeg 自行 flush 并退出
            for i, stdin in enumerate(ffmpeg_stdins):
                try:
                    stdin.close()
                except Exception:
                    pass

    # 5. 输出读取线程：每个 ffmpeg.stdout → 复用写入本进程 stdout
    stdout_fd = sys.stdout.fileno()
    output_threads = []

    def output_reader(idx, proc):
        topic_short = topics[idx].split('/')[-1][:20]
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                _write_muxed(stdout_fd, idx, chunk)
        except (BrokenPipeError, OSError):
            pass
        except Exception as exc:
            logger.error('Output reader error (topic=%s): %s', topic_short, exc)
        finally:
            logger.info('Output reader done for topic[%d] %s', idx, topic_short)

    for i, proc in enumerate(ffmpeg_procs):
        t = threading.Thread(target=output_reader, args=(i, proc))
        t.start()
        output_threads.append(t)

    # SIGPIPE: 父进程关闭 pipe 后不崩溃
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

    # 启动读帧线程
    feed_thread = threading.Thread(target=feed_worker)
    feed_thread.start()

    # 等待所有线程结束
    feed_thread.join()
    for t in output_threads:
        t.join(timeout=30)

    # 等待 ffmpeg 进程退出
    for i, proc in enumerate(ffmpeg_procs):
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning('ffmpeg[%d] did not exit, killing', i)
            proc.kill()
            proc.wait(timeout=5)

    # 关闭 reader
    try:
        if hasattr(reader, 'close'):
            reader.close()
    except Exception:
        pass
    del reader
    gc.collect()

    logger.info('Multi-stream worker exiting cleanly')


def _write_error(msg):
    try:
        import json
        err = json.dumps({'error': msg})
        sys.stderr.write(err + '\n')
        sys.stderr.flush()
    except Exception:
        pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Multi-topic stream worker subprocess')
    parser.add_argument('--bag-path', required=True)
    parser.add_argument('--topics', required=True, help='Comma-separated topic names')
    parser.add_argument('--mode', choices=['hevc', 'h264'], default='hevc')
    parser.add_argument('--start-ts', type=int, default=None)
    parser.add_argument('--end-ts', type=int, default=None)
    parser.add_argument('--fps', type=float, default=None)
    args = parser.parse_args()

    topics = [t.strip() for t in args.topics.split(',') if t.strip()]
    if not topics:
        _write_error('No topics specified')
        sys.exit(1)

    run_multi_stream(args.bag_path, topics, args.mode, args.start_ts, args.end_ts, args.fps)


if __name__ == '__main__':
    main()
