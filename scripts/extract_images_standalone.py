#!/usr/bin/env python3
"""
extract_images.py — 从 SQL 查询结果提取 Rosbag 图片帧（独立版本）

自包含脚本，不依赖 SceneSQL 项目结构。可直接分享给同事使用。

流程：
  bag_id → dm_sdk 倒查原始 bag 路径 → gsbag 提取 HEVC 帧 → 解码保存为 JPEG/PNG

用法:
  # 单条提取 (通过 bag_id，需 dm_sdk)
  python extract_images.py --bag_id 13qCIWDNxsqGSksD56q2si202605 --start_ts 1773270382 --end_ts 1773270389

  # 单条提取 (直接指定 bag 路径，无需 dm_sdk)
  python extract_images.py --bag_path /mnt/gacrnd-ali-collect-t68-thor/.../bag_dir --start_ts 1773270382 --end_ts 1773270389

  # 批量 CSV 提取
  python extract_images.py --csv tasks.csv --output_dir /data/output

  # 只提取指定摄像头
  python extract_images.py --bag_id xxx --start_ts 1 --end_ts 2 --cam_names fw120

CSV 格式 (逗号分隔，首行表头自动跳过):
  bag_id,start_ts,end_ts
  13qCIWDNxsqGSksD56q2si202605,1773270382,1773270389
  another_id,1773270400,1773270410

  也可直接给 bag_path（第1列非 bag_id 时需用 --csv_mode path）:
  /mnt/.../bag_dir,1773270382,1773270389

环境要求:
  - Python 3.10+ (uv 管理的 venv 或系统 Python 均可)
  - gsbag 包 (pip install)
  - dm_sdk 包 (可选，仅 --bag_id 模式需要)
  - PyAV + Pillow (用于 HEVC 解码)
  - 关键: gsbag 是 C 扩展，需要 LD_LIBRARY_PATH 包含:
    · libpython3.x.so 所在目录
    · libgacbag_*.so 所在目录 (通常在 .venv/lib/ 或 gsbag SDK lib/)
  本脚本会自动检测并设置 LD_LIBRARY_PATH，必要时重启动自身。
"""

import os
import sys
import csv
import json
import time
import argparse
import logging
import subprocess
from io import BytesIO
from typing import List, Optional, Dict

# ═══════════════════════════════════════════════════════════════════════
#  配置区 — 按你的 DSW 环境修改这些默认值
# ═══════════════════════════════════════════════════════════════════════

# gsbag SDK 路径 (包含 lib/ 和 external/)
DEFAULT_GSBAG_SDK = "/root/data/text2sql/three_party/gsbag_x86_Release_4.2.18_20260227_Linux"

# boleidl_pb2 proto 路径 (包含 j6/image_encode/boleidl_pb2.py)
DEFAULT_PROTO_BASE = "/root/data/data_mining/UBM_mining/ubm_data_mining/gsbag_parser/proto/v4.8.3"

# OSS 挂载映射 (格式: oss前缀:本地路径,oss前缀:本地路径)
DEFAULT_OSS_MOUNT_MAP = (
    "gacrnd-oss/gac_liulian:/mnt/gacrnd-oss/gac_liulian,"
    "gacrnd-ali-collect-t68-thor:/mnt/gacrnd-ali-collect-t68-thor"
)

# dm_sdk access token (用于 bag_id → bag 路径倒查)
DEFAULT_DM_ACCESS_TOKEN = ""

# ═══════════════════════════════════════════════════════════════════════
#  摄像头 topic 映射
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

DEFAULT_TOPICS = ["/gac/cam/fw120_encoded"]

# ═══════════════════════════════════════════════════════════════════════
#  日志
# ═══════════════════════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
#  0. 环境自举 — LD_LIBRARY_PATH 必须在进程启动前设好
# ═══════════════════════════════════════════════════════════════════════

