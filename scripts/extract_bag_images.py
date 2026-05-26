#!/usr/bin/env python3
"""
从 SQL 查询结果提取 Rosbag 图片帧

流程：
  bag_id (回灌后的 DB 文件名)
    → dm_sdk 查询原始 bag 信息
    → OSS 路径 → 本地挂载路径
    → gsbag 解析指定时间范围内的 HEVC 帧
    → 解码为 JPEG 图片保存到输出目录

用法:
  # 单条提取
  python extract_bag_images.py --bag_id <id> --start_ts <ts> --end_ts <ts>

  # 批量 CSV 提取
  python extract_bag_images.py --csv tasks.csv

  CSV 格式: bag_id,start_ts,end_ts  (首行可以是表头，自动跳过)
  start_ts/end_ts 支持秒或纳秒，自动识别。

注意:
  本脚本依赖 gsbag SDK，必须通过 shell 包装脚本启动（设置 LD_LIBRARY_PATH 等环境变量）。
  请使用: bash scripts/extract_images.sh --bag_id xxx --start_ts xxx --end_ts xxx
"""

import os
import sys
import csv
import argparse
import logging
import subprocess
from io import BytesIO
from typing import List, Optional, Dict

# ─── 日志 ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─── 环境变量加载 ──────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    for _p in [
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join(os.path.dirname(__file__), ".env"),
        ".env",
    ]:
        if os.path.exists(_p):
            load_dotenv(_p)
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════════════
#  1. OSS 路径映射
# ═══════════════════════════════════════════════════════════════════════

def parse_oss_mount_map(mount_map_str: Optional[str]) -> Dict[str, str]:
    result = {}
    if not mount_map_str:
        return result
    for pair in mount_map_str.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        oss_prefix, local_path = pair.split(":", 1)
        result[oss_prefix.strip()] = local_path.strip()
    return result


def oss_to_local(oss_path: str, mount_map: Dict[str, str]) -> Optional[str]:
    if not oss_path:
        return None
    oss_path = oss_path.rstrip("/")
    if oss_path.startswith("oss://"):
        oss_path = oss_path[6:]
    for oss_prefix, local_prefix in sorted(mount_map.items(), key=lambda x: -len(x[0])):
        if oss_path.startswith(oss_prefix):
            relative = oss_path[len(oss_prefix):]
            if relative.startswith("/"):
                relative = relative[1:]
            local = os.path.join(local_prefix, relative)
            return os.path.normpath(local)
    return os.path.normpath(oss_path)


# ═══════════════════════════════════════════════════════════════════════
#  2. dm_sdk 路径解析
# ═══════════════════════════════════════════════════════════════════════

class RosbagPathResolver:
    def __init__(
        self,
        access_token: Optional[str] = None,
        prod_table: str = "ubm_vehicle_module_bin",
        oss_mount_map: Optional[str] = None,
    ):
        self.access_token = access_token or os.getenv("DM_ACCESS_TOKEN", "")
        self.prod_table = prod_table or os.getenv("DM_PROD_TABLE", "ubm_vehicle_module_bin")
        self.mount_map = parse_oss_mount_map(oss_mount_map or os.getenv("OSS_MOUNT_MAP", ""))
        try:
            from dm_sdk import ProdDataClient, RawDataClient
            self._ProdDataClient = ProdDataClient
            self._RawDataClient = RawDataClient
        except ImportError as exc:
            raise ImportError("dm_sdk 未安装") from exc

    def resolve(self, data_id: str) -> Dict:
        prod_client = self._ProdDataClient(
            access_token=self.access_token,
            table=self.prod_table,
        )
        resp = prod_client.get_bag_metadata(data_id=data_id)
        if resp.resp_code() != 200:
            raise RuntimeError(f"ProdData query failed: {resp.msg}")
        prod_data = resp.resp_data()
        if not prod_data:
            raise ValueError(f"Bag {data_id} not found in {self.prod_table}")
        origins = prod_data.get("origins", [])
        if not origins:
            raise ValueError(f"Bag {data_id} has no origins info")
        origin = origins[0]
        origin_table = origin.get("table")
        origin_bag_id = origin.get("bag_id")
        if not origin_table or not origin_bag_id:
            raise ValueError(f"Origins info incomplete for {data_id}")
        raw_client = self._RawDataClient(
            access_token=self.access_token,
            table=origin_table,
        )
        raw_resp = raw_client.get_bag_metadata(bag_id=origin_bag_id)
        if raw_resp.resp_code() != 200:
            raise RuntimeError(f"RawData query failed: {raw_resp.msg}")
        raw_data = raw_resp.resp_data() or {}
        oss_path = raw_data.get("storage_prefix") or raw_data.get("raw_storage_prefix")
        local_path = oss_to_local(oss_path, self.mount_map) if oss_path else None
        return {
            "data_id": data_id,
            "origin_table": origin_table,
            "origin_bag_id": origin_bag_id,
            "oss_path": oss_path,
            "local_path": local_path,
            "bag_name": raw_data.get("bag_name"),
            "vin": raw_data.get("vin"),
            "vehicle_model": raw_data.get("vehicle_model"),
        }


