import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List


class QualityCheckStatus(str, Enum):
    SUCCESS = "success"
    FAIL = "fail"


class RespBody:
    """API 响应体，包含 code、msg、data、trace_id。"""

    def __init__(
        self,
        code: int = 0,
        msg: str = "",
        data: Any = None,
        trace_id: str = "",
    ):
        self.code = code
        self.data = data
        self.msg = msg
        self.trace_id = trace_id

    def resp_code(self) -> int:
        return self.code

    def resp_message(self) -> str:
        return self.msg

    def resp_data(self) -> Any:
        return self.data

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "msg": self.msg,
            "data": self.data,
            "traceid": self.trace_id,
        }

    def to_resp_body(self, data=None, filter_fields=None) -> "RespBody":
        """转换为 RespBody，支持覆盖 data 和字段过滤。

        与 WebappClient response 对象的 to_resp_body 行为一致，
        允许在已经拿到 RespBody 后继续链式调用。
        """
        resolved_data = self.data if data is None else data
        if filter_fields and isinstance(resolved_data, dict):
            resolved_data = {
                k: resolved_data[k] for k in filter_fields if k in resolved_data
            }
        return RespBody(
            code=self.code,
            msg=self.msg,
            data=resolved_data,
            trace_id=self.trace_id,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


@dataclass
class QualityCheckItem:
    """
    单个质检项。

    :param topic: 目标传感器/Topic 列表，如 ["cam_FW"]；不针对特定 topic 的全局质检项可传空列表
    :param status: 质检结果，使用 QualityCheckStatus.SUCCESS 或 QualityCheckStatus.FAIL
    :param info: 自由填写的质检详情
    """

    topic: List[str]
    status: QualityCheckStatus
    info: Dict[str, Any]

    def __post_init__(self):
        if self.topic is None:
            raise ValueError(
                "CheckItem.topic 不能为 None，不针对特定 topic 时请传空列表 []"
            )
        if not isinstance(self.status, QualityCheckStatus):
            raise ValueError(
                f"CheckItem.status 必须为 QualityCheckStatus 枚举值，收到: {self.status!r}"
            )
        if self.info is None:
            raise ValueError("CheckItem.info 不能为 None")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "topic": self.topic,
            "status": self.status.value,
            "info": self.info,
        }


@dataclass
class TopicIntegrityInfo:
    """
    topic_integrity 质检项的 info 结构。

    :param exist_topics: 实际存在的 topic 列表
    :param lost_topics: 缺失的 topic 列表
    """

    exist_topics: List[str]
    lost_topics: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exist_topics": self.exist_topics,
            "lost_topics": self.lost_topics,
        }


@dataclass
class FileIntegrityFolder:
    """
    file_integrity 质检项中单个目录的检查结果。

    :param path: 目录路径，如 "/" 或 "/camera/front"
    :param exist_files: 实际存在的文件列表
    :param lost_files: 缺失的文件列表
    """

    path: str
    exist_files: List[str]
    lost_files: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "exist_files": self.exist_files,
            "lost_files": self.lost_files,
        }


@dataclass
class FileIntegrityInfo:
    """
    file_integrity 质检项的 info 结构。

    :param folder: 各目录的文件完整性检查结果列表
    """

    folder: List[FileIntegrityFolder]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "folder": [f.to_dict() for f in self.folder],
        }


@dataclass
class QualityCheckInfo:
    """
    bag 质检信息。

    :param check_time: 质检时间，格式 "YYYYMMDDHHmmss"，如 "20260301102300"
    :param check_items: 质检项 dict，key 为质检项唯一标识（需在质检项管理表中注册）
    """

    check_time: str
    check_items: Dict[str, QualityCheckItem]

    def __post_init__(self):
        if not self.check_time:
            raise ValueError("CheckInfo.check_time 不能为空")
        if not self.check_items:
            raise ValueError("CheckInfo.check_items 不能为空")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_time": self.check_time,
            "check_items": {
                k: v.to_dict() for k, v in self.check_items.items()
            },
        }