def _bootstrap_env(gsbag_sdk: str, proto_base: str):
    """
    检测并设置 LD_LIBRARY_PATH 等环境变量。

    如果当前进程缺少必要的 LD_LIBRARY_PATH，设置后 os.execv 重启自身。
    这是处理 gsbag C 扩展依赖 .so 的唯一可靠方式。
    """
    # 如果环境标记已存在，说明已经完成自举，跳过
    if os.getenv("__EXTRACT_IMAGES_ENV_READY") == "1":
        # 只需补 proto sys.path
        _inject_proto_path(proto_base)
        return

    need_reexec = False
    ld_path = os.environ.get("LD_LIBRARY_PATH", "")

    # 收集需要加入 LD_LIBRARY_PATH 的目录
    lib_dirs = []

    # 1. gsbag SDK lib
    sdk_lib = os.path.join(gsbag_sdk, "lib")
    if os.path.isdir(sdk_lib):
        lib_dirs.append(sdk_lib)

    # 2. gsbag SDK platform_sdk
    for sub in ["gacrnd", "third_party"]:
        d = os.path.join(gsbag_sdk, "external", "platform_sdk", "lib", sub)
        if os.path.isdir(d):
            lib_dirs.append(d)

    # 3. uv Python 的 libpython3.x.so
    exe_real = os.path.realpath(sys.executable)
    exe_dir = os.path.dirname(exe_real)
    uv_lib = os.path.join(os.path.dirname(exe_dir), "lib")
    if os.path.isdir(uv_lib) and any(
        f.startswith("libpython") and f.endswith(".so")
        for f in os.listdir(uv_lib)
    ):
        lib_dirs.append(uv_lib)

    # 4. sysconfig.LIBDIR
    import sysconfig
    sysconfig_lib = sysconfig.get_config_var("LIBDIR")
    if sysconfig_lib and os.path.isdir(sysconfig_lib):
        lib_dirs.append(sysconfig_lib)

    # 5. venv/lib — pip 安装的 gsbag .so 在这里 (libgacbag_storage.so.4 等)
    #    sys.prefix 在 venv 下指向 .venv/ 根目录
    venv_lib = os.path.join(sys.prefix, "lib")
    if os.path.isdir(venv_lib) and any(
        f.startswith("libgacbag") or f.startswith("libpython")
        for f in os.listdir(venv_lib)
    ):
        lib_dirs.append(venv_lib)

    # 6. HOBOT_COM_SDK
    hobot_sdk = os.path.join(gsbag_sdk, "external", "platform_sdk")
    if os.path.isdir(hobot_sdk):
        os.environ["HOBOT_COM_SDK"] = hobot_sdk

    # 合并到 LD_LIBRARY_PATH
    for d in lib_dirs:
        if d not in ld_path:
            ld_path = f"{d}:{ld_path}"
            need_reexec = True

    os.environ["LD_LIBRARY_PATH"] = ld_path
    os.environ["GSBAG_SDK"] = gsbag_sdk
    os.environ["__EXTRACT_IMAGES_ENV_READY"] = "1"

    if need_reexec:
        logger.info("环境变量已设置，重启动以加载动态库...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    _inject_proto_path(proto_base)


def _inject_proto_path(proto_base: str):
    """将 proto 路径加入 sys.path (用于 j6.image_encode.boleidl_pb2)。"""
    if proto_base and os.path.isdir(proto_base):
        if proto_base not in sys.path:
            sys.path.append(proto_base)
        j6 = os.path.join(proto_base, "j6")
        if os.path.isdir(j6) and j6 not in sys.path:
            sys.path.append(j6)
        logger.info("Proto: %s", proto_base)


# ═══════════════════════════════════════════════════════════════════════
#  1. OSS 路径映射
# ═══════════════════════════════════════════════════════════════════════

def parse_oss_mount_map(s: Optional[str]) -> Dict[str, str]:
    result = {}
    if not s:
        return result
    for pair in s.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        k, v = pair.split(":", 1)
        result[k.strip()] = v.strip()
    return result


def oss_to_local(oss_path: str, mount_map: Dict[str, str]) -> Optional[str]:
    if not oss_path:
        return None
    oss_path = oss_path.rstrip("/")
    if oss_path.startswith("oss://"):
        oss_path = oss_path[6:]
    for k, v in sorted(mount_map.items(), key=lambda x: -len(x[0])):
        if oss_path.startswith(k):
            rel = oss_path[len(k):]
            if rel.startswith("/"):
                rel = rel[1:]
            return os.path.normpath(os.path.join(v, rel))
    return os.path.normpath(oss_path)


# ═══════════════════════════════════════════════════════════════════════
#  2. dm_sdk 路径解析 (可选)
# ═══════════════════════════════════════════════════════════════════════

def resolve_bag_path_via_dmsdk(bag_id: str, access_token: str, oss_mount_map_str: str) -> tuple:
    """
    通过 dm_sdk 从 bag_id 倒查本地 rosbag 路径。
    返回 (local_path, info_dict)
    """
    try:
        from dm_sdk import ProdDataClient, RawDataClient
    except ImportError:
        raise ImportError("dm_sdk 未安装。使用 --bag_path 直接指定 bag 目录可绕过此依赖。")

    mount_map = parse_oss_mount_map(oss_mount_map_str)

    # Step 1: 产线表查 origins
    prod = ProdDataClient(access_token=access_token, table="ubm_vehicle_module_bin")
    resp = prod.get_bag_metadata(data_id=bag_id)
    if resp.resp_code() != 200:
        raise RuntimeError(f"ProdData query failed: {resp.msg}")
    prod_data = resp.resp_data()
    if not prod_data:
        raise ValueError(f"Bag {bag_id} not found in ubm_vehicle_module_bin")
    origins = prod_data.get("origins", [])
    if not origins:
        raise ValueError(f"Bag {bag_id} has no origins info")
    origin = origins[0]
    origin_table = origin.get("table")
    origin_bag_id = origin.get("bag_id")
    if not origin_table or not origin_bag_id:
        raise ValueError(f"Origins info incomplete for {bag_id}")

    # Step 2: 原始表查 storage_prefix
    raw = RawDataClient(access_token=access_token, table=origin_table)
    raw_resp = raw.get_bag_metadata(bag_id=origin_bag_id)
    if raw_resp.resp_code() != 200:
        raise RuntimeError(f"RawData query failed: {raw_resp.msg}")
    raw_data = raw_resp.resp_data() or {}
    oss_path = raw_data.get("storage_prefix") or raw_data.get("raw_storage_prefix")

    # Step 3: OSS → 本地
    local_path = oss_to_local(oss_path, mount_map) if oss_path else None
    info = {
        "data_id": bag_id, "origin_bag_id": origin_bag_id,
        "oss_path": oss_path, "local_path": local_path,
        "vin": raw_data.get("vin"), "vehicle_model": raw_data.get("vehicle_model"),
    }

    if not local_path:
        raise ValueError(f"无法解析 bag_id={bag_id} 的本地路径 (OSS={oss_path})")
    if not os.path.exists(local_path):
        local_path = _download_from_oss(oss_path, bag_id)

    return local_path, info


def _download_from_oss(oss_path: str, bag_id: str) -> str:
    tmp = f"/tmp/bag_staging/{bag_id}"
    os.makedirs(tmp, exist_ok=True)
    url = oss_path if oss_path.startswith("oss://") else f"oss://{oss_path}"
    logger.info("从 OSS 下载: %s → %s", url, tmp)
    r = subprocess.run(["ossutil64", "cp", "-r", url, tmp], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"ossutil 失败: {r.stderr}")
    name = os.path.basename(oss_path)
    for c in [os.path.join(tmp, name), os.path.join(tmp, name, name)]:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(f"下载后找不到 bag: {tmp}")


# ═══════════════════════════════════════════════════════════════════════
#  3. metadata.yaml
# ═══════════════════════════════════════════════════════════════════════

def get_bag_time_range(bag_path: str):
    import yaml
    mp = os.path.join(bag_path, "metadata.yaml")
    if not os.path.exists(mp):
        return None, None
    try:
        with open(mp) as f:
            meta = yaml.safe_load(f)
        info = meta.get("gacbag_bagfile_information", {})
        dur = info.get("duration", {}).get("seconds", 0)
        st = info.get("start_time")
        st_ns = None
        if st and isinstance(st, dict):
            st_ns = int(st.get("seconds", 0) or 0) * 1_000_000_000 + int(st.get("nanoseconds", 0) or 0)
        elif st and isinstance(st, (int, float)):
            st_ns = int(st)
        en_ns = st_ns + int(dur * 1_000_000_000) if st_ns is not None else None
        return st_ns, en_ns
    except Exception:
        return None, None


# ═══════════════════════════════════════════════════════════════════════
#  4. 图片帧提取核心
# ═══════════════════════════════════════════════════════════════════════

def extract_images_from_bag(
    bag_path: str, topics: List[str],
    start_ts: Optional[int], end_ts: Optional[int],
    output_dir: str, image_format: str = "jpeg",
) -> Dict[str, int]:
    from gsbag import gsbag_reader
    from j6.image_encode import boleidl_pb2 as image_encode_boleidl_pb2

    # Clamp
    bag_start, bag_end = get_bag_time_range(bag_path)
    if start_ts is not None and bag_start is not None and start_ts < bag_start:
        logger.info("start_ts %d < bag_start %d, 钳位", start_ts, bag_start)
        start_ts = bag_start
    if end_ts is not None and bag_end is not None and end_ts > bag_end:
        logger.info("end_ts %d > bag_end %d, 钳位", end_ts, bag_end)
        end_ts = bag_end

    counts = {}
    for topic in topics:
        tdir = topic.strip("/").replace("/", "_")
        out = os.path.join(output_dir, tdir)
        os.makedirs(out, exist_ok=True)
        logger.info("提取 topic=%s [%s, %s]", topic, start_ts, end_ts)

        try:
            reader = gsbag_reader.GsBagReader(bag_path)
            reader.set_topic_filter([topic])
        except Exception as e:
            logger.error("打开 bag 失败: %s", e)
            counts[topic] = 0
            continue

        frames = []  # [(ts, hevc_bytes)]
        errs = 0
        for m in reader.read_messages():
            ts = m.timestamp
            if start_ts is not None and ts < start_ts:
                continue
            if end_ts is not None and ts > end_ts:
                continue
            try:
                msg = image_encode_boleidl_pb2.Image()
                data = []
                gsbag_reader.HobotMessageSerializer.deserialize_image(m, msg, data)
                if data:
                    frames.append((ts, data[0]))
            except Exception as e:
                errs += 1
                if errs <= 5:
                    logger.warning("帧解码错误: %s", e)
        if errs > 5:
            logger.info("跳过 %d 个解码错误帧", errs)

        total = len(frames)
        logger.info("topic=%s: %d 帧", topic, total)
        if total == 0:
            counts[topic] = 0
            continue

        saved = 0
        for idx, (ts, payload) in enumerate(frames):
            try:
                img = _decode_hevc(payload)
                if img is None:
                    continue
                ext = "jpg" if image_format == "jpeg" else "png"
                fp = os.path.join(out, f"{ts}.{ext}")
                img.save(fp, image_format.upper())
                saved += 1
                if (idx + 1) % 50 == 0:
                    logger.info("  [%s] %d/%d", tdir, saved, total)
            except Exception as e:
                if saved == 0:
                    logger.warning("帧保存失败: %s", e)

        counts[topic] = saved
        logger.info("topic=%s: 保存 %d 帧 → %s", topic, saved, out)
    return counts


def _decode_hevc(raw: bytes):
    try:
        import av
        c = av.open(BytesIO(raw), mode="r", format="hevc")
        for pkt in c.demux(video=0):
            for f in pkt.decode():
                return f.to_image()
        c.close()
    except ImportError:
        return _decode_hevc_ffmpeg(raw)
    except Exception:
        try:
            import av
            buf = BytesIO(raw)
            c = av.open(buf, mode="r", format="hevc")
            imgs = []
            for pkt in c.demux(video=0):
                for f in pkt.decode():
                    imgs.append(f.to_image())
            c.close()
            return imgs[0] if imgs else None
        except Exception:
            return None
    return None


def _decode_hevc_ffmpeg(raw: bytes):
    try:
        from PIL import Image
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "hevc", "-i", "-",
             "-f", "image2pipe", "-vcodec", "png", "-"],
            input=raw, capture_output=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout:
            return Image.open(BytesIO(r.stdout))
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════════
#  5. 任务执行
# ═══════════════════════════════════════════════════════════════════════