def resolve_bag_path(bag_id, access_token=None, oss_mount_map=None):
    resolver = RosbagPathResolver(access_token=access_token, oss_mount_map=oss_mount_map)
    info = resolver.resolve(bag_id)
    local_path = info["local_path"]
    if not local_path:
        raise ValueError(f"无法解析 bag_id={bag_id} 的本地路径 (OSS={info['oss_path']})")
    if not os.path.exists(local_path):
        logger.warning("本地路径不存在: %s，尝试 ossutil 下载...", local_path)
        local_path = _download_bag_from_oss(info["oss_path"], bag_id)
    return local_path, info


def _download_bag_from_oss(oss_path, bag_id):
    tmp_dir = f"/tmp/bag_staging/{bag_id}"
    os.makedirs(tmp_dir, exist_ok=True)
    oss_url = oss_path if oss_path.startswith("oss://") else f"oss://{oss_path}"
    logger.info("正在从 OSS 下载 bag: %s -> %s", oss_url, tmp_dir)
    result = subprocess.run(["ossutil64", "cp", "-r", oss_url, tmp_dir],
                            capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ossutil 下载失败: {result.stderr}")
    bag_name = os.path.basename(oss_path)
    for c in [os.path.join(tmp_dir, bag_name), os.path.join(tmp_dir, bag_name, bag_name)]:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(f"下载后找不到 bag 目录: {tmp_dir}")


# ═══════════════════════════════════════════════════════════════════════
#  3. metadata.yaml 读取
# ═══════════════════════════════════════════════════════════════════════

def get_bag_time_range(bag_path):
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
        start_time_ns = end_time_ns = None
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


# ═══════════════════════════════════════════════════════════════════════
#  4. 图片帧提取核心逻辑
# ═══════════════════════════════════════════════════════════════════════

CAMERA_TOPIC_MAP = {
    "fw120": "/gac/cam/fw120_encoded",
    "fw60":  "/gac/cam/fw60_encoded",
    "ft30":  "/gac/cam/ft30_encoded",
    "ft20":  "/gac/cam/ft20_encoded",
    "r50":   "/gac/cam/r50_encoded",
    "fl99":  "/gac/cam/fl99_encoded",
    "fr99":  "/gac/cam/fr99_encoded",
    "rl99":  "/gac/cam/rl99_encoded",
    "rr99":  "/gac/cam/rr99_encoded",
}

DEFAULT_TOPICS = [
    "/gac/cam/fw120_encoded",
    "/gac/cam/fw60_encoded",
    "/gac/cam/r50_encoded",
]


def extract_images_from_bag(
    bag_path: str,
    topics: List[str],
    start_ts: Optional[int],
    end_ts: Optional[int],
    output_dir: str,
    image_format: str = "jpeg",
) -> Dict[str, int]:
    from gsbag import gsbag_reader
    from j6.image_encode import boleidl_pb2 as image_encode_boleidl_pb2

    # Clamp 时间范围
    bag_start, bag_end = get_bag_time_range(bag_path)
    if start_ts is not None and bag_start is not None and start_ts < bag_start:
        logger.info("start_ts %d < bag_start %d, 钳位到 bag_start", start_ts, bag_start)
        start_ts = bag_start
    if end_ts is not None and bag_end is not None and end_ts > bag_end:
        logger.info("end_ts %d > bag_end %d, 钳位到 bag_end", end_ts, bag_end)
        end_ts = bag_end

    result_counts = {}

    for topic in topics:
        topic_dir_name = topic.strip("/").replace("/", "_")
        topic_output_dir = os.path.join(output_dir, topic_dir_name)
        os.makedirs(topic_output_dir, exist_ok=True)

        logger.info("正在提取 topic=%s, 范围=[%s, %s]", topic, start_ts, end_ts)

        try:
            reader = gsbag_reader.GsBagReader(bag_path)
            reader.set_topic_filter([topic])
        except Exception as exc:
            logger.error("无法打开 bag: %s", exc)
            result_counts[topic] = 0
            continue

        hevc_frames = []
        skipped_errors = 0

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
                    hevc_frames.append((ts, image_data[0]))
            except Exception as exc:
                skipped_errors += 1
                if skipped_errors <= 5:
                    logger.warning("帧解码错误: %s", exc)

        if skipped_errors > 5:
            logger.info("跳过 %d 个解码错误帧 (仅显示前5个)", skipped_errors)

        total = len(hevc_frames)
        logger.info("topic=%s: 共 %d 帧", topic, total)

        if total == 0:
            result_counts[topic] = 0
            continue

        saved = 0
        for idx, (ts, hevc_payload) in enumerate(hevc_frames):
            try:
                img = _decode_hevc_frame(hevc_payload)
                if img is None:
                    continue
                ext = "jpg" if image_format == "jpeg" else "png"
                filepath = os.path.join(topic_output_dir, f"{ts}.{ext}")
                img.save(filepath, image_format.upper())
                saved += 1
                if (idx + 1) % 50 == 0:
                    logger.info("  [%s] 已保存 %d/%d 帧", topic_dir_name, saved, total)
            except Exception as exc:
                if saved == 0:
                    logger.warning("帧解码失败: %s", exc)

        result_counts[topic] = saved
        logger.info("topic=%s: 保存 %d 帧到 %s", topic, saved, topic_output_dir)

    return result_counts


def _decode_hevc_frame(hevc_bytes: bytes):
    """使用 PyAV 将 HEVC 帧解码为 PIL Image。"""
    try:
        import av
        container = av.open(BytesIO(hevc_bytes), mode="r", format="hevc")
        for packet in container.demux(video=0):
            for frame in packet.decode():
                return frame.to_image()
        container.close()
    except ImportError:
        return _decode_hevc_frame_ffmpeg(hevc_bytes)
    except Exception:
        try:
            import av
            buf = BytesIO(hevc_bytes)
            container = av.open(buf, mode="r", format="hevc")
            frames = []
            for packet in container.demux(video=0):
                for frame in packet.decode():
                    frames.append(frame.to_image())
            container.close()
            return frames[0] if frames else None
        except Exception:
            return None
    return None


def _decode_hevc_frame_ffmpeg(hevc_bytes: bytes):
    try:
        from PIL import Image
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-f", "hevc", "-i", "-",
               "-f", "image2pipe", "-vcodec", "png", "-"]
        proc = subprocess.run(cmd, input=hevc_bytes, capture_output=True, timeout=10)
        if proc.returncode == 0 and proc.stdout:
            return Image.open(BytesIO(proc.stdout))
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════
#  5. 单条提取任务
# ═══════════════════════════════════════════════════════════════════════

