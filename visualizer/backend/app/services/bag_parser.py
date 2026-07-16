import os
import sys
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
    for entry in sorted(os.listdir(bag_path)):
        sub = os.path.join(bag_path, entry)
        if os.path.isdir(sub):
            mp = os.path.join(sub, "metadata.yaml")
            if os.path.exists(mp):
                bag_file = os.path.join(sub, "bag.bag") if os.path.isfile(os.path.join(sub, "bag.bag")) else None
                return mp, bag_file
    
    return None, None


def _extract_bag_id(bag_path: str) -> Optional[str]:
    """从 bag_path 中提取可能的 bag_id。
    
    bag_id 的特征：不含 / 且长度 > 10（如 1002AePBU4WlfnBzNtDbBu202606）
    或者路径的最后一个目录名看起来像 bag_id。
    """
    # 如果路径直接就是一个 bag_id（不含 / 且长度够长）
    if "/" not in bag_path and len(bag_path) > 10:
        return bag_path
    # 取路径最后一个目录名
    basename = os.path.basename(bag_path.rstrip("/"))
    # bag_id 特征：包含数字+字母混合，长度 > 10
    if len(basename) > 10 and re.match(r"^[a-zA-Z0-9]+$", basename):
        return basename
    # 也支持 .db 后缀的 bag_id（如 1002AePBU4WlfnBzNtDbBu202606.db）
    if basename.endswith(".db") and len(basename) > 14:
        return basename[:-3]
    return None


def _resolve_via_dm_sdk(bag_path: str) -> Optional[Dict]:
    """通过 dm_sdk 查询原始 rosbag 路径 + camera topics。
    
    当本地路径没有 metadata.yaml 时，尝试：
    1. 从 bag_path 中提取 bag_id
    2. 用 dm_sdk RosbagPathResolver 查询原始 rosbag OSS 路径
    3. 从原始表 metadata 中提取 camera topics
    
    Returns: bag_info dict (如果成功), None (如果失败或无法解析)
    """
    import re as _re
    
    bag_id = _extract_bag_id(bag_path)
    if not bag_id:
        return None
    
    try:
        from tools.rosbag_path_resolver import RosbagPathResolver
        from app.core.config import settings
        from dm_sdk import ProdDataClient, RawDataClient
        
        token = settings.DM_ACCESS_TOKEN
        if not token:
            logger.warning("DM_ACCESS_TOKEN not configured, cannot resolve bag_id=%s", bag_id)
            return None
        
        # Step 1: 查产线表获取 origin 信息
        prod_client = ProdDataClient(access_token=token, table=settings.DM_PROD_TABLE)
        resp = prod_client.get_bag_metadata(data_id=bag_id)
        if resp.resp_code() != 200:
            logger.warning("dm_sdk prod query failed for bag_id=%s: %s", bag_id, resp.msg())
            return None
        
        prod_data = resp.resp_data()
        origins = prod_data.get("origins", [])
        if not origins:
            logger.warning("bag_id=%s has no origins", bag_id)
            return None
        
        origin = origins[0]
        origin_table = origin.get("table")
        origin_bag_id = origin.get("bag_id")
        
        # Step 2: 查原始表获取 rosbag metadata
        raw_client = RawDataClient(access_token=token, table=origin_table)
        raw_resp = raw_client.get_bag_metadata(bag_id=origin_bag_id)
        if raw_resp.resp_code() != 200:
            logger.warning("dm_sdk raw query failed for origin_bag_id=%s: %s", origin_bag_id, raw_resp.msg())
            return None
        
        raw_data = raw_resp.resp_data()
        oss_path = raw_data.get("storage_prefix") or raw_data.get("raw_storage_prefix")
        
        # Step 3: 从 raw_data.topics 中提取 camera topics
        raw_topics = raw_data.get("topics", {})
        camera_topics = []
        for name, info in raw_topics.items():
            if not isinstance(info, dict):
                continue
            group = info.get("group", "")
            if group == "camera" or "/cam/" in name or "/camera/" in name:
                camera_topics.append({
                    "name": name,
                    "type": "encoded",
                    "message_count": info.get("frame_num", 0),
                    "freq": 0,  # dm_sdk 不直接提供 freq
                })
        
        # Step 4: 检测本地是否有 rosbag（通过 OSS_MOUNT_MAP）
        rosbag_local_path = None
        mount_map_str = settings.OSS_MOUNT_MAP
        if mount_map_str and oss_path:
            try:
                from tools.rosbag_path_resolver import _parse_oss_mount_map, _oss_to_local
                mount_map = _parse_oss_mount_map(mount_map_str)
                rosbag_local_path = _oss_to_local(oss_path, mount_map)
                if rosbag_local_path and not os.path.exists(rosbag_local_path):
                    rosbag_local_path = None
            except Exception:
                pass
        
        # 解析时间范围
        start_ts = prod_data.get("start_timestamp")
        end_ts = prod_data.get("end_timestamp")
        duration_sec = prod_data.get("duration", 0)
        
        # 检测 em bin 本地路径（用于 fusion_map）
        em_bin_oss = prod_data.get("storage_prefix")
        em_bin_local = None
        if mount_map_str and em_bin_oss:
            try:
                from tools.rosbag_path_resolver import _parse_oss_mount_map, _oss_to_local
                mm = _parse_oss_mount_map(mount_map_str)
                em_bin_local = _oss_to_local(em_bin_oss, mm)
                if em_bin_local and not os.path.exists(em_bin_local):
                    em_bin_local = None
            except Exception:
                pass
        
        # 检测 fusion_map_topic（em bin 本地路径或原 bag_path）
        fusion_map_topic = None
        check_path = em_bin_local or bag_path
        if os.path.isfile(os.path.join(check_path, "bin", "gac_enviro_model_fusion_map_plus.bin")):
            fusion_map_topic = {"name": "/gac/enviro_model/fusion_map_plus", "type": "EFusionMap", "message_count": 0, "freq": 0}
        
        logger.info("Resolved bag_id=%s via dm_sdk: %d camera topics, rosbag_oss=%s, rosbag_local=%s",
                     bag_id, len(camera_topics), oss_path, rosbag_local_path)
        
        return {
            "bag_path": bag_path,
            "bag_id": bag_id,
            "rosbag_path": rosbag_local_path,
            "rosbag_oss_path": oss_path,
            "topics": camera_topics,
            "fusion_map_topic": fusion_map_topic,
            "duration_sec": duration_sec,
            "message_count": sum(t["message_count"] for t in camera_topics),
            "start_time_ns": start_ts,
            "end_time_ns": end_ts,
        }
        
    except Exception as exc:
        logger.warning("Failed to resolve bag_id=%s via dm_sdk: %s", bag_id, exc)
        return None