def parse_time(val: str) -> int:
    """自动识别秒/纳秒：< 10^10 视为秒，否则纳秒。"""
    n = int(val)
    if n < 10_000_000_000:
        logger.info("时间戳 %d → 秒，转纳秒: %d", n, n * 1_000_000_000)
        return n * 1_000_000_000
    return n


def run_one(
    bag_id: Optional[str], bag_path: Optional[str],
    start_ts: str, end_ts: str,
    topics: List[str], output_dir: Optional[str],
    image_format: str, access_token: str, oss_mount_map: str,
) -> bool:
    start_ns = parse_time(start_ts)
    end_ns = parse_time(end_ts)

    if not topics:
        topics = DEFAULT_TOPICS

    # bag 路径
    info = {}
    if bag_path:
        _bag = bag_path
        info = {"data_id": os.path.basename(bag_path), "local_path": bag_path}
    elif bag_id:
        try:
            _bag, info = resolve_bag_path_via_dmsdk(bag_id, access_token, oss_mount_map)
            logger.info("路径解析: %s (vin=%s)", _bag, info.get("vin"))
        except Exception as e:
            logger.error("路径解析失败: %s", e)
            return False
    else:
        logger.error("需要 --bag_id 或 --bag_path")
        return False

    if not os.path.isdir(_bag):
        logger.error("bag 目录不存在: %s", _bag)
        return False

    # 输出目录
    if output_dir:
        _out = output_dir
    else:
        ts_tag = time.strftime("%Y%m%d_%H%M%S")
        _out = os.path.join(".", "extracted_images", f"{info.get('data_id', 'unknown')}_{ts_tag}")
    os.makedirs(_out, exist_ok=True)

    logger.info("提取: bag=%s topics=%s [%d, %d]ns → %s", _bag, topics, start_ns, end_ns, _out)
    try:
        result = extract_images_from_bag(_bag, topics, start_ns, end_ns, _out, image_format)
    except Exception as e:
        logger.error("提取失败: %s", e)
        return False

    total = sum(result.values())
    logger.info("=" * 60)
    logger.info("完成! %d 帧 → %s", total, os.path.abspath(_out))
    for t, c in result.items():
        logger.info("  %-40s %d", t, c)
    logger.info("=" * 60)

    with open(os.path.join(_out, "_extraction_meta.json"), "w", encoding="utf-8") as f:
        json.dump({
            "bag_path": _bag, "bag_info": info, "topics": topics,
            "start_ts_ns": start_ns, "end_ts_ns": end_ns,
            "result_counts": result, "total_frames": total,
        }, f, ensure_ascii=False, indent=2)
    return True


