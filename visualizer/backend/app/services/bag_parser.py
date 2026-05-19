import os
import sys
import logging
import yaml
from typing import List, Dict, Optional

from app.core.config import settings
from app.core.exceptions import BagNotFoundException

# Ensure proto paths are available (absolute path from project root parent)
_PROTO_BASE = settings.PROJECT_ROOT.parent / "data_mining/UBM_mining/ubm_data_mining/gsbag_parser/proto/v4.8.3"
sys.path.append(str(_PROTO_BASE))
sys.path.append(str(_PROTO_BASE / "j6"))

from gsbag import gsbag_reader

logger = logging.getLogger(__name__)


def get_bag_info(bag_path: str) -> Dict:
    if not os.path.isdir(bag_path):
        raise BagNotFoundException(bag_path)

    metadata_path = os.path.join(bag_path, "metadata.yaml")
    if not os.path.exists(metadata_path):
        raise BagNotFoundException(f"metadata.yaml not found in {bag_path}")

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

    camera_topics = [t for t in topics if "/cam/" in t["name"]]
    logger.info("Bag %s loaded: %d camera topics, %d total messages", bag_path, len(camera_topics), info.get("message_count", 0))

    return {
        "bag_path": bag_path,
        "topics": camera_topics,
        "duration_sec": info.get("duration", {}).get("seconds", 0),
        "message_count": info.get("message_count", 0),
    }


def get_camera_topics(bag_path: str) -> List[str]:
    info = get_bag_info(bag_path)
    return [t["name"] for t in info["topics"]]
