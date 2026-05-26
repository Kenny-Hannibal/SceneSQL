#!/usr/bin/env python3
"""
extract_images.py — 从 SQL 查询结果提取 Rosbag 图片帧

用法:
  python extract_images.py --bag_id xxx --start_ts 1773270382 --end_ts 1773270389
  python extract_images.py --bag_path /mnt/.../bag_dir --start_ts 1773270382 --end_ts 1773270389
  python extract_images.py --csv tasks.csv --output_dir /data/output
  python extract_images.py --csv tasks.csv --cam_names fw120

CSV 格式: bag_id,start_ts,end_ts (首行表头自动跳过)
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  配置区 — 同事拿到文件后，只需修改这里 ↓                            ║
# ╚══════════════════════════════════════════════════════════════════════╝

GSBAG_SDK = "/root/data/text2sql/three_party/gsbag_x86_Release_4.2.18_20260227_Linux"
PROTO_BASE = "/root/data/data_mining/UBM_mining/ubm_data_mining/gsbag_parser/proto/v4.8.3"
OSS_MOUNT_MAP = "gacrnd-oss/gac_liulian:/mnt/gacrnd-oss/gac_liulian,gacrnd-ali-collect-t68-thor:/mnt/gacrnd-ali-collect-t68-thor"
DM_ACCESS_TOKEN = os.getenv("DM_ACCESS_TOKEN", "")  # 优先读环境变量，否则在此填入

# LD_LIBRARY_PATH 需要的额外目录（按实际环境增减）
# 常见需要加入的: libpython3.x.so 所在目录, libgacbag_*.so 所在目录
EXTRA_LD_PATHS = [
    "/root/data/text2sql/.venv/lib",                                         # libgacbag_*.so
    "/root/.local/share/uv/python/cpython-3.10.19-linux-x86_64-gnu/lib",    # libpython3.10.so
]

# ╔══════════════════════════════════════════════════════════════════════╗
# ║  以下无需修改                                                      ║
# ╚══════════════════════════════════════════════════════════════════════╝

CAMERA_TOPIC_MAP = {
    "fw120": "/gac/cam/fw120_encoded", "fw60": "/gac/cam/fw60_encoded",
    "ft30": "/gac/cam/ft30_encoded",   "ft20": "/gac/cam/ft20_encoded",
    "r50": "/gac/cam/r50_encoded",     "fl99": "/gac/cam/fl99_encoded",
    "fr99": "/gac/cam/fr99_encoded",   "rl99": "/gac/cam/rl99_encoded",
    "rr99": "/gac/cam/rr99_encoded",
}
DEFAULT_TOPICS = ["/gac/cam/fw120_encoded"]


# ═══════════════════════════════════════════════════════════════════════
#  环境自举 — LD_LIBRARY_PATH 必须在进程启动前设好，否则 os.execv 重启
# ═══════════════════════════════════════════════════════════════════════

def _bootstrap():
    global DM_ACCESS_TOKEN
    if os.getenv("__EXTRACT_ENV_READY") == "1":
        # exec 后从 os.environ 恢复
        DM_ACCESS_TOKEN = os.getenv("DM_ACCESS_TOKEN", DM_ACCESS_TOKEN)
        # proto sys.path
        if PROTO_BASE and os.path.isdir(PROTO_BASE):
            if PROTO_BASE not in sys.path:
                sys.path.append(PROTO_BASE)
            j6 = os.path.join(PROTO_BASE, "j6")
            if os.path.isdir(j6) and j6 not in sys.path:
                sys.path.append(j6)
        return

    ld = os.environ.get("LD_LIBRARY_PATH", "")
    changed = False

    # gsbag SDK
    for d in [
        os.path.join(GSBAG_SDK, "lib"),
        os.path.join(GSBAG_SDK, "external", "platform_sdk", "lib", "gacrnd"),
        os.path.join(GSBAG_SDK, "external", "platform_sdk", "lib", "third_party"),
    ]:
        if os.path.isdir(d) and d not in ld:
            ld = f"{d}:{ld}"
            changed = True

    # 额外 lib 目录
    for d in EXTRA_LD_PATHS:
        if os.path.isdir(d) and d not in ld:
            ld = f"{d}:{ld}"
            changed = True

    # HOBOT_COM_SDK
    hobot = os.path.join(GSBAG_SDK, "external", "platform_sdk")
    if os.path.isdir(hobot):
        os.environ["HOBOT_COM_SDK"] = hobot

    os.environ["LD_LIBRARY_PATH"] = ld
    os.environ["GSBAG_SDK"] = GSBAG_SDK
    os.environ["DM_ACCESS_TOKEN"] = DM_ACCESS_TOKEN
    os.environ["__EXTRACT_ENV_READY"] = "1"

    if changed:
        logger.info("LD_LIBRARY_PATH 已设置，重启动...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    # proto
    if PROTO_BASE and os.path.isdir(PROTO_BASE):
        sys.path.append(PROTO_BASE)
        j6 = os.path.join(PROTO_BASE, "j6")
        if os.path.isdir(j6):
            sys.path.append(j6)


# ═══════════════════════════════════════════════════════════════════════
#  OSS
# ═══════════════════════════════════════════════════════════════════════

def _parse_mount_map(s):
    m = {}
    if not s:
        return m
    for p in s.split(","):
        p = p.strip()
        if ":" not in p:
            continue
        k, v = p.split(":", 1)
        m[k.strip()] = v.strip()
    return m


def _oss2local(path, mmap):
    if not path:
        return None
    path = path.rstrip("/")
    if path.startswith("oss://"):
        path = path[6:]
    for k, v in sorted(mmap.items(), key=lambda x: -len(x[0])):
        if path.startswith(k):
            r = path[len(k):]
            if r.startswith("/"):
                r = r[1:]
            return os.path.normpath(os.path.join(v, r))
    return os.path.normpath(path)


# ═══════════════════════════════════════════════════════════════════════
#  dm_sdk 路径解析
# ═══════════════════════════════════════════════════════════════════════

def _resolve_bag_path(bag_id, token, oss_map_str):
    from dm_sdk import ProdDataClient, RawDataClient
    mmap = _parse_mount_map(oss_map_str)

    prod = ProdDataClient(access_token=token, table="ubm_vehicle_module_bin")
    resp = prod.get_bag_metadata(data_id=bag_id)
    if resp.resp_code() != 200:
        raise RuntimeError(f"ProdData failed: {resp.msg}")
    pd = resp.resp_data()
    if not pd:
        raise ValueError(f"Bag {bag_id} not found")
    origins = pd.get("origins", [])
    if not origins:
        raise ValueError(f"Bag {bag_id} no origins")
    o = origins[0]
    otable, obag = o.get("table"), o.get("bag_id")
    if not otable or not obag:
        raise ValueError(f"Origins incomplete for {bag_id}")

    raw = RawDataClient(access_token=token, table=otable)
    rr = raw.get_bag_metadata(bag_id=obag)
    if rr.resp_code() != 200:
        raise RuntimeError(f"RawData failed: {rr.msg}")
    rd = rr.resp_data() or {}
    oss = rd.get("storage_prefix") or rd.get("raw_storage_prefix")
    local = _oss2local(oss, mmap) if oss else None
    info = {"data_id": bag_id, "origin_bag_id": obag, "oss_path": oss,
            "local_path": local, "vin": rd.get("vin"), "vehicle_model": rd.get("vehicle_model")}

    if not local:
        raise ValueError(f"无法解析本地路径 (OSS={oss})")
    if not os.path.exists(local):
        local = _download_oss(oss, bag_id)
    return local, info


def _download_oss(oss, bag_id):
    tmp = f"/tmp/bag_staging/{bag_id}"
    os.makedirs(tmp, exist_ok=True)
    url = oss if oss.startswith("oss://") else f"oss://{oss}"
    logger.info("OSS 下载: %s → %s", url, tmp)
    r = subprocess.run(["ossutil64", "cp", "-r", url, tmp], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"ossutil 失败: {r.stderr}")
    name = os.path.basename(oss)
    for c in [os.path.join(tmp, name), os.path.join(tmp, name, name)]:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(f"下载后找不到 bag: {tmp}")


# ═══════════════════════════════════════════════════════════════════════
#  metadata.yaml
# ═══════════════════════════════════════════════════════════════════════

def _bag_time_range(bag_path):
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
#  图片帧提取
# ═══════════════════════════════════════════════════════════════════════

def _extract(bag_path, topics, start_ts, end_ts, output_dir, fmt="jpeg"):
    from gsbag import gsbag_reader
    from j6.image_encode import boleidl_pb2

    bs, be = _bag_time_range(bag_path)
    if start_ts is not None and bs is not None and start_ts < bs:
        logger.info("start_ts %d < bag_start %d, 钳位", start_ts, bs)
        start_ts = bs
    if end_ts is not None and be is not None and end_ts > be:
        logger.info("end_ts %d > bag_end %d, 钳位", end_ts, be)
        end_ts = be

    counts = {}
    for topic in topics:
        tdir = topic.strip("/").replace("/", "_")
        out = os.path.join(output_dir, tdir)
        os.makedirs(out, exist_ok=True)
        logger.info("提取 %s [%s, %s]", topic, start_ts, end_ts)

        try:
            reader = gsbag_reader.GsBagReader(bag_path)
            reader.set_topic_filter([topic])
        except Exception as e:
            logger.error("打开 bag 失败: %s", e)
            counts[topic] = 0
            continue

        frames = []
        errs = 0
        for m in reader.read_messages():
            ts = m.timestamp
            if start_ts is not None and ts < start_ts:
                continue
            if end_ts is not None and ts > end_ts:
                continue
            try:
                msg = boleidl_pb2.Image()
                data = []
                gsbag_reader.HobotMessageSerializer.deserialize_image(m, msg, data)
                if data:
                    frames.append((ts, data[0]))
            except Exception as e:
                errs += 1
                if errs <= 5:
                    logger.warning("解码错误: %s", e)
        if errs > 5:
            logger.info("跳过 %d 个解码错误帧", errs)

        total = len(frames)
        logger.info("%s: %d 帧", topic, total)
        if total == 0:
            counts[topic] = 0
            continue

        saved = 0
        for idx, (ts, payload) in enumerate(frames):
            try:
                img = _decode(payload)
                if img is None:
                    continue
                ext = "jpg" if fmt == "jpeg" else "png"
                img.save(os.path.join(out, f"{ts}.{ext}"), fmt.upper())
                saved += 1
                if (idx + 1) % 50 == 0:
                    logger.info("  [%s] %d/%d", tdir, saved, total)
            except Exception as e:
                if saved == 0:
                    logger.warning("保存失败: %s", e)
        counts[topic] = saved
        logger.info("%s: %d 帧 → %s", topic, saved, out)
    return counts


def _decode(raw):
    try:
        import av
        c = av.open(BytesIO(raw), mode="r", format="hevc")
        for pkt in c.demux(video=0):
            for f in pkt.decode():
                return f.to_image()
        c.close()
    except ImportError:
        return _decode_ffmpeg(raw)
    except Exception:
        try:
            import av
            buf = BytesIO(raw)
            c = av.open(buf, mode="r", format="hevc")
            imgs = [f.to_image() for pkt in c.demux(video=0) for f in pkt.decode()]
            c.close()
            return imgs[0] if imgs else None
        except Exception:
            return None
    return None


def _decode_ffmpeg(raw):
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
#  任务执行
# ═══════════════════════════════════════════════════════════════════════

def _parse_time(val):
    n = int(val)
    if n < 10_000_000_000:
        logger.info("时间戳 %d → 秒，转纳秒", n)
        return n * 1_000_000_000
    return n


def _run_one(bag_id, bag_path, start_ts, end_ts, topics, output_dir, fmt, token, oss_map):
    start_ns = _parse_time(start_ts)
    end_ns = _parse_time(end_ts)
    if not topics:
        topics = DEFAULT_TOPICS

    info = {}
    if bag_path:
        _bag = bag_path
        info = {"data_id": os.path.basename(bag_path), "local_path": bag_path}
    elif bag_id:
        try:
            _bag, info = _resolve_bag_path(bag_id, token, oss_map)
            logger.info("路径: %s (vin=%s)", _bag, info.get("vin"))
        except Exception as e:
            logger.error("路径解析失败: %s", e)
            return False
    else:
        logger.error("需要 --bag_id 或 --bag_path")
        return False

    if not os.path.isdir(_bag):
        logger.error("bag 目录不存在: %s", _bag)
        return False

    if output_dir:
        _out = output_dir
    else:
        _out = os.path.join(".", "extracted_images", f"{info.get('data_id', 'unknown')}_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(_out, exist_ok=True)

    logger.info("提取: %s topics=%s [%d,%d] → %s", _bag, topics, start_ns, end_ns, _out)
    try:
        result = _extract(_bag, topics, start_ns, end_ns, _out, fmt)
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
        json.dump({"bag_path": _bag, "bag_info": info, "topics": topics,
                   "start_ts_ns": start_ns, "end_ts_ns": end_ns,
                   "result_counts": result, "total_frames": total}, f, ensure_ascii=False, indent=2)
    return True


def _run_csv(csv_path, csv_mode, topics, output_base, fmt, token, oss_map):
    if not os.path.exists(csv_path):
        logger.error("CSV 不存在: %s", csv_path)
        sys.exit(1)

    tasks = []
    with open(csv_path, encoding="utf-8") as f:
        for i, row in enumerate(csv.reader(f)):
            if not row or not row[0].strip():
                continue
            if i == 0:
                try:
                    int(row[1].strip())
                except (ValueError, IndexError):
                    continue
            if len(row) < 3:
                logger.warning("第 %d 行不足3列", i + 1)
                continue
            tasks.append({"col1": row[0].strip(), "start_ts": row[1].strip(), "end_ts": row[2].strip()})

    if not tasks:
        logger.error("CSV 无有效任务")
        sys.exit(1)

    logger.info("CSV: %d 条任务", len(tasks))
    ok = fail = 0
    for i, t in enumerate(tasks, 1):
        logger.info("\n" + "=" * 60)
        logger.info("[%d/%d] %s [%s,%s]", i, len(tasks), t["col1"], t["start_ts"], t["end_ts"])
        bid = t["col1"] if csv_mode == "id" else None
        bpath = t["col1"] if csv_mode == "path" else None
        tout = os.path.join(output_base, t["col1"]) if output_base else None
        if _run_one(bid, bpath, t["start_ts"], t["end_ts"], topics, tout, fmt, token, oss_map):
            ok += 1
        else:
            fail += 1

    logger.info("\n" + "#" * 60)
    logger.info("完成: 成功 %d / 失败 %d / 共 %d", ok, fail, len(tasks))
    logger.info("#" * 60)


# ═══════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="从 SQL 查询结果提取 Rosbag 图片帧", formatter_class=argparse.RawDescriptionHelpFormatter)
    inp = p.add_mutually_exclusive_group()
    inp.add_argument("--csv", help="CSV 文件 (批量)")
    inp.add_argument("--bag_id", help="bag_id (需 dm_sdk)")
    inp.add_argument("--bag_path", help="bag 本地路径 (无需 dm_sdk)")
    p.add_argument("--csv_mode", choices=["id", "path"], default="id", help="CSV第1列: id或path")
    p.add_argument("--start_ts", help="起始时间戳 (秒/纳秒自动识别)")
    p.add_argument("--end_ts", help="结束时间戳 (秒/纳秒自动识别)")
    p.add_argument("--topics", nargs="+", help="摄像头 topic")
    p.add_argument("--cam_names", nargs="+", help="摄像头短名: fw120 fw60 r50 等")
    p.add_argument("--output_dir", help="单条:输出目录 / CSV:输出根目录(每个bag子目录)")
    p.add_argument("--format", choices=["jpeg", "png"], default="jpeg")
    args = p.parse_args()

    _bootstrap()

    topics = args.topics
    if not topics and args.cam_names:
        topics = [CAMERA_TOPIC_MAP[n] for n in args.cam_names if n in CAMERA_TOPIC_MAP]
        bad = [n for n in args.cam_names if n not in CAMERA_TOPIC_MAP]
        if bad:
            logger.warning("未知摄像头: %s (可用: %s)", bad, list(CAMERA_TOPIC_MAP.keys()))
    if not topics:
        topics = DEFAULT_TOPICS

    token = DM_ACCESS_TOKEN
    oss_map = OSS_MOUNT_MAP

    if args.csv:
        _run_csv(args.csv, args.csv_mode, topics, args.output_dir, args.format, token, oss_map)
    else:
        if not args.bag_id and not args.bag_path:
            p.error("需要 --csv, --bag_id 或 --bag_path")
        if not args.start_ts or not args.end_ts:
            p.error("单条模式需要 --start_ts 和 --end_ts")
        ok = _run_one(args.bag_id, args.bag_path, args.start_ts, args.end_ts,
                       topics, args.output_dir, args.format, token, oss_map)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
