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


def get_bag_info(bag_path: str) -> Dict:
    if not os.path.isdir(bag_path):
        raise BagNotFoundException(bag_path)

    metadata_path, bag_file_path = _find_metadata_and_bag(bag_path)
    if not metadata_path:
        # 没有 metadata.yaml 时，仍然检测 bin 目录下的数据文件
        fusion_map_topic = None
        if os.path.isfile(os.path.join(bag_path, "bin", "gac_enviro_model_fusion_map_plus.bin")):
            fusion_map_topic = {"name": "/gac/enviro_model/fusion_map_plus", "type": "EFusionMap", "message_count": 0, "freq": 0}
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
