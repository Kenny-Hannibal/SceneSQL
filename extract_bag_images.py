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
  python extract_bag_images.py \
    --bag_id <bag_id> \
    --start_ts <纳秒时间戳> \
    --end_ts <纳秒时间戳> \
    [--topics /gac/cam/fw120_encoded /gac/cam/fw60_encoded] \
    [--output_dir ./extracted_images] \
    [--format jpeg] \
    [--access_token <token>] \
    [--oss_mount_map <map_str>]

注意:
  - range_tag 表的 start_ts/end_ts 单位是【秒】，本脚本统一使用【纳秒】
    如果你的 start_ts/end_ts 来自 range_tag，请先乘以 1_000_000_000
  - 如果 start_ts/end_ts 来自 ego/dynamic_obj 表，已经是纳秒，直接传入即可
  - gsbag SDK 和 proto 依赖需要提前配置环境变量（见下方 ENV SETUP）
"""

import os
import sys
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
    # 优先加载项目根目录 .env，再加载当前目录 .env
    for _p in [
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
    """解析 OSS_MOUNT_MAP 字符串为 {oss_prefix: local_path} 字典。"""
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
    """将 OSS 路径转换为本地挂载路径。"""
    if not oss_path:
        return None
    oss_path = oss_path.rstrip("/")
    if oss_path.startswith("oss://"):
        oss_path = oss_path[6:]
    # 按前缀长度降序匹配，避免短前缀误匹配
    for oss_prefix, local_prefix in sorted(mount_map.items(), key=lambda x: -len(x[0])):
        if oss_path.startswith(oss_prefix):
            relative = oss_path[len(oss_prefix):]
            if relative.startswith("/"):
                relative = relative[1:]
            local = os.path.join(local_prefix, relative)
            return os.path.normpath(local)
    # 未匹配到，当作本地路径返回
    return os.path.normpath(oss_path)


# ═══════════════════════════════════════════════════════════════════════
#  2. dm_sdk 路径解析
# ═══════════════════════════════════════════════════════════════════════

class RosbagPathResolver:
    """从回灌 bag_id 解析到本地 rosbag 路径。"""

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
            raise ImportError(
                "dm_sdk 未安装。请先安装: pip install dm_sdk\n"
                "或设置 PYTHONPATH 包含 dm_sdk 所在目录"
            ) from exc

    def resolve(self, data_id: str) -> Dict:
        """
        一站式解析：
          回灌 bag_id → ProdDataClient 查原始 table + bag_id
          → RawDataClient 查 storage_prefix (OSS)
          → OSS_MOUNT_MAP 转本地路径

        返回: {
            data_id, origin_table, origin_bag_id,
            oss_path, local_path, bag_name, vin, vehicle_model
        }
        """
        # Step 1: 查询产线表 → 获取 origins
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

        # Step 2: 查询原始表 → 获取 storage_prefix
        raw_client = self._RawDataClient(
            access_token=self.access_token,
            table=origin_table,
        )
        raw_resp = raw_client.get_bag_metadata(bag_id=origin_bag_id)
        if raw_resp.resp_code() != 200:
            raise RuntimeError(f"RawData query failed: {raw_resp.msg}")

        raw_data = raw_resp.resp_data() or {}
        oss_path = raw_data.get("storage_prefix") or raw_data.get("raw_storage_prefix")

        # Step 3: OSS → 本地路径
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


def resolve_bag_path(
    bag_id: str,
    access_token: Optional[str] = None,
    oss_mount_map: Optional[str] = None,
) -> str:
    """从 bag_id 解析本地 rosbag 路径，失败则抛异常。"""
    resolver = RosbagPathResolver(
        access_token=access_token,
        oss_mount_map=oss_mount_map,
    )
    info = resolver.resolve(bag_id)
    local_path = info["local_path"]
    if not local_path:
        raise ValueError(f"无法解析 bag_id={bag_id} 的本地路径 (OSS={info['oss_path']})")
    if not os.path.exists(local_path):
        logger.warning("本地路径不存在: %s (OSS: %s)，尝试 ossutil 下载...", local_path, info["oss_path"])
        local_path = _download_bag_from_oss(info["oss_path"], bag_id)
    return local_path, info


def _download_bag_from_oss(oss_path: str, bag_id: str) -> str:
    """通过 ossutil64 从 OSS 下载 bag 到临时目录。"""
    tmp_dir = f"/tmp/bag_staging/{bag_id}"
    os.makedirs(tmp_dir, exist_ok=True)

    oss_url = oss_path
    if not oss_url.startswith("oss://"):
        oss_url = f"oss://{oss_url}"

    logger.info("正在从 OSS 下载 bag: %s -> %s", oss_url, tmp_dir)
    cmd = ["ossutil64", "cp", "-r", oss_url, tmp_dir]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ossutil 下载失败: {result.stderr}")

    # 找到下载后的实际 bag 目录
    bag_name = os.path.basename(oss_path)
    candidates = [
        os.path.join(tmp_dir, bag_name),
        os.path.join(tmp_dir, bag_name, bag_name),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(f"下载后找不到 bag 目录: {tmp_dir}")


# ═══════════════════════════════════════════════════════════════════════
#  3. metadata.yaml 读取 (bag 时间范围)
# ═══════════════════════════════════════════════════════════════════════

def get_bag_time_range(bag_path: str):
    """从 metadata.yaml 读取 bag 起止时间戳（纳秒）。"""
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


# ═══════════════════════════════════════════════════════════════════════
#  4. gsbag 环境初始化
# ═══════════════════════════════════════════════════════════════════════

def _find_python_libdir() -> str:
    """
    探测当前 Python 解释器对应的 libpython3.x.so 所在目录。

    uv 管理的 venv 中 sysconfig.LIBDIR 可能返回系统 Python 的路径
    （如 /usr/local/lib → 只有 libpython3.12），而实际用的是 uv 安装的
    Python 3.10（lib 在 ~/.local/share/uv/python/cpython-3.10.*/lib/）。
    因此：先从 sys.executable 向上找 lib/，再 fallback sysconfig。
    """
    import sysconfig

    # 方法1: 从 sys.executable 推断（uv 安装的 Python 在 <prefix>/bin/python3.10，
    #         对应的 so 在 <prefix>/lib/）
    exe_dir = os.path.dirname(os.path.realpath(sys.executable))
    # exe_dir 通常是 <prefix>/bin，lib 在 <prefix>/lib
    candidate = os.path.join(os.path.dirname(exe_dir), "lib")
    # 验证该目录下确实有 libpython
    if os.path.isdir(candidate) and any(
        f.startswith("libpython") and f.endswith(".so")
        for f in os.listdir(candidate)
    ):
        return candidate

    # 方法2: sysconfig.LIBDIR
    libdir = sysconfig.get_config_var("LIBDIR")
    if libdir and os.path.isdir(libdir):
        return libdir

    # 方法3: 常见 uv 安装路径探测
    import glob
    for pattern in [
        os.path.expanduser("~/.local/share/uv/python/cpython-3.*/lib"),
    ]:
        for d in sorted(glob.glob(pattern), reverse=True):
            if os.path.isdir(d) and any(
                f.startswith("libpython") and f.endswith(".so")
                for f in os.listdir(d)
            ):
                return d

    return ""


def setup_gsbag_env(gsbag_sdk_path: Optional[str] = None, proto_base: Optional[str] = None):
    """
    配置 gsbag SDK 所需的环境变量和 sys.path。

    gsbag_sdk_path: gsbag SDK 目录（包含 lib/ 和 gsbag 模块）
    proto_base:     boleidl_pb2 proto 文件所在根目录
    """
    # --- gsbag SDK ---
    sdk = gsbag_sdk_path or os.getenv("GSBAG_SDK", "")
    if not sdk:
        # 自动探测常见路径
        for candidate in [
            os.path.join(os.path.dirname(__file__), "three_party", "gsbag_x86_Release_4.2.18_20260227_Linux"),
            "/root/data/text2sql/three_party/gsbag_x86_Release_4.2.18_20260227_Linux",
        ]:
            if os.path.isdir(candidate):
                sdk = candidate
                break

    if sdk and os.path.isdir(sdk):
        os.environ["GSBAG_SDK"] = sdk
        # LD_LIBRARY_PATH — 必须包含 PYTHON_LIBDIR (libpython3.x.so 在这里)
        # uv 管理的 venv 中 sysconfig.LIBDIR 可能返回系统 Python 的路径（如
        # /usr/local/lib），而非 uv 安装的 Python 库目录。因此优先通过
        # sys.executable 探测，再 fallback 到 sysconfig。
        python_libdir = _find_python_libdir()
        ld_path = os.environ.get("LD_LIBRARY_PATH", "")
        for lib_dir in [
            os.path.join(sdk, "lib"),
            os.path.join(sdk, "external", "platform_sdk", "lib", "gacrnd"),
            os.path.join(sdk, "external", "platform_sdk", "lib", "third_party"),
            python_libdir,
        ]:
            if os.path.isdir(lib_dir) and lib_dir not in ld_path:
                ld_path = f"{lib_dir}:{ld_path}"
        os.environ["LD_LIBRARY_PATH"] = ld_path
        # HOBOT_COM_SDK
        hobot_sdk = os.path.join(sdk, "external", "platform_sdk")
        if os.path.isdir(hobot_sdk):
            os.environ["HOBOT_COM_SDK"] = hobot_sdk
        # sys.path: gsbag Python 包
        gsbag_py = os.path.join(sdk, "python")
        if os.path.isdir(gsbag_py) and gsbag_py not in sys.path:
            sys.path.insert(0, gsbag_py)
        logger.info("gsbag SDK 路径: %s", sdk)
    else:
        logger.warning("未找到 gsbag SDK，请设置 --gsbag_sdk 或 GSBAG_SDK 环境变量")

    # --- Proto ---
    proto = proto_base or os.getenv("PROTO_BASE", "")
    if not proto:
        # 自动探测
        for candidate in [
            "/data/var/workspace/projects/projects/data_mining/UBM_mining/ubm_data_mining/gsbag_parser/proto/v4.8.3",
            "/root/data/data_mining/UBM_mining/ubm_data_mining/gsbag_parser/proto/v4.8.3",
        ]:
            if os.path.isdir(candidate):
                proto = candidate
                break

    if proto and os.path.isdir(proto):
        j6_path = os.path.join(proto, "j6")
        if proto not in sys.path:
            sys.path.append(proto)
        if os.path.isdir(j6_path) and j6_path not in sys.path:
            sys.path.append(j6_path)
        logger.info("Proto 路径: %s", proto)
    else:
        logger.warning("未找到 proto 文件 (boleidl_pb2)，请设置 --proto_base 或 PROTO_BASE 环境变量")


# ═══════════════════════════════════════════════════════════════════════
#  5. 图片帧提取核心逻辑
# ═══════════════════════════════════════════════════════════════════════

# 摄像头名称 → topic 映射
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

# 默认提取的摄像头（前视120° + 前/后视常用）
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
    """
    从 rosbag 中提取指定 topic 和时间范围内的图片帧。

    参数:
        bag_path:    本地 bag 目录路径
        topics:      要提取的摄像头 topic 列表
        start_ts:    起始时间戳（纳秒），None=从 bag 开始
        end_ts:      结束时间戳（纳秒），None=到 bag 结束
        output_dir:  图片输出目录
        image_format: 输出格式 jpeg/png

    返回:
        {topic: 提取的帧数}
    """
    from gsbag import gsbag_reader
    from j6.image_encode import boleidl_pb2 as image_encode_boleidl_pb2

    # Clamp 时间范围到 bag 实际范围
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

        # 收集 HEVC 帧
        hevc_frames = []   # [(timestamp, hevc_bytes), ...]
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

        # 逐帧解码并保存为图片
        saved = 0
        for idx, (ts, hevc_payload) in enumerate(hevc_frames):
            try:
                img = _decode_hevc_frame(hevc_payload)
                if img is None:
                    continue
                ext = "jpg" if image_format == "jpeg" else "png"
                filename = f"{ts}.{ext}"
                filepath = os.path.join(topic_output_dir, filename)
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
    """使用 PyAV 将单个 HEVC 帧解码为 PIL Image。"""
    try:
        import av
        from PIL import Image

        container = av.open(BytesIO(hevc_bytes), mode="r", format="hevc")
        for packet in container.demux(video=0):
            for frame in packet.decode():
                return frame.to_image()
        container.close()
    except ImportError:
        # 降级：使用 ffmpeg 子进程
        return _decode_hevc_frame_ffmpeg(hevc_bytes)
    except Exception:
        # 尝试将所有帧合并后再解码
        try:
            import av
            buf = BytesIO()
            buf.write(hevc_bytes)
            buf.seek(0)
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
    """降级方案：使用 ffmpeg 子进程解码单帧。"""
    try:
        from PIL import Image
        import numpy as np

        # 先尝试获取帧尺寸（假设常见分辨率 1920x1080）
        # 对于 HEVC，SPS 中包含分辨率信息，ffmpeg 可以自动检测
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "hevc", "-i", "-",
            "-f", "image2pipe", "-vcodec", "png", "-",
        ]
        proc = subprocess.run(
            cmd, input=hevc_bytes, capture_output=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout:
            return Image.open(BytesIO(proc.stdout))
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════
#  6. CLI 入口
# ═══════════════════════════════════════════════════════════════════════

def parse_time_arg(val: str) -> int:
    """
    解析时间参数，支持自动识别秒/纳秒：
      - 如果值 < 1e15 (约 1970-01-01 + ~31年)，视为秒，自动转为纳秒
      - 否则视为纳秒
    """
    num = int(val)
    if num < 1_000_000_000_000_000:  # < 10^15 → 可能是秒
        if num < 10_000_000_000:     # < 10^10 → 几乎确定是秒级（1970-2031 范围）
            logger.info("时间戳 %d 识别为【秒】，自动转为纳秒: %d", num, num * 1_000_000_000)
            return num * 1_000_000_000
    return num


def main():
    parser = argparse.ArgumentParser(
        description="从 SQL 查询结果的 bag_id + 时间范围，倒查原始 rosbag 并提取图片帧",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 从 range_tag 查询出的 start_ts/end_ts 是秒级，会自动转为纳秒
  python extract_bag_images.py --bag_id 20260515_xxx --start_ts 1715700000 --end_ts 1715700060

  # 显式使用纳秒时间戳
  python extract_bag_images.py --bag_id 20260515_xxx --start_ts 1715700000000000000 --end_ts 1715700060000000000

  # 指定 topic 和输出目录
  python extract_bag_images.py --bag_id 20260515_xxx --start_ts 1715700000 --end_ts 1715700060 \\
      --topics /gac/cam/fw120_encoded /gac/cam/fw60_encoded --output_dir ./my_images

  # 直接提供本地 bag 路径（跳过 dm_sdk 解析）
  python extract_bag_images.py --bag_path /mnt/gacrnd-oss/.../bag_dir --start_ts 1715700000 --end_ts 1715700060
        """,
    )

    # ──── bag 标识 ────
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bag_id", help="回灌后的 bag_id（db 文件名），将通过 dm_sdk 倒查原始路径")
    group.add_argument("--bag_path", help="直接提供本地 rosbag 目录路径（跳过 dm_sdk 解析）")

    # ──── 时间范围 ────
    parser.add_argument("--start_ts", type=str, required=True,
                        help="起始时间戳（秒或纳秒，自动识别）")
    parser.add_argument("--end_ts", type=str, required=True,
                        help="结束时间戳（秒或纳秒，自动识别）")

    # ──── 摄像头 topic ────
    parser.add_argument("--topics", nargs="+", default=None,
                        help="要提取的摄像头 topic 列表，例如 /gac/cam/fw120_encoded。"
                             "默认提取 fw120+fw60+r50 三路")
    parser.add_argument("--cam_names", nargs="+", default=None,
                        help="用摄像头短名指定，例如 fw120 fw60 r50 fl99")

    # ──── 输出 ────
    parser.add_argument("--output_dir", default=None,
                        help="图片输出目录（默认: ./extracted_images/<bag_id>_<timestamp>）")
    parser.add_argument("--format", choices=["jpeg", "png"], default="jpeg",
                        help="输出图片格式 (默认: jpeg)")

    # ──── dm_sdk 配置 ────
    parser.add_argument("--access_token", default=None,
                        help="dm_sdk access token（默认读取 .env 中 DM_ACCESS_TOKEN）")
    parser.add_argument("--oss_mount_map", default=None,
                        help="OSS 挂载映射（格式: oss前缀:本地路径,oss前缀:本地路径）")

    # ──── gsbag 环境 ────
    parser.add_argument("--gsbag_sdk", default=None,
                        help="gsbag SDK 目录路径（默认自动探测或从 GSBAG_SDK 环境变量读取）")
    parser.add_argument("--proto_base", default=None,
                        help="boleidl_pb2 proto 文件根目录（默认自动探测）")

    args = parser.parse_args()

    # ── 解析时间参数 ──
    start_ns = parse_time_arg(args.start_ts)
    end_ns = parse_time_arg(args.end_ts)

    # ── 解析 topic 列表 ──
    topics = args.topics
    if not topics and args.cam_names:
        topics = [CAMERA_TOPIC_MAP[n] for n in args.cam_names if n in CAMERA_TOPIC_MAP]
        unknown = [n for n in args.cam_names if n not in CAMERA_TOPIC_MAP]
        if unknown:
            logger.warning("未知的摄像头名: %s (可用: %s)", unknown, list(CAMERA_TOPIC_MAP.keys()))
    if not topics:
        topics = DEFAULT_TOPICS
        logger.info("使用默认 topic 列表: %s", topics)

    # ── 解析 bag 路径 ──
    if args.bag_path:
        bag_path = args.bag_path
        bag_info = {"data_id": os.path.basename(bag_path), "local_path": bag_path}
        logger.info("使用直接提供的 bag 路径: %s", bag_path)
    else:
        logger.info("正在通过 dm_sdk 解析 bag_id=%s ...", args.bag_id)
        bag_path, bag_info = resolve_bag_path(
            args.bag_id,
            access_token=args.access_token,
            oss_mount_map=args.oss_mount_map,
        )
        logger.info("解析完成: local_path=%s, origin_bag_id=%s, vin=%s",
                     bag_info["local_path"], bag_info.get("origin_bag_id"), bag_info.get("vin"))

    if not os.path.isdir(bag_path):
        logger.error("bag 目录不存在: %s", bag_path)
        sys.exit(1)

    # ── 设置 gsbag 环境 ──
    setup_gsbag_env(gsbag_sdk_path=args.gsbag_sdk, proto_base=args.proto_base)

    # ── 输出目录 ──
    if args.output_dir:
        output_dir = args.output_dir
    else:
        import time
        ts_tag = time.strftime("%Y%m%d_%H%M%S")
        bag_name = bag_info.get("data_id", "unknown")
        output_dir = os.path.join(".", "extracted_images", f"{bag_name}_{ts_tag}")

    os.makedirs(output_dir, exist_ok=True)
    logger.info("图片输出目录: %s", output_dir)

    # ── 提取图片 ──
    logger.info("开始提取: topics=%s, start_ts=%d ns, end_ts=%d ns", topics, start_ns, end_ns)
    result = extract_images_from_bag(
        bag_path=bag_path,
        topics=topics,
        start_ts=start_ns,
        end_ts=end_ns,
        output_dir=output_dir,
        image_format=args.format,
    )

    # ── 输出汇总 ──
    total = sum(result.values())
    logger.info("=" * 60)
    logger.info("提取完成！共保存 %d 帧", total)
    logger.info("输出目录: %s", os.path.abspath(output_dir))
    for topic, count in result.items():
        logger.info("  %-40s %d 帧", topic, count)
    logger.info("=" * 60)

    # 写入元数据文件
    import json
    meta_path = os.path.join(output_dir, "_extraction_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "bag_path": bag_path,
            "bag_info": bag_info,
            "topics": topics,
            "start_ts_ns": start_ns,
            "end_ts_ns": end_ns,
            "image_format": args.format,
            "result_counts": result,
            "total_frames": total,
        }, f, ensure_ascii=False, indent=2)
    logger.info("元数据已写入: %s", meta_path)


if __name__ == "__main__":
    main()