def get_bag_info(bag_path: str) -> Dict:
    if not os.path.isdir(bag_path):
        raise BagNotFoundException(bag_path)

    metadata_path, bag_file_path = _find_metadata_and_bag(bag_path)
    if not metadata_path:
        # 没有 metadata.yaml — 尝试通过 dm_sdk 查询原始 rosbag 信息
        dm_result = _resolve_via_dm_sdk(bag_path)
        fusion_map_topic = None
        if os.path.isfile(os.path.join(bag_path, "bin", "gac_enviro_model_fusion_map_plus.bin")):
            fusion_map_topic = {"name": "/gac/enviro_model/fusion_map_plus", "type": "EFusionMap", "message_count": 0, "freq": 0}

        if dm_result:
            return dm_result

        return {
            "bag_path": bag_path,
            "rosbag_path": None,
            "topics": [],
            "fusion_map_topic": fusion_map_topic,
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
    logger.info("Bag %s loaded: %d camera topics, %d total messages", bag_path, len(camera_topics), info.get("message_count", 0))

    # 解析 bag 起止时间戳（纳秒），用于 clamp 视频提取范围
    duration_sec = info.get("duration", {}).get("seconds", 0)
    start_time_ns = None
    end_time_ns = None

    # metadata.yaml 中 start_time 通常是 {seconds: N, nanoseconds: N} 格式
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
        "bag_path": bag_path,
        "rosbag_path": bag_file_path,
        "topics": camera_topics,
        "fusion_map_topic": fusion_map_topic,
        "duration_sec": duration_sec,
        "message_count": info.get("message_count", 0),
        "start_time_ns": start_time_ns,
        "end_time_ns": end_time_ns,
    }


def get_camera_topics(bag_path: str) -> List[str]:
    info = get_bag_info(bag_path)
    return [t["name"] for t in info["topics"]]