def run_csv(
    csv_path: str, csv_mode: str,
    topics: List[str], output_base: Optional[str],
    image_format: str, access_token: str, oss_mount_map: str,
):
    if not os.path.exists(csv_path):
        logger.error("CSV 不存在: %s", csv_path)
        sys.exit(1)

    tasks = []
    with open(csv_path, encoding="utf-8") as f:
        for i, row in enumerate(csv.reader(f)):
            if not row or not row[0].strip():
                continue
            if i == 0:
                # 跳过表头
                try:
                    int(row[1].strip())
                except (ValueError, IndexError):
                    continue
            if len(row) < 3:
                logger.warning("第 %d 行不足3列，跳过", i + 1)
                continue
            tasks.append({
                "col1": row[0].strip(),
                "start_ts": row[1].strip(),
                "end_ts": row[2].strip(),
            })

    if not tasks:
        logger.error("CSV 无有效任务")
        sys.exit(1)

    logger.info("CSV: %d 条任务", len(tasks))
    ok, fail = 0, 0

    for i, t in enumerate(tasks, 1):
        logger.info("\n" + "=" * 60)
        logger.info("[%d/%d] %s  [%s, %s]", i, len(tasks), t["col1"], t["start_ts"], t["end_ts"])

        # csv_mode=id → col1 是 bag_id; csv_mode=path → col1 是 bag_path
        bag_id = t["col1"] if csv_mode == "id" else None
        bag_path = t["col1"] if csv_mode == "path" else None

        # 输出: output_base/bag_id/
        task_out = os.path.join(output_base, t["col1"]) if output_base else None

        if run_one(bag_id, bag_path, t["start_ts"], t["end_ts"],
                    topics, task_out, image_format, access_token, oss_mount_map):
            ok += 1
        else:
            fail += 1

    logger.info("\n" + "#" * 60)
    logger.info("全部完成: 成功 %d / 失败 %d / 共 %d", ok, fail, len(tasks))
    logger.info("#" * 60)


