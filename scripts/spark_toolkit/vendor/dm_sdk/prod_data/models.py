from dataclasses import dataclass
from typing import Any, Dict, List

# 数据类型常量
DATA_TYPE_BAG = "bag"
DATA_TYPE_CLIP = "clip"
DATA_TYPE_VIRTUAL_CLIP = "virtual-clip"


@dataclass
class SubFile:
    """表示 topic 中的单个文件"""

    file_key: str
    path: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "file_key": self.file_key,
            "path": self.path,
        }


@dataclass
class TopicRecord:
    topic: str
    type: str
    metadata: Dict[str, Any]
    sub_files: List[SubFile] = None
    folder_path: str = ""

    def __post_init__(self):
        if not self.sub_files and not self.folder_path:
            raise ValueError("sub_files 和 folder_path 至少需要传递一个")

    def to_dict(self) -> Dict[str, Any]:
        """返回单键 dict，键为 self.topic，值为该 topic 的 name/type/metadata/sub_files。"""
        return {
            self.topic: {
                "name": self.topic,
                "type": self.type,
                "metadata": self.metadata,
                "sub_files": [f.to_dict() for f in self.sub_files]
                if self.sub_files
                else [],
                "folder_path": self.folder_path,
            }
        }


@dataclass
class FrameTopicRecord:
    timestamp: int
    topics: List[TopicRecord]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "topics": {t.topic: t.to_dict()[t.topic] for t in self.topics},
        }
        return payload


@dataclass
class MergeLabelItem:
    """表示融合标注中的单条标注记录"""

    version: str
    source_record_id: str
    label_type: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "version": self.version,
            "source_record_id": self.source_record_id,
            "label_type": self.label_type,
        }


@dataclass
class UploadFileItem:
    """表示要上传的单个文件信息"""

    local_path: str
    remote_path: str
    file_key: str
    topic: str = "other"
    topic_type: str = "other"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "local": self.local_path,
            "remote": self.remote_path,
            "file_key": self.file_key,
            "topic": self.topic,
            "topic_type": self.topic_type,
        }