def parse_time_arg(val: str) -> int:
    """自动识别秒/纳秒：值 < 10^10 视为秒，自动转纳秒。"""
    num = int(val)
    if num < 10_000_000_000:
        logger.info("时间戳 %d 识别为【秒】，自动转为纳秒: %d", num, num * 1_000_000_000)
        return num * 1_000_000_000
    return num


def run_single_task(
    bag_id: Optional[str],
    bag_path: Optional[str],
    start_ts: str,
    end_ts: str,
    topics: Optional[List[str]],
    cam_names: Optional[List[str]],
    output_dir: Optional[str],
    image_format: str,
    access_token: Optional[str],
    oss_mount_map: Optional[str],
) -> bool:
    """执行单条提取任务，返回是否成功。"""
    start_ns = parse_time_arg(start_ts)
    end_ns = parse_time_arg(end_ts)

    # 解析 topic 列表
    _topics = topics
    if not _topics and cam_names:
        _topics = [CAMERA_TOPIC_MAP[n] for n in cam_names if n in CAMERA_TOPIC_MAP]
        unknown = [n for n in cam_names if n not in CAMERA_TOPIC_MAP]
        if unknown:
            logger.warning("未知的摄像头名: %s", unknown)
    if not _topics:
        _topics = DEFAULT_TOPICS
        logger.info("使用默认 topic: %s", _topics)

    # 解析 bag 路径
    if bag_path:
        _bag_path = bag_path
        bag_info = {"data_id": os.path.basename(bag_path), "local_path": bag_path}
    else:
        if not bag_id:
            logger.error("必须提供 --bag_id 或 --bag_path")
            return False
        logger.info("正在通过 dm_sdk 解析 bag_id=%s ...", bag_id)
        try:
            _bag_path, bag_info = resolve_bag_path(
                bag_id, access_token=access_token, oss_mount_map=oss_mount_map,
            )
            logger.info("解析完成: local_path=%s, vin=%s",
                        bag_info["local_path"], bag_info.get("vin"))
        except Exception as exc:
            logger.error("路径解析失败: %s", exc)
            return False

    if not os.path.isdir(_bag_path):
        logger.error("bag 目录不存在: %s", _bag_path)
        return False

    # 输出目录
    if output_dir:
        _output_dir = output_dir
    else:
        import time
        ts_tag = time.strftime("%Y%m%d_%H%M%S")
        bag_name = bag_info.get("data_id", "unknown")
        _output_dir = os.path.join(".", "extracted_images", f"{bag_name}_{ts_tag}")
    os.makedirs(_output_dir, exist_ok=True)

    # 提取图片
    logger.info("开始提取: bag=%s, topics=%s, [%d, %d] ns",
                _bag_path, _topics, start_ns, end_ns)
    try:
        result = extract_images_from_bag(
            bag_path=_bag_path, topics=_topics,
            start_ts=start_ns, end_ts=end_ns,
            output_dir=_output_dir, image_format=image_format,
        )
    except Exception as exc:
        logger.error("提取失败: %s", exc)
        return False

    total = sum(result.values())
    logger.info("=" * 60)
    logger.info("提取完成！共 %d 帧 → %s", total, os.path.abspath(_output_dir))
    for topic, count in result.items():
        logger.info("  %-40s %d 帧", topic, count)
    logger.info("=" * 60)

    # 写元数据
    import json
    meta_path = os.path.join(_output_dir, "_extraction_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "bag_path": _bag_path, "bag_info": bag_info,
            "topics": _topics, "start_ts_ns": start_ns, "end_ts_ns": end_ns,
            "result_counts": result, "total_frames": total,
        }, f, ensure_ascii=False, indent=2)
    return True


