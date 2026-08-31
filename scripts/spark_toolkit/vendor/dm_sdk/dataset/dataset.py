import os
from typing import Any, Dict, List, Optional

from dm_sdk.models import RespBody
from dm_sdk.tools.api import ApiBaseError
from dm_sdk.tools.es_query_builder import condition_to_str
from dm_sdk.tools.webapp_client import WebappClient

from ._csv_utils import (
    list_csv_files,
    upload_csv_files_to_oss,
)
from .models import ClipMatchMode, MemberSource


def create_dataset(
    self,
    name: str,
    table: Optional[str] = None,
    category: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    version_metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    member_ids: Optional[List[str]] = None,
    members: Optional[List[Dict[str, Any]]] = None,
    condition: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    member_source: MemberSource = MemberSource.DEFAULT,
    csv_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a dataset
    Args:
        name: Dataset name
        category: Category (e.g., training/evaluation/...)
        description: Description
        table: Table name
        metadata: Metadata
        tags: Tags
        member_ids: Member list
        members: Member list with detail
        condition: Search condition (used when building dataset async based on conditions)
        member_source: Which field of matched members to use as dataset members.
            Options: MemberSource.DEFAULT, MemberSource.ORIGIN, MemberSource.PARENT.
        csv_dir: Local directory containing CSV files. When provided, uploads
            all CSV files to OSS and updates dataset status to ready.

    Returns:
        Dict containing id, status, status_str
    """
    if not name:
        raise ApiBaseError("创建数据集时name不能为空")

    provided = [
        member_ids is not None,
        condition is not None,
        members is not None,
        csv_dir is not None,
    ]
    if not any(provided):
        raise ApiBaseError(
            "member_ids、condition、members和csv_dir至少提供一个"
        )
    if sum(provided) > 1:
        raise ApiBaseError(
            "member_ids、condition、members和csv_dir只能四选一"
        )

    if csv_dir is not None:
        csv_files = list_csv_files(csv_dir)
        if not csv_files:
            raise ApiBaseError(
                f"csv_dir 下没有找到 csv 文件: {csv_dir}"
            )

    payload: Dict[str, Any] = {
        "name": name,
        "category": category,
        "description": description,
        "table": table or self.table,
    }

    if metadata is not None:
        payload["metadata"] = metadata
    if version_metadata is not None:
        payload["version_metadata"] = version_metadata
    if tags is not None:
        payload["tags"] = tags
    if members is not None:
        payload["members"] = members
    if member_ids is not None:
        payload["member_ids"] = member_ids
    if condition is not None:
        payload["condition"] = condition
        payload["condition_query_str"] = condition_to_str(condition)
        if limit is not None:
            payload["limit"] = limit
        if member_source != MemberSource.DEFAULT:
            payload["member_source"] = member_source

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/add", json=payload
    )
    if resp.get("code", 0) != 200:
        raise ApiBaseError(
            f"创建数据集失败: {resp.get('message', '')} (name={name!r})",
            trace_id=resp.trace_id,
        )

    result = resp.get("data") or {}

    # csv_dir 模式：上传 CSV 并更新状态
    if csv_dir is not None:
        dataset_version_id = result.get("dataset_version_id")
        member_location_prefix = result.get("member_location_prefix", "")
        if not dataset_version_id:
            raise ApiBaseError("创建数据集成功但未返回 dataset_version_id")
        if not member_location_prefix:
            raise ApiBaseError(
                "创建数据集成功但未返回 member_location_prefix"
            )

        total = upload_csv_files_to_oss(
            self._oss_tool,
            self._bucket_name,
            member_location_prefix,
            csv_files,
        )

        stats_payload = {
            "total": total,
            "status": "ready",
            "dataset_version_id": dataset_version_id,
        }
        stats_resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/dataset/update-version-stats",
            json=stats_payload,
        )
        if stats_resp.get("code", 0) != 200:
            raise ApiBaseError(
                f"更新数据集状态失败: {stats_resp.get('message', '')} "
                f"(dataset_version_id={dataset_version_id})",
                trace_id=stats_resp.trace_id,
            )

    return result


def update_dataset(
    self,
    dataset_id: Optional[str] = None,
    name: Optional[str] = None,
    category: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
) -> RespBody:
    """
    Update dataset
    Args:
        dataset_id: Dataset ID
        name: Dataset name, when both id and name are provided, id takes precedence
        description: Description
        metadata: Metadata
        tags: Tags

    Returns:
        Whether the update was successful
    """
    if not dataset_id and not name:
        raise ApiBaseError("dataset_id和name至少提供一个")

    payload: Dict[str, Any] = {}
    if dataset_id:
        payload["dataset_id"] = dataset_id
    if name:
        payload["name"] = name
    if category is not None:
        payload["category"] = category
    if description is not None:
        payload["description"] = description
    if metadata is not None:
        payload["metadata"] = metadata
    if tags is not None:
        payload["tags"] = tags

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/update", json=payload
    )
    return resp.to_resp_body()


def update_version(
    self,
    dataset_version_id: Optional[str] = None,
    name: Optional[str] = None,
    version: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> RespBody:
    """
    Update dataset
    Args:
        dataset_version_id: Dataset version ID
        name: Dataset name, when both id and name are provided, id takes precedence
        version: Version number
        description: Description
        metadata: Metadata

    Returns:
        Whether the update was successful
    """
    if not dataset_version_id and (not name or not version):
        raise ApiBaseError("dataset_version_id和(name, version)至少提供一组")

    payload: Dict[str, Any] = {}
    if dataset_version_id:
        payload["dataset_version_id"] = dataset_version_id
    if name:
        payload["name"] = name
    if version:
        payload["version"] = version
    if metadata is not None:
        payload["metadata"] = metadata

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/update-version", json=payload
    )
    return resp.to_resp_body()


def get_dataset(
    self,
    dataset_id: Optional[str] = None,
    name: Optional[str] = None,
) -> RespBody:
    """
    Get dataset detail.

    Args:
        dataset_id: Dataset ID, takes precedence if provided
        name: Dataset name

    Returns:
        Dataset details including name, category, description, metadata, table, tags, etc.
    """
    if not dataset_id and not name:
        raise ApiBaseError("dataset_id和name至少提供一个")

    payload: Dict[str, Any] = {}
    if dataset_id:
        payload["dataset_id"] = dataset_id
    if name:
        payload["name"] = name

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/detail", json=payload
    )
    return resp.to_resp_body()


def get_version(
    self,
    dataset_version_id: Optional[str] = None,
    name: Optional[str] = None,
    version: Optional[int] = None,
) -> RespBody:
    """
    Get dataset version detail.

    Args:
        dataset_version_id: Dataset version ID, takes precedence if provided
        name: Dataset name
        version: Version number, query by name + version

    Returns:
        Dataset details including name, category, description, metadata, table, tags, etc.
    """
    if not dataset_version_id and (not name or not version):
        raise ApiBaseError("dataset_version_id和(name, version)至少提供一组")

    payload: Dict[str, Any] = {}
    if dataset_version_id:
        payload["dataset_version_id"] = dataset_version_id
    if name:
        payload["name"] = name
    if version:
        payload["version"] = version

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/version-detail", json=payload
    )
    return resp.to_resp_body()


def get_latest_version(self, name: str) -> RespBody:
    """
    Get the latest version of a dataset

    Args:
        name: Dataset name

    Returns:
        Dataset details of the latest version
    """
    if not name:
        raise ApiBaseError("name不能为空")

    payload = {"name": name}
    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/latest", json=payload
    )
    return resp.to_resp_body()


def get_dataset_members(
    self,
    dataset_version_id: Optional[str] = None,
    name: Optional[str] = None,
    version: Optional[int] = None,
    page: int = 1,
    size: int = 50,
) -> RespBody:
    """
    Get dataset members (paginated).

    Args:
        dataset_version_id: Dataset version ID (use either id or name+version).
        name: Dataset name.
        version: Version number.
        page: Page number, 1-based.
        size: Page size.

    Returns:
        Paginated result with total, currentPage, pageSize, totalPages, data.
    """
    if not dataset_version_id and (not name or not version):
        raise ApiBaseError("dataset_version_id和(name, version)至少提供一组")

    payload: Dict[str, Any] = {"page": page, "size": size}
    if dataset_version_id:
        payload["dataset_version_id"] = dataset_version_id
    if name:
        payload["name"] = name
    if version:
        payload["version"] = version

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/members", json=payload
    )
    return resp.to_resp_body()


def add_dataset_members(
    self,
    member_ids: Optional[List[str]] = None,
    members: Optional[List[Dict[str, Any]]] = None,
    dataset_version_id: Optional[str] = None,
    name: Optional[str] = None,
    version: Optional[int] = None,
) -> RespBody:
    """
    Add members to a dataset version.

    Args:
        member_ids: List of member IDs to add.
        members: List of member dicts to add, each containing member_id and
            member_metadata. When provided, takes precedence over member_ids.
        dataset_version_id: Dataset version ID (use either id or name+version).
        name: Dataset name.
        version: Version number.

    Returns:
        Whether the operation succeeded.
    """
    if not dataset_version_id and (not name or not version):
        raise ApiBaseError("dataset_version_id和(name, version)至少提供一组")

    if not member_ids and not members:
        raise ApiBaseError("member_ids和members至少提供一个")

    payload: Dict[str, Any] = {"operate_type": "add"}
    if members is not None:
        payload["members"] = members
    elif member_ids is not None:
        payload["member_ids"] = member_ids
    if dataset_version_id:
        payload["dataset_version_id"] = dataset_version_id
    if name:
        payload["name"] = name
    if version:
        payload["version"] = version

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/update-members", json=payload
    )
    return resp.to_resp_body()


def remove_dataset_members(
    self,
    member_ids: List[str],
    dataset_version_id: Optional[str] = None,
    name: Optional[str] = None,
    version: Optional[int] = None,
) -> RespBody:
    """
    Remove members from a dataset version.

    Args:
        member_ids: List of member IDs to remove.
        dataset_version_id: Dataset version ID (use either id or name+version).
        name: Dataset name.
        version: Version number.

    Returns:
        Whether the operation succeeded.
    """
    return _update_members(
        self,
        member_ids=member_ids,
        operate_type="delete",
        dataset_version_id=dataset_version_id,
        name=name,
        version=version,
    )


def _update_members(
    self,
    member_ids: List[str],
    operate_type: str,
    dataset_version_id: Optional[str] = None,
    name: Optional[str] = None,
    version: Optional[int] = None,
) -> RespBody:
    if not dataset_version_id and (not name or not version):
        raise ApiBaseError("dataset_version_id和(name, version)至少提供一组")

    if not member_ids:
        raise ApiBaseError("member_ids不能为空")

    payload: Dict[str, Any] = {
        "member_ids": member_ids,
        "operate_type": operate_type,
    }
    if dataset_version_id:
        payload["dataset_version_id"] = dataset_version_id
    if name:
        payload["name"] = name
    if version:
        payload["version"] = version

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/update-members", json=payload
    )
    return resp.to_resp_body()


def create_new_version(
    self,
    name: str,
    version: int,
    metadata: Optional[Dict[str, Any]] = None,
    member_ids: Optional[List[str]] = None,
    members: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Create a new dataset version.

    Args:
        name: Dataset name.
        version: New version number.
        description: Description.
        metadata: Metadata.
        member_ids: List of member IDs.

    Returns:
        Dict containing id, status, status_str.
    """
    if not name or not version:
        raise ApiBaseError("name和version都不能为空")
    if member_ids is None and members is None:
        raise ApiBaseError("member_ids和members至少提供一个")
    if member_ids is not None and members is not None:
        raise ApiBaseError("member_ids和members只能提供一个")

    payload: Dict[str, Any] = {
        "name": name,
        "version": version,
    }
    if metadata is not None:
        payload["metadata"] = metadata
    if members is not None:
        payload["members"] = members
    if member_ids is not None:
        payload["member_ids"] = member_ids

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/add-version", json=payload
    )
    if resp.get("code", 0) != 200:
        raise ApiBaseError(
            f"创建数据集版本失败: {resp.get('message', '')} (name={name!r}, version={version})",
            trace_id=resp.trace_id,
        )
    return resp.get("data") or {}


