from typing import Any, Dict, List, Optional

from dm_sdk.models import RespBody
from dm_sdk.tools.api import ApiBaseError
from dm_sdk.tools.es_query_builder import condition_to_str
from dm_sdk.tools.webapp_client import WebappClient

from ._csv_utils import (
    list_csv_files,
    upload_csv_files_to_oss,
)
from .models import MemberSource


def create_data_collection(
    self,
    name: str,
    table: Optional[str] = None,
    category: str = "",
    description: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    member_ids: Optional[List[str]] = None,
    condition: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
    member_source: MemberSource = MemberSource.DEFAULT,
    csv_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a data collection.

    Args:
        name: Collection name.
        table: Table name.
        category: Category.
        description: Description.
        metadata: Metadata.
        tags: Tags.
        member_ids: Member list.
        condition: Search condition (used when building collection asynchronously based on conditions).
        member_source: Which field of matched members to use as collection members.
            Options: MemberSource.DEFAULT, MemberSource.ORIGIN, MemberSource.PARENT.
        csv_dir: Local directory containing CSV files. When provided, uploads
            all CSV files to OSS and updates collection status to ready.

    Returns:
        Dict containing data_collection_id, status, status_str.
    """
    if not name:
        raise ApiBaseError("创建集合时name不能为空")

    provided = [
        member_ids is not None,
        condition is not None,
        csv_dir is not None,
    ]
    if not any(provided):
        raise ApiBaseError(
            "member_ids、condition和csv_dir至少提供一个"
        )
    if sum(provided) > 1:
        raise ApiBaseError(
            "member_ids、condition和csv_dir只能三选一"
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
    if tags is not None:
        payload["tags"] = tags
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
        WebappClient.POST, "/data-collection/add", json=payload
    )
    if resp.get("code", 0) != 200:
        raise ApiBaseError(
            f"创建集合失败: {resp.get('message', '')} (name={name!r})",
            trace_id=resp.trace_id,
        )

    result = resp.get("data") or {}

    # csv_dir 模式：上传 CSV 并更新状态
    if csv_dir is not None:
        data_collection_id = result.get("data_collection_id")
        member_location_prefix = result.get("member_location_prefix", "")
        if not data_collection_id:
            raise ApiBaseError(
                "创建集合成功但未返回 data_collection_id"
            )
        if not member_location_prefix:
            raise ApiBaseError(
                "创建集合成功但未返回 member_location_prefix"
            )

        total = upload_csv_files_to_oss(
            self._oss_tool,
            self._bucket_name,
            member_location_prefix,
            csv_files,
        )

        stats_payload = {
            "status": "ready",
            "total": total,
            "data_collection_id": data_collection_id,
        }
        stats_resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/data-collection/update-stats",
            json=stats_payload,
        )
        if stats_resp.get("code", 0) != 200:
            raise ApiBaseError(
                f"更新集合状态失败: {stats_resp.get('message', '')} "
                f"(data_collection_id={data_collection_id})",
                trace_id=stats_resp.trace_id,
            )

    return result


def get_data_collection(
    self,
    data_collection_id: Optional[str] = None,
    name: Optional[str] = None,
) -> RespBody:
    """
    Get data collection.

    Args:
        id: Collection ID (use either id or name).
        name: Collection name.

    Returns:
        Collection details including name, status, description, metadata, table, tags, etc.
    """
    if not data_collection_id and not name:
        raise ApiBaseError("data_collection_id和name至少提供一个")

    payload: Dict[str, Any] = {}
    if data_collection_id:
        payload["data_collection_id"] = data_collection_id
    if name:
        payload["name"] = name

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/data-collection/detail", json=payload
    )
    return resp.to_resp_body()


def update_data_collection(
    self,
    data_collection_id: Optional[str] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
) -> RespBody:
    """
    Update data collection.

    Args:
        data_collection_id: Collection ID.
        name: Collection name.
        description: Description.
        metadata: Metadata.
        tags: Tags.

    Returns:
        Whether the update was successful.
    """
    if not data_collection_id and not name:
        raise ApiBaseError("data_collection_id和name至少提供一个")

    payload: Dict[str, Any] = {}
    if data_collection_id:
        payload["data_collection_id"] = data_collection_id
    if name:
        payload["name"] = name
    if description is not None:
        payload["description"] = description
    if metadata is not None:
        payload["metadata"] = metadata
    if tags is not None:
        payload["tags"] = tags

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/data-collection/update", json=payload
    )
    return resp.to_resp_body()


def delete_data_collection(
    self,
    data_collection_id: Optional[str] = None,
    name: Optional[str] = None,
) -> RespBody:
    """
    Delete data collection.

    Args:
        data_collection_id: Collection ID (use either id or name).
        name: Collection name.

    Returns:
        Whether the deletion was successful.
    """
    if not data_collection_id and not name:
        raise ApiBaseError("data_collection_id和name至少提供一个")

    payload: Dict[str, Any] = {}
    if data_collection_id:
        payload["data_collection_id"] = data_collection_id
    if name:
        payload["name"] = name

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/data-collection/delete", json=payload
    )
    return resp.to_resp_body()


def get_data_collection_members(
    self,
    data_collection_id: Optional[str] = None,
    name: Optional[str] = None,
    page: int = 1,
    size: int = 50,
) -> RespBody:
    """
    Get data collection members (paginated).

    Args:
        data_collection_id: Collection ID (use either id or name).
        name: Collection name.
        page: Page number, 1-based.
        size: Page size.

    Returns:
        Paginated result with total, currentPage, pageSize, totalPages, data.
    """
    if not data_collection_id and not name:
        raise ApiBaseError("data_collection_id和name至少提供一个")

    payload: Dict[str, Any] = {"page": page, "size": size}
    if data_collection_id:
        payload["data_collection_id"] = data_collection_id
    if name:
        payload["name"] = name

    resp = self._webapp_client.do_request(
        WebappClient.POST, "/data-collection/members", json=payload
    )
    return resp.to_resp_body()


def add_data_collection_members(
    self,
    member_ids: List[str],
    data_collection_id: Optional[str] = None,
    name: Optional[str] = None,
) -> RespBody:
    """
    Add members to a data collection.

    Args:
        member_ids: List of member IDs to add.
        data_collection_id: Collection ID (use either id or name).
        name: Collection name.

    Returns:
        Whether the operation succeeded.
    """
    return _update_collection_members(
        self,
        member_ids=member_ids,
        operate_type="add",
        data_collection_id=data_collection_id,
        name=name,
    )


def remove_data_collection_members(
    self,
    member_ids: List[str],
    data_collection_id: Optional[str] = None,
    name: Optional[str] = None,
) -> RespBody:
    """
    Remove members from a data collection.

    Args:
        member_ids: List of member IDs to remove.
        data_collection_id: Collection ID (use either id or name).
        name: Collection name.

    Returns:
        Whether the operation succeeded.
    """
    return _update_collection_members(
        self,
        member_ids=member_ids,
        operate_type="delete",
        data_collection_id=data_collection_id,
        name=name,
    )


def _update_collection_members(
    self,
    member_ids: List[str],
    operate_type: str,
    data_collection_id: Optional[str] = None,
    name: Optional[str] = None,
) -> RespBody:
    if not data_collection_id and not name:
        raise ApiBaseError("data_collection_id和name至少提供一个")

    if not member_ids:
        raise ApiBaseError("member_ids不能为空")

    payload: Dict[str, Any] = {
        "member_ids": member_ids,
        "operate_type": operate_type,
    }
    if data_collection_id:
        payload["data_collection_id"] = data_collection_id
    if name:
        payload["name"] = name

    resp = self._webapp_client.do_request(
        WebappClient.POST,
        "/data-collection/update-members",
        json=payload,
    )
    return resp.to_resp_body()
