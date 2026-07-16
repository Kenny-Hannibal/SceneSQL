import os
import re
import logging
import yaml
from typing import List, Dict, Optional

from app.core.config import settings
from app.core.exceptions import BagNotFoundException

# gsbag SDK — 可选依赖（本机无 gsbag 时优雅降级）
try:
    from gsbag import gsbag_reader
    _HAS_GSBAG = True
except ImportError:
    gsbag_reader = None
    _HAS_GSBAG = False

logger = logging.getLogger(__name__)


# ── bag_id 提取 ──

def _extract_bag_id(input_str: str) -> Optional[str]:
    """从输入字符串中提取 bag_id。

    bag_id 特征：纯字母数字，长度 > 10（如 1002AePBU4WlfnBzNtDbBu202606）
    - 直接输入 bag_id（不含 /）
    - 路径最后一个目录名像 bag_id
    - .db 后缀的 bag_id
    """
    if not input_str:
        return None
    if "/" not in input_str and len(input_str) > 10 and re.match(r"^[a-zA-Z0-9]+$", input_str):
        return input_str
    basename = os.path.basename(input_str.rstrip("/"))
    if len(basename) > 10 and re.match(r"^[a-zA-Z0-9]+$", basename):
        return basename
    if basename.endswith(".db") and len(basename) > 14:
        return basename[:-3]
    return None


# ── dm_sdk 双路径解析 ──

def _resolve_dual_paths_via_dm(bag_id: str) -> Optional[Dict]:
    """通过 dm_sdk 分别查询 em bin 路径（BEV 用）和 rosbag 路径（camera 用）。

    流程：
    1. ProdDataClient 查产线表 → em bin 的 storage_prefix（em bin 本地挂载路径）
    2. origins → RawDataClient 查原始表 → rosbag 的 storage_prefix + camera topics
    3. 通过 OSS_MOUNT_MAP 将 OSS 路径映射为本地挂载路径

    Returns: {
        "bag_id": str,
        "em_bin_path": str or None,      # em bin 本地路径（BEV 3D 用）
        "rosbag_path": str or None,       # rosbag 本地路径（camera 用）
        "rosbag_oss_path": str or None,   # rosbag OSS 路径（备下载用）
        "em_bin_oss_path": str or None,   # em bin OSS 路径
        "camera_topics": list,            # camera topic 列表
        "fusion_map_topic": dict or None, # fusion_map_plus topic
        "duration_sec": float,
        "start_time_ns": int or None,
        "end_time_ns": int or None,
    }
    """
    try:
        from dm_sdk import ProdDataClient, RawDataClient

        token = settings.DM_ACCESS_TOKEN
        if not token:
            logger.warning("DM_ACCESS_TOKEN not configured, cannot resolve bag_id=%s", bag_id)
            return None

        # ── Step 1: 查产线表 → em bin 路径 ──
        prod_client = ProdDataClient(access_token=token, table=settings.DM_PROD_TABLE)
        resp = prod_client.get_bag_metadata(data_id=bag_id)
        if resp.resp_code() != 200:
            logger.warning("dm_sdk prod query failed for bag_id=%s: %s", bag_id, resp.msg())
            return None

        prod_data = resp.resp_data()

        # em bin OSS 路径 → 本地挂载
        em_bin_oss = prod_data.get("storage_prefix", "")
        em_bin_local = _oss_to_local_path(em_bin_oss) if em_bin_oss else None

        # 检测 fusion_map_topic
        fusion_map_topic = None
        check_path = em_bin_local
        if check_path and os.path.isfile(os.path.join(check_path, "bin", "gac_enviro_model_fusion_map_plus.bin")):
            fusion_map_topic = {"name": "/gac/enviro_model/fusion_map_plus", "type": "EFusionMap", "message_count": 0, "freq": 0}

        # ── Step 2: 查原始表 → rosbag 路径 + camera topics ──
        origins = prod_data.get("origins", [])
        rosbag_local = None
        rosbag_oss = None
        camera_topics = []

        if origins:
            origin = origins[0]
            origin_table = origin.get("table")
            origin_bag_id = origin.get("bag_id")

            raw_client = RawDataClient(access_token=token, table=origin_table)
            raw_resp = raw_client.get_bag_metadata(bag_id=origin_bag_id)
            if raw_resp.resp_code() != 200:
                logger.warning("dm_sdk raw query failed for origin_bag_id=%s: %s", origin_bag_id, raw_resp.msg())
            else:
                raw_data = raw_resp.resp_data()

                # rosbag OSS 路径 → 本地挂载
                rosbag_oss = raw_data.get("storage_prefix") or raw_data.get("raw_storage_prefix", "")
                if rosbag_oss:
                    rosbag_local = _oss_to_local_path(rosbag_oss)

                # 从 raw_data.topics 提取 camera topics
                raw_topics = raw_data.get("topics", {})
                for name, info in raw_topics.items():
                    if not isinstance(info, dict):
                        continue
                    group = info.get("group", "")
                    if group == "camera" or "/cam/" in name or "/camera/" in name:
                        camera_topics.append({
                            "name": name,
                            "type": "encoded",
                            "message_count": info.get("frame_num", 0),
                            "freq": 0,
                        })

        # 时间范围
        start_ts = prod_data.get("start_timestamp")
        end_ts = prod_data.get("end_timestamp")
        duration_sec = prod_data.get("duration", 0)

        logger.info(
            "Resolved bag_id=%s via dm_sdk: em_bin_local=%s, rosbag_local=%s, %d camera topics",
            bag_id, em_bin_local, rosbag_local, len(camera_topics),
        )

        return {
            "bag_id": bag_id,
            "em_bin_path": em_bin_local,
            "rosbag_path": rosbag_local,
            "rosbag_oss_path": rosbag_oss or None,
            "em_bin_oss_path": em_bin_oss or None,
            "camera_topics": camera_topics,
            "fusion_map_topic": fusion_map_topic,
            "duration_sec": duration_sec,
            "start_time_ns": start_ts,
            "end_time_ns": end_ts,
        }

    except Exception as exc:
        logger.warning("Failed to resolve bag_id=%s via dm_sdk: %s", bag_id, exc)
        return None


