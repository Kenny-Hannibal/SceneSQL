import fnmatch
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dm_sdk.models import RespBody
from dm_sdk.prod_data.base import ProdDataBasic
from dm_sdk.prod_data.models import DATA_TYPE_VIRTUAL_CLIP, UploadFileItem
from dm_sdk.tools.api import ApiBaseError
from dm_sdk.tools.webapp_client import WebappClient
from dm_sdk.tools.type_check import checked


class FileMixin(ProdDataBasic):
    """文件上传/下载/列举/删除 Mixin。"""

    def list_clip_files(
        self,
        data_id: str,
        table: Optional[str] = None,
        relative_path: Optional[str] = None,
    ) -> List[str]:
        """
        List all file paths associated with a clip using the API.

        Args:
            data_id: The clip ID.
            table: Table name. If not specified, uses the default table.
            relative_path: Optional relative path prefix to filter files.
                          Only files under this path will be returned.

        Returns:
            List of logical file paths.
        """
        self._tracker.track(data_id, table or self.table, "list_clip_files")
        return self._list_files(data_id, table, relative_path)

    def list_bag_files(
        self,
        data_id: str,
        table: Optional[str] = None,
        relative_path: Optional[str] = None,
    ) -> List[str]:
        """
        List all file paths associated with a bag using the API.

        Args:
            data_id: The bag ID.
            table: Table name. If not specified, uses the default table.
            relative_path: Optional relative path prefix to filter files.
                          Only files under this path will be returned.

        Returns:
            List of logical file paths.
        """
        self._tracker.track(data_id, table or self.table, "list_bag_files")
        return self._list_files(data_id, table, relative_path)

    def _list_files(
        self,
        data_id: str,
        table: Optional[str] = None,
        relative_path: Optional[str] = None,
    ) -> List[str]:
        table = table or self.table
        storage_prefix = self._get_storage_prefix(data_id, table)
        if not storage_prefix:
            raise ApiBaseError(
                f"storage_prefix无效 (data_id={data_id}, table={table})"
            )

        # Normalize relative_path: ensure it ends with / if provided
        if relative_path:
            relative_path = relative_path.strip("/") + "/"

        # UBM, 直接从 OSS 目录获取文件列表
        is_ubm = not self._should_save_file_stats(data_id, table)
        if is_ubm:
            bucket_name, key = (
                self._oss_tool.get_bucket_name_and_remote_prefix(
                    storage_prefix
                )
            )
            # If relative_path is specified, append it to the OSS prefix
            list_prefix = key + relative_path if relative_path else key
            paths = self._oss_tool.list_directory(
                table, bucket_name, list_prefix
            )

            # Remove the base prefix to get relative paths
            base_to_remove = key + relative_path if relative_path else key
            return [
                path[len(base_to_remove) :]
                if path.startswith(base_to_remove)
                else path
                for path in paths
            ]

        # UPM，以 db 为准
        all_paths = []
        for item in self._fetch_all_file_items(data_id, table):
            path = item.get("path")
            if path:
                # Get relative path by removing storage_prefix
                rel_path = (
                    path[len(storage_prefix) :]
                    if path.startswith(storage_prefix)
                    else path
                )

                # Filter by relative_path if specified
                if relative_path:
                    if rel_path.startswith(relative_path):
                        all_paths.append(rel_path)
                else:
                    all_paths.append(rel_path)

        return all_paths

    def _list_files_for_download(
        self, data_id: str, table: Optional[str] = None
    ) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
        """
        获取文件列表用于下载，返回物理路径和逻辑路径的映射。
        - UBM: 从 OSS 直接列目录
        - UPM: 以 db 为准, 虚拟 clip 如果存在 mapping_path, 则:
                - logical_path, 即 path, 是要下载到本地的路径，要处理为逻辑路径
                - physical_path, 即 mapping_path, 是指向要下载的真实 oss 文件的路径

        Returns:
            List[Dict[str, str]]: 每个元素包含 {"logical_path": str, "physical_path": str}
        """
        table = table or self.table
        storage_prefix = self._get_storage_prefix(data_id, table)
        if not storage_prefix:
            raise ApiBaseError(
                f"storage_prefix无效 (data_id={data_id}, table={table})"
            )
        bucket_name, remote_prefix = (
            self._oss_tool.get_bucket_name_and_remote_prefix(storage_prefix)
        )

        is_ubm = not self._should_save_file_stats(data_id, table)
        if is_ubm:
            oss_paths = self._oss_tool.list_directory(
                table, bucket_name, remote_prefix
            )
            mappings = []
            for full_path in oss_paths:
                physical_path = f"oss://{bucket_name}/{full_path}"
                logical_path = full_path.split(remote_prefix, 1)[1]
                mappings.append(
                    {
                        "logical_path": logical_path,
                        "physical_path": physical_path,
                    }
                )
            bucket_table_map = self.get_bucket_name_to_table_map([bucket_name])
            return mappings, bucket_table_map

        # UPM，以 db 为准
        _, meta_resp = self._get_metadata(data_id, table, ["data_type"])
        if meta_resp.code != 200:
            raise ApiBaseError(
                f"获取元数据失败: {meta_resp.msg} (data_id={data_id}, table={table})",
                trace_id=meta_resp.trace_id,
            )

        is_virtual = (
            (meta_resp.resp_data() or {}).get("data_type") == DATA_TYPE_VIRTUAL_CLIP
        )

        all_mappings = []
        buckets = {bucket_name}
        for item in self._fetch_all_file_items(data_id, table):
            path = item.get("path")
            mapping_path = item.get("mapping_path")
            logical_path = (
                path[len(storage_prefix) :]
                if path.startswith(storage_prefix)
                else path
            )
            if is_virtual and mapping_path:
                bucket_name = mapping_path[6:].split("/", 1)[0]
                buckets.add(bucket_name)
                all_mappings.append(
                    {
                        "logical_path": logical_path,
                        "physical_path": mapping_path,
                    }
                )
            elif path:
                all_mappings.append(
                    {"logical_path": logical_path, "physical_path": path}
                )

        bucket_table_map = self.get_bucket_name_to_table_map(list(buckets))
        return all_mappings, bucket_table_map

    def download_clip(
        self,
        data_id: str,
        target_path: str,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """Download all files in a clip."""
        self._tracker.track(data_id, table or self.table, "download_clip")
        return self._download_data(data_id, target_path, table, max_workers)

    def download_bag(
        self,
        data_id: str,
        target_path: str,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """Download all files in a bag."""
        self._tracker.track(data_id, table or self.table, "download_bag")
        return self._download_data(data_id, target_path, table, max_workers)

    def _download_data(
        self,
        data_id: str,
        target_path: str,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        file_mappings, bucket_table_map = self._list_files_for_download(
            data_id, table
        )
        if not file_mappings:
            raise ApiBaseError(
                f"未找到可下载的文件 (data_id={data_id}, table={table})"
            )

        table = table or self.table
        results = self._oss_tool.download_files_with_mapping(
            bucket_table_map, file_mappings, target_path, max_workers
        )

        return {
            "success": sum(1 for r in results if r.get("success")),
            "failed": [r["path"] for r in results if not r.get("success")],
        }

    def download_clip_files(
        self,
        data_id: str,
        relative_paths: List[str],
        target_path: str,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """Download specific clip files using wildcard patterns against the file list."""
        self._tracker.track(
            data_id, table or self.table, "download_clip_files"
        )
        return self._download_filtered_files(
            data_id, relative_paths, target_path, table, max_workers
        )

    def download_bag_files(
        self,
        data_id: str,
        relative_paths: List[str],
        target_path: str,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """Download specific bag files using wildcard patterns against the file list."""
        self._tracker.track(data_id, table or self.table, "download_bag_files")
        return self._download_filtered_files(
            data_id, relative_paths, target_path, table, max_workers
        )

    def _download_filtered_files(
        self,
        data_id: str,
        patterns: List[str],
        target_path: str,
        table: Optional[str],
        max_workers: int,
    ) -> Dict[str, Any]:
        file_mappings, bucket_table_map = self._list_files_for_download(
            data_id, table
        )
        matched_mappings = []

        for pattern in patterns:
            for mapping in file_mappings:
                logical_path = mapping["logical_path"]
                if fnmatch.fnmatch(logical_path, pattern) or fnmatch.fnmatch(
                    logical_path, f"*/{pattern}"
                ):
                    if mapping not in matched_mappings:
                        matched_mappings.append(mapping)

        if not matched_mappings:
            return {"success": 0, "failed": []}

        table = table or self.table
        results = self._oss_tool.download_files_with_mapping(
            bucket_table_map, matched_mappings, target_path, max_workers
        )
        return {
            "success": sum(1 for r in results if r.get("success")),
            "failed": [r["path"] for r in results if not r.get("success")],
        }

    def upload_clip_dir(
        self,
        data_id: str,
        local_dir: str,
        exclude: Optional[List[str]] = None,
        table: Optional[str] = None,
        topic: str = "other",
        max_workers: int = 16,
        target_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._tracker.track(data_id, table or self.table, "upload_clip_dir")
        return self._upload_dir(
            data_id, local_dir, exclude, table, topic, max_workers, target_dir
        )

    def upload_bag_dir(
        self,
        data_id: str,
        local_dir: str,
        exclude: Optional[List[str]] = None,
        table: Optional[str] = None,
        topic: str = "other",
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        self._tracker.track(data_id, table or self.table, "upload_bag_dir")
        return self._upload_dir(
            data_id, local_dir, exclude, table, topic, max_workers
        )

    @checked
    def upload_bag_files(
        self,
        data_id,
        path_mappings: List[Tuple[str, str, str]],
        table=None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """path_mappings: List of (local_path, remote_path, topic)"""
        self._validate_path_mappings(path_mappings)
        items = [
            {"local": local_path, "remote": rel_path, "topic": topic}
            for local_path, rel_path, topic in path_mappings
            if os.path.isfile(local_path)
        ]
        self._tracker.track(data_id, table or self.table, "upload_bag_files")
        return self._upload_flow(
            data_id, table or self.table, items, max_workers
        )

    def upload_clip_files(
        self,
        data_id: str,
        table: Optional[str] = None,
        files: Optional[List[UploadFileItem]] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        if files is None:
            files = []
        items = [f.to_dict() for f in files]
        self._tracker.track(data_id, table or self.table, "upload_clip_files")
        return self._upload_flow(
            data_id, table or self.table, items, max_workers
        )

    def _upload_dir(
        self,
        data_id: str,
        local_dir: str,
        exclude: Optional[List[str]],
        table: Optional[str],
        topic: str,
        max_workers: int,
        target_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        abs_dir = Path(local_dir).resolve()
        if not abs_dir.is_dir():
            raise ApiBaseError(f"local_dir不是目录: {local_dir}")

        prefix = target_dir.strip("/") + "/" if target_dir else ""
        items = []
        exclude_patterns = exclude or []

        for root, dirs, files in os.walk(local_dir):
            if exclude_patterns:
                dirs[:] = [
                    d
                    for d in dirs
                    if not any(
                        fnmatch.fnmatch(d, pattern)
                        for pattern in exclude_patterns
                    )
                ]

            for file in files:
                if exclude_patterns and any(
                    fnmatch.fnmatch(file, pattern)
                    for pattern in exclude_patterns
                ):
                    continue

                local_path = Path(root, file)
                rel_path = str(local_path.relative_to(abs_dir)).replace(
                    os.sep, "/"
                )
                items.append(
                    {
                        "local": str(local_path),
                        "remote": prefix + rel_path,
                        "topic": topic,
                    }
                )
        return self._upload_flow(
            data_id, table or self.table, items, max_workers
        )

    def _upload_flow(
        self,
        data_id: str,
        table: str,
        items: List[Dict],
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """
        The central pipeline for all upload operations.
        """
        if not items:
            return {"all_success": True, "failed_files": []}

        storage_prefix = self._get_storage_prefix(data_id, table)
        remote_prefix = self._prepare_remote_prefix(data_id, table)
        is_ubm = not self._should_save_file_stats(data_id, table)

        oss_keys = [remote_prefix + item["remote"] for item in items]
        bucket_name = self._oss_tool.get_bucket_name_by_table(table, "base")
        remote_info_map = self._oss_tool._batch_fetch_remote_info(
            table, bucket_name, oss_keys, max_workers=max_workers
        )
        if is_ubm:
            existing_file_sizes = {
                key: info.get("size", 0) if info.get("exists") else 0
                for key, info in remote_info_map.items()
            }
        else:
            existing_file_sizes = self._get_file_size_from_db(data_id, table)

        topics = {}
        oss_mappings = []
        file_info_map = {}
        for item in items:
            local_path, remote_relative_path = item["local"], item["remote"]
            fk = item.get("file_key") or ""
            l_size = os.path.getsize(local_path)

            if is_ubm:
                key = remote_prefix + remote_relative_path
                e_size = existing_file_sizes.get(key, 0)
            else:
                e_size = existing_file_sizes.get(
                    storage_prefix + remote_relative_path, 0
                )

            file_info_map[local_path] = {
                "local_size": l_size,
                "existing_size": e_size,
            }
            oss_mappings.append(
                {"local_file": local_path, "remote": remote_relative_path}
            )

            if not is_ubm:
                topic = item.get("topic", "other")
                topic_type = item.get("topic_type", "other")
                if topic not in topics:
                    topics[topic] = {
                        "name": topic,
                        "type": topic_type,
                        "sub_files": [],
                    }
                topics[topic]["sub_files"].append(
                    {
                        "file_key": fk,
                        "path": remote_relative_path,
                        "mapping_path": "",
                        "file_size": l_size,
                    }
                )

        clip_data = topics if not is_ubm else {}
        pre_res = self._file_pre_upload(data_id, table, clip_data)
        if not pre_res:
            raise ApiBaseError(
                f"预上传初始化失败 (data_id={data_id}, table={table})"
            )
        req_id, curr_count, curr_size, curr_physical_size = pre_res

        bucket_name = self._oss_tool.get_bucket_name_by_table(table, "base")
        results, fails = self._oss_tool.upload_files(
            table,
            bucket_name,
            remote_prefix,
            oss_mappings,
            remote_info_map,
            max_workers,
        )
        if fails:
            if req_id:
                self._upload_task_fail(
                    req_id, f"OSS Upload failed for {len(fails)} files"
                )
            return {"all_success": False, "failed_files": fails}

        return self._update_stats(
            data_id,
            table,
            results,
            file_info_map,
            curr_count,
            curr_size,
            curr_physical_size,
            req_id,
            clip_data,
        )

    def _update_stats(
        self,
        data_id,
        table,
        results,
        info_map,
        curr_count,
        curr_size,
        curr_physical_size,
        req_id,
        clip_data,
    ) -> Dict[str, Any]:
        new_files = 0
        size_delta = 0
        files = []
        for r in results:
            files.append(r.get("local_file"))

            if r.get("error"):
                continue
            info = info_map.get(str(r["local_file"]))
            if info:
                if info["existing_size"] == 0:
                    new_files += 1
                size_delta += info["local_size"] - info["existing_size"]

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/file/update-stats",
            json={
                "data_id": data_id,
                "table": table,
                "file_cnt": (curr_count or 0) + new_files,
                "total_size": (curr_size or 0) + size_delta,
                "physical_size": (curr_physical_size or 0) + size_delta,
                "req_id": req_id,
                "clip_data": clip_data,
            },
        )
        if resp.get("code") != 200:
            return {"all_success": False, "failed_files": files}
        return {"all_success": True, "failed_files": []}

    def delete_bag_files(
        self,
        data_id: str,
        topic: str = "other",
        file_paths: Optional[List[str]] = None,
        table: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete specific files from oss bucket.

        Args:
            data_id: The bag ID.
            file_paths: List of file paths to delete.
            table: Table name. If not specified, uses the default table.

        Returns:
            A dictionary containing deletion result:
            - success (bool): True if deletion succeeded.
            - msg (str): Error message if deletion failed, or empty string.
            - fail_list (list): List of fail to deleted file paths.
        """
        self._tracker.track(data_id, table or self.table, "delete_bag_files")
        return self._delete_files(data_id, topic, file_paths, table)

    def delete_clip_files(
        self,
        data_id: str,
        topic: str = "other",
        file_paths: Optional[List[str]] = None,
        table: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delete specific files from oss bucket. parameters same as delete_bag_files."""
        self._tracker.track(data_id, table or self.table, "delete_clip_files")
        return self._delete_files(data_id, topic, file_paths, table)

    def delete_clip(
        self,
        data_id: str,
        table: Optional[str] = None,
        delete_scope: Optional[List[str]] = None,
    ) -> RespBody:
        """Delete a clip and/or its associated tags and data.

        When ``delete_scope`` is not provided, the entire clip record,
        its tags and all data (files + frames) will be hard-deleted.

        Args:
            data_id: The clip ID.
            table: Table name. If not specified, uses the default table.
            delete_scope: Optional list specifying what to delete.
                - ``"tags"``: Delete only tags data (label results).
                - ``"data"``: Delete only files and frames.
                If omitted, deletes everything.

        Returns:
            RespBody with deletion result.
        """
        return self._delete_data(data_id, table, delete_scope, "clip")

    def delete_bag(
        self,
        data_id: str,
        table: Optional[str] = None,
        delete_scope: Optional[List[str]] = None,
    ) -> RespBody:
        """Delete a bag and/or its associated tags and data.

        When ``delete_scope`` is not provided, the entire bag record,
        its tags and all data (files) will be hard-deleted.

        Args:
            data_id: The bag ID.
            table: Table name. If not specified, uses the default table.
            delete_scope: Optional list specifying what to delete.
                - ``"tags"``: Delete only tags data (label results).
                - ``"data"``: Delete only files.
                If omitted, deletes everything.

        Returns:
            RespBody with deletion result.
        """
        return self._delete_data(data_id, table, delete_scope, "bag")

    def _check_delete_permission(self, table: str) -> None:
        """检查当前用户是否对指定表拥有删除权限。

        Args:
            table: 表名。

        Raises:
            ApiBaseError: 获取权限失败或无删除权限。
        """
        resp = self._cerberus_client.do_request(
            WebappClient.GET,
            "/permissionCache/getTablePermissionOfUser",
            params={"table": table},
        )
        if resp.get("code") != 200:
            raise ApiBaseError(
                f"获取表权限失败: {resp.get('message', '')} (table={table})",
                trace_id=resp.trace_id,
            )
        permissions = resp.get("data", []) or []
        if "delete" not in permissions:
            raise ApiBaseError(
                f"没有删除权限 (table={table})",
                trace_id=resp.trace_id,
            )

    def _delete_data(
        self,
        data_id: str,
        table: Optional[str] = None,
        delete_scope: Optional[List[str]] = None,
        data_type: str = "clip",
    ) -> RespBody:
        """Internal implementation for delete_clip / delete_bag."""
        table = table or self.table

        # 0. 检查删除权限
        self._check_delete_permission(table)

        # 1. 解析并校验 delete_scope
        resolved_scopes = None
        if delete_scope:
            delete_scope = list({s for s in delete_scope})
            for scope in delete_scope:
                if scope not in ("tags", "data"):
                    raise ApiBaseError(
                        f"delete_scope 只能包含 'tags' 或 'data' (收到: {scope!r}, "
                        f"data_id={data_id}, table={table})"
                    )
            resolved_scopes = delete_scope

        action_name = f"delete_{data_type}"
        self._tracker.track(data_id, table, action_name)

        # 2. 如需删除 data，直接扫描 OSS 目录并删除
        if not delete_scope or "data" in delete_scope:
            storage_prefix = self._get_storage_prefix(data_id, table)
            if not storage_prefix:
                raise ApiBaseError(
                    f"storage_prefix无效，无法删除OSS文件 "
                    f"(data_id={data_id}, table={table})"
                )
            bucket_name, remote_prefix = (
                self._oss_tool.get_bucket_name_and_remote_prefix(storage_prefix)
            )
            oss_result = self._oss_tool.delete_by_prefix(
                table, bucket_name, remote_prefix
            )
            if oss_result.get("failed"):
                failed_keys = [f[0] for f in oss_result["failed"]]
                return RespBody(
                    code=500,
                    msg=f"OSS 删除失败 {len(failed_keys)} 个文件",
                    data={"failed_files": failed_keys},
                )

        # 3. 调用后端删除接口
        request_body = {
            "table": table,
            "data_id": data_id,
        }
        if resolved_scopes:
            request_body["delete_scopes"] = resolved_scopes

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/data/delete",
            json=request_body,
        )
        return resp.to_resp_body()

    @checked
    def _build_not_stats_candidates(
        self,
        storage_prefix: str,
        file_paths: List[str],
        table: str,
        bucket_name: str,
    ) -> List[Dict[str, Any]]:
        """为 UBM 构造删除 candidates：拼接 delete_key、批量查 OSS size、过滤不存在的文件。

        Args:
            storage_prefix: OSS 存储前缀，如 oss://bucket/prefix/
            file_paths: 相对路径列表。
            table: 表名。
            bucket_name: OSS bucket 名称。

        Returns:
            过滤后仅包含存在的文件的 candidates 列表。
        """
        # 预先提取 storage_prefix 中 bucket 之后的 key_prefix
        sp_parts = storage_prefix.rstrip("/")[6:].split("/", 1)
        key_prefix = sp_parts[1] + "/" if len(sp_parts) == 2 else ""

        candidates = []
        oss_keys = []
        for p in file_paths:
            delete_key = key_prefix + p.lstrip("/")
            candidates.append(
                {
                    "path": p,
                    "delete_key": delete_key,
                    "file_size": 0,
                }
            )
            oss_keys.append(delete_key)

        if not oss_keys:
            return candidates

        remote_info = self._oss_tool._batch_fetch_remote_info(
            table, bucket_name, oss_keys
        )
        # 过滤掉 OSS 上已不存在的文件，避免无效删除
        existing_candidates = []
        for c in candidates:
            info = remote_info.get(c["delete_key"], {})
            if info.get("exists"):
                c["file_size"] = info.get("size", 0)
                existing_candidates.append(c)
            else:
                self._logger.warning("文件不存在，跳过删除: %s", c["path"])
        return existing_candidates

    @checked
    def _delete_files(
        self,
        data_id: str,
        topic: str,
        file_paths: Optional[List[str]],
        table: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not file_paths:
            return {"success": True, "msg": "", "fail_list": []}

        table = table or self.table

        # 统一走 pre-delete → 删OSS → delete 流程
        # 1. 获取待删除文件信息（UPM 从 DB 查，UBM 从 file_paths 构造）
        storage_prefix = self._get_storage_prefix(data_id, table)
        bucket_name, key = storage_prefix.replace("oss://", "").split("/", 1)

        need_stats = self._should_save_file_stats(data_id, table)
        if need_stats:
            candidates = self._get_clip_delete_candidates(data_id, table, topic, file_paths)
        else:
            candidates = self._build_not_stats_candidates(
                storage_prefix, file_paths, table, bucket_name
            )

        # 2. 调用 pre-delete（files 只含 path）
        req_id = self._file_pre_delete(data_id, table, candidates)

        # 3. 删 OSS（mapping 文件跳过）
        oss_result = self._delete_oss_candidates(
            data_id, table, candidates, bucket_name
        )
        if oss_result["failed"]:
            failed_cnt = len(oss_result["failed"])
            raise ApiBaseError(
                f"OSS 删除失败 {failed_cnt} 个文件，"
                f"已记录 pre-delete(req_id={req_id})，由后端兜底 job 处理"
            )

        # 4. 计算删除后的 remaining stats
        remaining_stats = self._calc_remaining_stats(data_id, table, candidates)

        # 5. 调用 delete 确认
        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/file/delete",
            json={
                "data_id": data_id,
                "table": table,
                "topic": topic,
                "files": file_paths,
                "req_id": req_id,
                "file_cnt": remaining_stats["file_cnt"],
                "total_size": remaining_stats["total_size"],
                "physical_size": remaining_stats["physical_size"],
            },
        )
        return resp.get("data", {})

    def delete_frame_all(
        self,
        data_id: str,
        table: Optional[str] = None,
    ) -> RespBody:
        """Delete all frame data for a given data_id.

        Args:
            data_id: The data ID.
            table: Table name. If not specified, uses the default table.

        Returns:
            RespBody with deletion result.
        """
        return self._delete_frame_list(data_id=data_id, table=table)

    def delete_frames(
        self,
        data_id: str,
        timestamps: Optional[List[int]],
        topics: Optional[List[str]] = None,
        table: Optional[str] = None,
    ) -> RespBody:
        """Delete frames by timestamps, topics.

        - timestamps only: delete frames at those timestamps
        - both: delete all (timestamp, topic) combinations
        Args:
            data_id: The data ID.
            timestamps: List of timestamps to delete.
            topics: List of topics to delete.
            table: Table name. If not specified, uses the default table.

        Returns:
            RespBody with deletion result.
        """
        return self._delete_frame_list(
            data_id=data_id,
            table=table,
            timestamps=timestamps,
            topics=topics,
        )

    def delete_frame_files(
        self,
        data_id: str,
        file_paths: List[str],
        timestamps: Optional[List[int]],
        topics: Optional[List[str]] = None,
        table: Optional[str] = None,
    ) -> RespBody:
        """Delete specific files within the scope of given timestamps or topics.

        file_paths must be provided, along with at least one of timestamps or topics.

        Args:
            data_id: The data ID.
            file_paths: List of file paths to delete.
            timestamps: Restrict deletion to these timestamps.
            topics: Restrict deletion to these topics.
            table: Table name. If not specified, uses the default table.

        Returns:
            RespBody with deletion result.
        """
        if not file_paths:
            raise ApiBaseError("file_paths不能为空")
        if not timestamps or not topics:
            raise ApiBaseError("删除frame文件时必须提供timestamps或topics")
        return self._delete_frame_list(
            data_id=data_id,
            table=table,
            timestamps=timestamps,
            topics=topics,
            file_paths=file_paths,
        )

    def _get_frame_files_to_delete(
        self,
        data_id: str,
        table: str,
        timestamps: Optional[List[int]] = None,
        topics: Optional[List[str]] = None,
        file_paths: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """从 frame 数据中提取待删除的文件列表。

        Args:
            data_id: 数据 ID。
            table: 表名。
            timestamps: 指定要删除的 timestamps，None 表示不限制。
            topics: 指定要删除的 topics，None 表示不限制。
            file_paths: 指定要删除的文件相对路径，None 表示不限制。

        Returns:
            待删除文件信息列表，每项包含 file_key, path, delete_key,
            mapping_path, file_size, relative_path。
        """
        storage_prefix = self._get_storage_prefix(data_id, table)
        # 预先提取 storage_prefix 中 bucket 之后的 key_prefix，用于拼接 delete_key
        prefix_parts = storage_prefix.replace("oss://", "").split("/", 1)
        key_prefix = prefix_parts[1].rstrip("/") + "/" if len(prefix_parts) == 2 else ""

        file_path_set = set(file_paths) if file_paths else None
        frame_files: List[Dict[str, Any]] = []
        seen_paths: set = set()

        for frame in self.iter_clip_frames(data_id, table, bring_clip_files=True):
            ts = frame.get("timestamp")
            if timestamps is not None and ts not in timestamps:
                continue

            frame_topics = frame.get("topics", {})
            for topic_name, topic_data in frame_topics.items():
                if topics is not None and topic_name not in topics:
                    continue

                for sf in topic_data.get("sub_files", []):
                    path = sf.get("path", "")
                    if not path:
                        continue

                    # 将相对路径转换为完整路径
                    if not path.startswith("oss://"):
                        path = storage_prefix.rstrip("/") + "/" + path.lstrip("/")

                    rel_path = (
                        path[len(storage_prefix) :].lstrip("/")
                        if path.startswith(storage_prefix)
                        else path.lstrip("/")
                    )

                    if file_path_set is not None and rel_path not in file_path_set:
                        continue

                    if path in seen_paths:
                        continue
                    seen_paths.add(path)

                    frame_files.append(
                        {
                            "file_key": sf.get("file_key", ""),
                            "path": path,
                            "delete_key": key_prefix + rel_path.lstrip("/"),
                            "mapping_path": sf.get("mapping_path", ""),
                            "file_size": sf.get("file_size", 0),
                            "relative_path": rel_path,
                        }
                    )

        return frame_files

    def _delete_frame_list(
        self,
        data_id: str,
        table: Optional[str] = None,
        timestamps: Optional[List[int]] = None,
        topics: Optional[List[str]] = None,
        file_paths: Optional[List[str]] = None,
    ) -> RespBody:
        table = table or self.table

        # 只有 UPM（clip）支持删除 frame 文件
        need_stats = self._should_save_file_stats(data_id, table)
        if not need_stats:
            raise ApiBaseError(
                f"当前表 {table} 不支持删除 frame 文件"
            )

        # UPM（clip）：走新流程
        # 1. 获取待删除的 frame 文件列表
        frame_files = self._get_frame_files_to_delete(
            data_id, table, timestamps, topics, file_paths
        )

        # 2. 调用 pre-delete
        req_id = self._file_pre_delete(data_id, table, frame_files)

        # 3. 删 OSS（mapping 文件跳过）
        storage_prefix = self._get_storage_prefix(data_id, table)
        bucket_name = storage_prefix.replace("oss://", "").split("/", 1)[0]
        oss_result = self._delete_oss_candidates(
            data_id, table, frame_files, bucket_name
        )
        if oss_result["failed"]:
            failed_cnt = len(oss_result["failed"])
            raise ApiBaseError(
                f"OSS 删除失败 {failed_cnt} 个文件，"
                f"已记录 pre-delete(req_id={req_id})，由后端兜底 job 处理"
            )

        # 4. 计算删除后的 remaining stats
        remaining_stats = self._calc_remaining_stats(
            data_id, table, frame_files
        )

        # 5. 调用 delete-frame-list 确认
        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/frame/delete-frame-list",
            json={
                "req_id": req_id,
                "data_id": data_id,
                "table": table,
                "timestamps": timestamps,
                "topics": topics,
                "file_paths": file_paths,
                "file_cnt": remaining_stats["file_cnt"],
                "total_size": remaining_stats["total_size"],
                "physical_size": remaining_stats["physical_size"],
            },
        )
        return resp.to_resp_body()

    def read_bag_file(
        self,
        data_id: str,
        relative_path: str,
        table: Optional[str] = None,
        read_part_size: int = None,
        read_parallel_num: int = None,
        read_block_size: int = None,
    ) -> Optional[bytes]:
        """在线读取 bag 中单个文件内容到内存，不写本地磁盘。

        通过 data_id 获取 storage_prefix 后直接拼接 OSS 路径读取，
        无需像 clip 那样解析复杂的文件映射关系。

        Args:
            data_id: bag 的数据 ID。
            relative_path: 文件在 bag 中的相对路径。
            table: 表名，不传则使用默认表。
            read_part_size: 读取分片大小（字节），不传则使用客户端默认值。
            read_parallel_num: 并行线程数，不传则使用客户端默认值。
            read_block_size: 缓冲区块大小（字节），不传则使用客户端默认值。

        Returns:
            文件的字节内容。读取失败返回 None。
        """
        table = table or self.table
        relative_path = relative_path.lstrip("/")
        storage_prefix = self._get_storage_prefix(data_id, table)
        bucket_name, remote_prefix = (
            self._oss_tool.get_bucket_name_and_remote_prefix(storage_prefix)
        )
        key = remote_prefix.rstrip("/") + "/" + relative_path
        read_kwargs = {}
        if read_part_size is not None:
            read_kwargs["part_size"] = read_part_size
        if read_parallel_num is not None:
            read_kwargs["parallel_num"] = read_parallel_num
        if read_block_size is not None:
            read_kwargs["block_size"] = read_block_size
        try:
            return self._oss_tool.read_file(
                table, bucket_name, key, **read_kwargs
            )
        except Exception:
            return None

    def read_clip_file(
        self,
        data_id: str,
        relative_path: str,
        table: Optional[str] = None,
        read_part_size: int = None,
        read_parallel_num: int = None,
        read_block_size: int = None,
    ) -> Optional[bytes]:
        """
        在线读取 clip 中单个文件内容到内存，不写本地磁盘。

        Args:
            data_id: Clip ID。
            relative_path: 文件在 clip 中的相对路径。
            table: 表名，不传则使用默认表。
            read_part_size: 读取分片大小（字节），不传则使用客户端默认值。
            read_parallel_num: 并行线程数，不传则使用客户端默认值。
            read_block_size: 缓冲区块大小（字节），不传则使用客户端默认值。

        Returns:
            文件的字节内容。找不到文件，返回 None。

        """
        table = table or self.table
        relative_path = relative_path.lstrip("/")
        resolved = self._resolve_file_path(relative_path, data_id, table)
        if resolved is None:
            return None
        oss_table, bucket_name, key = resolved
        read_kwargs = {}
        if read_part_size is not None:
            read_kwargs["part_size"] = read_part_size
        if read_parallel_num is not None:
            read_kwargs["parallel_num"] = read_parallel_num
        if read_block_size is not None:
            read_kwargs["block_size"] = read_block_size
        try:
            return self._oss_tool.read_file(
                oss_table, bucket_name, key, **read_kwargs
            )
        except Exception:
            return None

    def list_clip_dir(
        self,
        data_id: str,
        folder: Optional[str] = None,
        table: Optional[str] = None,
    ) -> Dict[str, List[str]]:
        return self._list_clip_dir(data_id, folder, table)

    def _list_clip_dir(
        self,
        data_id: str,
        folder: Optional[str] = None,
        table: Optional[str] = None,
    ) -> Dict[str, List[str]]:
        table = table or self.table
        storage_prefix = self._get_storage_prefix(data_id, table)
        bucket_name, key = self._oss_tool.get_bucket_name_and_remote_prefix(
            storage_prefix
        )
        if folder:
            key = key.strip("/") + "/" + folder.lstrip("/")
        is_ubm = not self._should_save_file_stats(data_id, table)
        if is_ubm:
            return self._oss_tool.list_dir(table, bucket_name, key)

        # UPM（含虚拟 clip）：从 DB 逻辑路径推导目录/文件
        folder_prefix = folder.strip("/") + "/" if folder else ""
        folders: set = set()
        files: List[str] = []
        for item in self._fetch_all_file_items(data_id, table):
            path = item.get("path")
            if not path:
                continue
            logical = (
                path[len(storage_prefix) :]
                if path.startswith(storage_prefix)
                else path
            )
            logical = logical.lstrip("/")
            if folder_prefix and not logical.startswith(folder_prefix):
                continue
            relative = logical[len(folder_prefix) :]
            parts = relative.split("/", 1)
            if len(parts) == 1:
                files.append(parts[0])
            else:
                folders.add(parts[0])
        return {"folders": sorted(folders), "files": files}

    @staticmethod
    def _validate_path_mappings(mappings: List[Tuple[str, str, str]]):
        if not isinstance(mappings, list) or not mappings:
            raise ApiBaseError("path_mappings必须为非空列表")
        for i, m in enumerate(mappings):
            if not isinstance(m, tuple) or len(m) != 3 or not all(m):
                raise ApiBaseError(
                    f"path_mappings[{i}]格式错误，必须为(local_path, remote_path, file_key)"
                )