# ═══════════════════════════════════════════════════════════════════════
#  6. CSV 批量模式
# ═══════════════════════════════════════════════════════════════════════

def run_csv_tasks(
    csv_path: str,
    topics: Optional[List[str]],
    cam_names: Optional[List[str]],
    output_base: Optional[str],
    image_format: str,
    access_token: Optional[str],
    oss_mount_map: Optional[str],
):
    """
    从 CSV 文件读取批量任务并逐条执行。

    CSV 格式: bag_id,start_ts,end_ts
    - 首行如果是非数字（表头），自动跳过
    - 支持可选的第4列 output_dir
    """
    if not os.path.exists(csv_path):
        logger.error("CSV 文件不存在: %s", csv_path)
        sys.exit(1)

    tasks = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if not row or not row[0].strip():
                continue
            # 跳过表头（如果第一列不是数字或看起来像列名）
            if i == 0:
                first_val = row[0].strip().lower()
                if first_val in ("bag_id", "bagid", "id", "#bag_id") or not first_val.replace("-", "").replace("_", "").isalnum():
                    # 更可靠的判断：如果 start_ts 列不是数字就认为是表头
                    try:
                        int(row[1].strip())
                    except (ValueError, IndexError):
                        logger.info("跳过 CSV 表头: %s", row)
                        continue

            if len(row) < 3:
                logger.warning("第 %d 行列数不足3，跳过: %s", i + 1, row)
                continue

            task = {
                "bag_id": row[0].strip(),
                "start_ts": row[1].strip(),
                "end_ts": row[2].strip(),
                "output_dir": row[3].strip() if len(row) > 3 else None,
            }
            tasks.append(task)

    if not tasks:
        logger.error("CSV 中没有有效任务")
        sys.exit(1)

    logger.info("从 CSV 读取到 %d 条任务", len(tasks))

    success, fail = 0, 0
    for i, task in enumerate(tasks, 1):
        logger.info("\n" + "=" * 60)
        logger.info("任务 %d/%d: bag_id=%s, [%s, %s]",
                    i, len(tasks), task["bag_id"], task["start_ts"], task["end_ts"])
        logger.info("=" * 60)

        # 如果指定了 output_base，每个任务的输出放在 output_base/bag_id/ 下
        task_output = task["output_dir"]
        if not task_output and output_base:
            task_output = os.path.join(output_base, task["bag_id"])

        ok = run_single_task(
            bag_id=task["bag_id"],
            bag_path=None,
            start_ts=task["start_ts"],
            end_ts=task["end_ts"],
            topics=topics,
            cam_names=cam_names,
            output_dir=task_output,
            image_format=image_format,
            access_token=access_token,
            oss_mount_map=oss_mount_map,
        )
        if ok:
            success += 1
        else:
            fail += 1

    logger.info("\n" + "#" * 60)
    logger.info("全部完成: 成功 %d, 失败 %d, 共 %d", success, fail, len(tasks))
    logger.info("#" * 60)