def _oss_to_local_path(oss_path: str) -> Optional[str]:
    """将 OSS 路径转换为本地挂载路径（通过 OSS_MOUNT_MAP 配置）。"""
    mount_map_str = getattr(settings, "OSS_MOUNT_MAP", "")
    if not mount_map_str or not oss_path:
        return None
    try:
        from tools.rosbag_path_resolver import _parse_oss_mount_map, _oss_to_local
        mount_map = _parse_oss_mount_map(mount_map_str)
        local = _oss_to_local(oss_path, mount_map)
        if local and os.path.exists(local):
            return local
    except Exception:
        pass
    return None


# ── 本地 rosbag 路径解析（兼容旧流程：直接输入本地 rosbag 目录） ──

def _find_metadata_and_bag(bag_path: str) -> tuple:
    """查找 metadata.yaml 和 bag.bag 路径。

    bag_path 可能是:
    - 直接包含 metadata.yaml 的目录（rosbag目录）
    - 包含子目录的父目录（em bin目录，子目录里有 metadata.yaml + bag.bag）

    Returns: (metadata_path, bag_file_path or None)
    """
    metadata_path = os.path.join(bag_path, "metadata.yaml")
    if os.path.exists(metadata_path):
        bag_file = os.path.join(bag_path, "bag.bag") if os.path.isfile(os.path.join(bag_path, "bag.bag")) else None
        return metadata_path, bag_file

    # 搜索一级子目录
    if os.path.isdir(bag_path):
        for entry in sorted(os.listdir(bag_path)):
            sub = os.path.join(bag_path, entry)
            if os.path.isdir(sub):
                mp = os.path.join(sub, "metadata.yaml")
                if os.path.exists(mp):
                    bag_file = os.path.join(sub, "bag.bag") if os.path.isfile(os.path.join(sub, "bag.bag")) else None
                    return mp, bag_file

    return None, None


def _parse_local_rosbag(bag_path: str) -> Dict:
    """解析本地 rosbag 目录（含 metadata.yaml）。

    这是旧流程：用户直接输入一个 rosbag 本地路径，从 metadata.yaml 读取 topics。
    """
    metadata_path, bag_file_path = _find_metadata_and_bag(bag_path)

    if not metadata_path:
        return {
            "em_bin_path": None,
            "rosbag_path": None,
            "topics": [],
            "fusion_map_topic": None,
            "duration_sec": 0,
            "message_count": 0,
            "start_time_ns": None,
            "end_time_ns": None,
        }

    with open(metadata_path, "r") as f:
        meta = yaml.safe_load(f)

    info = meta.get("gacbag_bagfile_information", {})
    topics = []
    for t in info.get("topics_with_message_count", []):
        tm = t.get("topic_metadata", {})
        topics.append({
            "name": tm.get("name"),
            "type": tm.get("type"),
            "message_count": t.get("message_count", 0),
            "freq": t.get("message_freq", 0),
        })

    camera_topics = [t for t in topics if "/camera" in t["name"] or "/cam/" in t["name"]]
    fusion_map_topic = next((t for t in topics if "fusion_map_plus" in t["name"]), None)

    # 如果 metadata.yaml 没有 fusion_map_plus topic，但 bin 目录下有对应文件，也标记为有数据
    if fusion_map_topic is None and os.path.isfile(os.path.join(bag_path, "bin", "gac_enviro_model_fusion_map_plus.bin")):
        fusion_map_topic = {"name": "/gac/enviro_model/fusion_map_plus", "type": "EFusionMap", "message_count": 0, "freq": 0}

    logger.info("Local rosbag %s: %d camera topics, %d total messages", bag_path, len(camera_topics), info.get("message_count", 0))

    # 解析时间范围
    duration_sec = info.get("duration", {}).get("seconds", 0)
    start_time_ns = None
    end_time_ns = None
    start_time_obj = info.get("start_time")
    if start_time_obj and isinstance(start_time_obj, dict):
        s = start_time_obj.get("seconds", 0) or 0
        ns = start_time_obj.get("nanoseconds", 0) or 0
        start_time_ns = int(s) * 1_000_000_000 + int(ns)
    elif start_time_obj and isinstance(start_time_obj, (int, float)):
        start_time_ns = int(start_time_obj)
    if start_time_ns is not None:
        end_time_ns = start_time_ns + int(duration_sec * 1_000_000_000)

    return {
        "em_bin_path": None,  # 本地 rosbag 流程没有 em bin 路径
        "rosbag_path": bag_file_path or bag_path,
        "topics": camera_topics,
        "fusion_map_topic": fusion_map_topic,
        "duration_sec": duration_sec,
        "message_count": info.get("message_count", 0),
        "start_time_ns": start_time_ns,
        "end_time_ns": end_time_ns,
    }