# ═══════════════════════════════════════════════════════════════════════
#  6. CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="从 SQL 查询结果提取 Rosbag 图片帧（独立版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python extract_images.py --bag_id 13qCIWDN --start_ts 1773270382 --end_ts 1773270389
  python extract_images.py --bag_path /mnt/.../bag --start_ts 1773270382 --end_ts 1773270389
  python extract_images.py --csv tasks.csv --output_dir /data/output
  python extract_images.py --csv tasks.csv --cam_names fw120 --output_dir /data/output
        """,
    )

    # 输入
    inp = p.add_mutually_exclusive_group()
    inp.add_argument("--csv", help="CSV 文件 (批量模式)")
    inp.add_argument("--bag_id", help="bag_id (需 dm_sdk)")
    inp.add_argument("--bag_path", help="bag 本地路径 (无需 dm_sdk)")

    p.add_argument("--csv_mode", choices=["id", "path"], default="id",
                   help="CSV 第1列含义: id=bag_id (默认), path=bag_path")
    p.add_argument("--start_ts", help="起始时间戳 (秒或纳秒)")
    p.add_argument("--end_ts", help="结束时间戳 (秒或纳秒)")

    # topic
    p.add_argument("--topics", nargs="+", default=None,
                   help="摄像头 topic，如 /gac/cam/fw120_encoded")
    p.add_argument("--cam_names", nargs="+", default=None,
                   help="摄像头短名: fw120 fw60 r50 fl99 等")

    # 输出
    p.add_argument("--output_dir", default=None,
                   help="单条: 输出目录; CSV: 输出根目录 (每个bag一个子目录)")
    p.add_argument("--format", choices=["jpeg", "png"], default="jpeg")

    # 环境配置
    p.add_argument("--gsbag_sdk", default=DEFAULT_GSBAG_SDK, help="gsbag SDK 路径")
    p.add_argument("--proto_base", default=DEFAULT_PROTO_BASE, help="proto 路径")
    p.add_argument("--access_token", default=DEFAULT_DM_ACCESS_TOKEN, help="dm_sdk token")
    p.add_argument("--oss_mount_map", default=DEFAULT_OSS_MOUNT_MAP, help="OSS 挂载映射")

    args = p.parse_args()

    # ─── 环境自举 ───
    _bootstrap_env(args.gsbag_sdk, args.proto_base)

    # ─── 解析 topics ───
    topics = args.topics
    if not topics and args.cam_names:
        topics = [CAMERA_TOPIC_MAP[n] for n in args.cam_names if n in CAMERA_TOPIC_MAP]
        bad = [n for n in args.cam_names if n not in CAMERA_TOPIC_MAP]
        if bad:
            logger.warning("未知摄像头: %s (可用: %s)", bad, list(CAMERA_TOPIC_MAP.keys()))
    if not topics:
        topics = DEFAULT_TOPICS

    # ─── 执行 ───
    if args.csv:
        run_csv(
            args.csv, args.csv_mode, topics, args.output_dir,
            args.format, args.access_token, args.oss_mount_map,
        )
    else:
        if not args.bag_id and not args.bag_path:
            p.error("需要 --csv, --bag_id 或 --bag_path")
        if not args.start_ts or not args.end_ts:
            p.error("单条模式需要 --start_ts 和 --end_ts")
        ok = run_one(
            args.bag_id, args.bag_path,
            args.start_ts, args.end_ts,
            topics, args.output_dir,
            args.format, args.access_token, args.oss_mount_map,
        )
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