# ═══════════════════════════════════════════════════════════════════════
#  7. CLI 入口
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="从 SQL 查询结果的 bag_id + 时间范围，倒查原始 rosbag 并提取图片帧",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单条提取 (通过 bag_id)
  python extract_bag_images.py --bag_id 13qCIWDN --start_ts 1773270382 --end_ts 1773270389

  # 单条提取 (直接指定 bag 路径)
  python extract_bag_images.py --bag_path /mnt/.../bag_dir --start_ts 1773270382 --end_ts 1773270389

  # 批量 CSV 提取
  python extract_bag_images.py --csv tasks.csv

  # CSV + 自定义输出根目录 (每个 bag_id 一个子目录)
  python extract_bag_images.py --csv tasks.csv --output_dir /data/output

CSV 格式 (逗号分隔，支持表头自动跳过):
  bag_id,start_ts,end_ts
  13qCIWDNxsqGSksD56q2si202605,1773270382,1773270389
  another_bag_id,1773270400,1773270410
        """,
    )

    # ──── 输入模式 ────
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--csv", help="CSV 文件路径，批量提取（格式: bag_id,start_ts,end_ts）")
    input_group.add_argument("--bag_id", help="单条: 回灌后的 bag_id")
    input_group.add_argument("--bag_path", help="单条: 本地 rosbag 目录路径")

    # ──── 时间范围 (单条模式) ────
    parser.add_argument("--start_ts", type=str, default=None,
                        help="起始时间戳（秒或纳秒，自动识别）")
    parser.add_argument("--end_ts", type=str, default=None,
                        help="结束时间戳（秒或纳秒，自动识别）")

    # ──── 摄像头 topic ────
    parser.add_argument("--topics", nargs="+", default=None,
                        help="摄像头 topic 列表，例如 /gac/cam/fw120_encoded")
    parser.add_argument("--cam_names", nargs="+", default=None,
                        help="摄像头短名，例如 fw120 fw60 r50")

    # ──── 输出 ────
    parser.add_argument("--output_dir", default=None,
                        help="单条模式: 图片输出目录；CSV模式: 输出根目录（每个bag一个子目录）")
    parser.add_argument("--format", choices=["jpeg", "png"], default="jpeg",
                        help="输出图片格式 (默认: jpeg)")

    # ──── dm_sdk ────
    parser.add_argument("--access_token", default=None)
    parser.add_argument("--oss_mount_map", default=None)

    args = parser.parse_args()

    # ──── CSV 批量模式 ────
    if args.csv:
        run_csv_tasks(
            csv_path=args.csv,
            topics=args.topics,
            cam_names=args.cam_names,
            output_base=args.output_dir,
            image_format=args.format,
            access_token=args.access_token,
            oss_mount_map=args.oss_mount_map,
        )
        return

    # ──── 单条模式 ────
    if not args.bag_id and not args.bag_path:
        parser.error("单条模式需要 --bag_id 或 --bag_path")
    if not args.start_ts or not args.end_ts:
        parser.error("单条模式需要 --start_ts 和 --end_ts")

    ok = run_single_task(
        bag_id=args.bag_id,
        bag_path=args.bag_path,
        start_ts=args.start_ts,
        end_ts=args.end_ts,
        topics=args.topics,
        cam_names=args.cam_names,
        output_dir=args.output_dir,
        image_format=args.format,
        access_token=args.access_token,
        oss_mount_map=args.oss_mount_map,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