# ── 主入口 ──

def get_bag_info(bag_path: str) -> Dict:
    """解析 bag 信息，返回双路径（em_bin_path + rosbag_path）。

    逻辑：
    1. 输入是 bag_id → dm_sdk 分别查 em bin 路径和 rosbag 路径
    2. 输入是本地 rosbag 路径（含 metadata.yaml）→ 直接解析
    3. 输入是本地 em bin 路径（含 bin/fusion_map_plus.bin）→ 检测 fusion_map，尝试从目录名提取 bag_id 走 dm_sdk
    """
    # ── 判断是否为 bag_id ──
    bag_id = _extract_bag_id(bag_path)
    is_bag_id = (bag_id is not None) and ("/" not in bag_path)

    if is_bag_id:
        # 直接输入 bag_id → dm_sdk 双路径解析
        dm_result = _resolve_dual_paths_via_dm(bag_id)
        if dm_result:
            return {
                "bag_path": bag_path,
                "bag_id": dm_result["bag_id"],
                "em_bin_path": dm_result["em_bin_path"],
                "rosbag_path": dm_result["rosbag_path"],
                "rosbag_oss_path": dm_result.get("rosbag_oss_path"),
                "em_bin_oss_path": dm_result.get("em_bin_oss_path"),
                "topics": dm_result["camera_topics"],
                "fusion_map_topic": dm_result["fusion_map_topic"],
                "duration_sec": dm_result["duration_sec"],
                "message_count": sum(t["message_count"] for t in dm_result["camera_topics"]),
                "start_time_ns": dm_result["start_time_ns"],
                "end_time_ns": dm_result["end_time_ns"],
            }
        # dm_sdk 失败 → 返回空结果
        return {
            "bag_path": bag_path,
            "bag_id": bag_id,
            "em_bin_path": None,
            "rosbag_path": None,
            "topics": [],
            "fusion_map_topic": None,
            "duration_sec": 0,
            "message_count": 0,
            "start_time_ns": None,
            "end_time_ns": None,
        }

    # ── 输入是本地路径 ──
    if not os.path.isdir(bag_path):
        raise BagNotFoundException(bag_path)

    # 检测是否有 metadata.yaml（本地 rosbag 流程）
    metadata_path, _ = _find_metadata_and_bag(bag_path)
    if metadata_path:
        result = _parse_local_rosbag(bag_path)
        result["bag_path"] = bag_path
        result["bag_id"] = None
        return result

    # 没有 metadata.yaml — 可能是 em bin 目录
    # 检测 fusion_map_topic
    fusion_map_topic = None
    if os.path.isfile(os.path.join(bag_path, "bin", "gac_enviro_model_fusion_map_plus.bin")):
        fusion_map_topic = {"name": "/gac/enviro_model/fusion_map_plus", "type": "EFusionMap", "message_count": 0, "freq": 0}

    # 尝试从目录名提取 bag_id 走 dm_sdk
    if bag_id:
        dm_result = _resolve_dual_paths_via_dm(bag_id)
        if dm_result:
            return {
                "bag_path": bag_path,
                "bag_id": dm_result["bag_id"],
                "em_bin_path": dm_result["em_bin_path"] or bag_path,  # 如果 dm_sdk 没返回 em_bin，就用当前本地路径
                "rosbag_path": dm_result["rosbag_path"],
                "rosbag_oss_path": dm_result.get("rosbag_oss_path"),
                "em_bin_oss_path": dm_result.get("em_bin_oss_path"),
                "topics": dm_result["camera_topics"],
                "fusion_map_topic": dm_result["fusion_map_topic"] or fusion_map_topic,
                "duration_sec": dm_result["duration_sec"],
                "message_count": sum(t["message_count"] for t in dm_result["camera_topics"]),
                "start_time_ns": dm_result["start_time_ns"],
                "end_time_ns": dm_result["end_time_ns"],
            }

    # 纯本地 em bin 目录，无法查 rosbag
    return {
        "bag_path": bag_path,
        "bag_id": bag_id,
        "em_bin_path": bag_path,
        "rosbag_path": None,
        "topics": [],
        "fusion_map_topic": fusion_map_topic,
        "duration_sec": 0,
        "message_count": 0,
        "start_time_ns": None,
        "end_time_ns": None,
    }


def get_camera_topics(bag_path: str) -> List[str]:
    info = get_bag_info(bag_path)
    return [t["name"] for t in info["topics"]]