def load_to_cpfs(
    self, dataset_version_id: str, file_system_id: Optional[str] = None
) -> RespBody:
    """
    Load dataset to CPFS
    :param dataset_version_id: dataset version id
    :param file_system_id: CPFS file system id

    Returns:
        {"code": 200, "message": "", "data": { "request_id": "", "task_id": "" } }
    """
    payload: Dict[str, Any] = {
        "dataset_version_id": dataset_version_id,
    }
    if file_system_id is not None:
        payload["file_system_id"] = file_system_id
    resp = self._webapp_client.do_request(
        WebappClient.POST, "/dataset/load_to_cpfs", json=payload
    )

    return resp.to_resp_body()


def find_matching_clips(
    self,
    source_dataset_version_id: str,
    target_dataset_version_id: str,
    query_clip_file: str,
    mode: ClipMatchMode = ClipMatchMode.BIN_STRICT,
) -> RespBody:
    """
    在目标数据集中，找目标 clip id 的同源成员

    :param source_dataset_version_id: 源数据集版本 ID
    :param target_dataset_version_id: 目标数据集版本 ID
    :param query_clip_file: 本地 clip 文件路径，首行必须为 'id'
    :param mode: 匹配模式，可选值:
        - ClipMatchMode.BIN_STRICT: bin 严格模式 - 同源 bin id + 同源 bin table + start_timestamp + end_timestamp
        - ClipMatchMode.BIN_LOOSE:  bin 宽松模式 - 同源 bin id + 同源 bin table
        - ClipMatchMode.RAW_LOOSE:  bag 同源模式 - 原始 bag id + 同源 bag table
        默认为 BIN_STRICT

    Returns:
        {"code": 200, "message": "", "data": { "result_path": "", "status": "" } }
    """
    if not os.path.isfile(query_clip_file):
        raise ApiBaseError(f"query_clip_file不存在: {query_clip_file}")

    # Read the first line of the file
    try:
        with open(query_clip_file) as f:
            first_line = f.readline().strip()
    except Exception as e:
        raise ApiBaseError(f"读取query_clip_file失败: {e}")
    if not first_line or first_line != "id":
        raise ApiBaseError(
            f"query_clip_file首行必须是'id'，实际为: {first_line!r}"
        )

    oss_client = self._oss_tool._get_or_create_client("")
    filename = os.path.basename(query_clip_file)
    oss_key = (
        f"dataset/{target_dataset_version_id}/spark-search-param/{filename}"
    )
    oss_path = f"oss://{self._bucket_name}/{oss_key}"
    oss_client.upload_file(self._bucket_name, oss_key, query_clip_file)

    payload: Dict[str, Any] = {
        "source_dataset_version_id": source_dataset_version_id,
        "target_dataset_version_id": target_dataset_version_id,
        "query_clip_file": oss_path,
        "mode": mode.value,
    }
    resp = self._webapp_client.do_request(
        WebappClient.POST,
        "/dataset/compare-ubm-clip-version",
        json=payload,
    )

    return resp.to_resp_body()


def freeze_version(
    self,
    dataset_version_id: str,
) -> RespBody:
    """
    Freeze a dataset version.

    Args:
        dataset_version_id: Dataset version ID.

    Returns:
        RespBody with code, message, and data.
    """
    if not dataset_version_id:
        raise ApiBaseError("dataset_version_id不能为空")

    resp = self._webapp_client.do_request(
        WebappClient.POST,
        "/dataset/freeze-version",
        params={"id": dataset_version_id},
    )

    return resp.to_resp_body()
