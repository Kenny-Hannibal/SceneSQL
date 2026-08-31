import os
from typing import Any, Dict, Iterator, List, Optional, Tuple

from dm_sdk.models import RespBody
from dm_sdk.prod_data.base import ProdDataBasic
from dm_sdk.prod_data.models import FrameTopicRecord, SubFile, TopicRecord
from dm_sdk.tools.api import ApiBaseError
from dm_sdk.tools.webapp_client import WebappClient


class FrameMixin(ProdDataBasic):
    """帧操作与对齐 Mixin。"""

    def upload_alignment_clip(
        self,
        data_id: str,
        local_path: str,
        frame_data: List[FrameTopicRecord],
        clip_data: List[TopicRecord],
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> RespBody:
        """Align clip with frame data and upload associated files."""
        table = table or self.table
        storage_prefix = self._get_storage_prefix(data_id, table)
        remote_prefix = self._prepare_remote_prefix(data_id, table)

        is_ubm = not self._should_save_file_stats(data_id, table)
        if is_ubm:
            raise ApiBaseError(
                f"UBM类型数据不支持上传对齐操作 (data_id={data_id}, table={table})"
            )

        # Only UPM supported
        existing_file_sizes = self._get_file_size_from_db(data_id, table)
        frame_proc, frame_file_map, frame_size = (
            self._process_frame_alignment_files(
                storage_prefix, frame_data, local_path, existing_file_sizes
            )
        )
        clip_proc, clip_file_map, clip_size = (
            self._process_clip_alignment_files(
                storage_prefix, clip_data, local_path, existing_file_sizes
            )
        )

        pre_resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/frame/pre_save_alignment_data",
            json={
                "data_id": data_id,
                "table": table,
                "frame_data": frame_proc,
                "clip_data": clip_proc,
            },
        )
        if pre_resp.get("code") != 200:
            raise ApiBaseError(
                f"预保存对齐数据失败 (data_id={data_id}, table={table})",
                trace_id=pre_resp.trace_id,
            )

        pre_data = pre_resp["data"] or {}
        req_id = str(pre_data.get("req_id"))
        stats = pre_data.get("file_stats") or {}

        all_file_map = frame_file_map + clip_file_map
        oss_keys = [remote_prefix + m["remote"] for m in all_file_map]
        bucket_name = self._oss_tool.get_bucket_name_by_table(table, "base")
        remote_info_map = self._oss_tool._batch_fetch_remote_info(
            table, bucket_name, oss_keys, max_workers=max_workers
        )

        results, fails = self._oss_tool.upload_files(
            table,
            bucket_name,
            remote_prefix,
            all_file_map,
            remote_info_map,
            max_workers,
        )

        if fails:
            return self._upload_task_fail(
                req_id, "Alignment file upload failed"
            )

        size_map = {**frame_size, **clip_size}
        succ_info = [
            size_map.get(
                str(r["local_file"]), {"local_size": 0, "existing_size": 0}
            )
            for r in results
            if not r.get("error")
        ]
        new_cnt = sum(1 for i in succ_info if i["existing_size"] == 0)
        net_sz = sum(i["local_size"] - i["existing_size"] for i in succ_info)

        body = {
            "data_id": data_id,
            "table": table,
            "req_id": req_id,
            "frame_data": frame_proc,
            "clip_data": clip_proc,
            "file_cnt": (stats.get("file_cnt") or 0) + new_cnt,
            "total_size": (stats.get("total_size") or 0) + net_sz,
            "physical_size": (stats.get("physical_size") or 0) + net_sz,
        }
        resp = self._webapp_client.do_request(
            WebappClient.POST, "/frame/save_alignment_data", json=body
        )

        return resp.to_resp_body()

    @staticmethod
    def _validate_frame_mappings(mappings: List[Dict[str, Any]]) -> None:
        if not isinstance(mappings, list):
            raise ApiBaseError(
                f"frame_mappings必须是列表 (type={type(mappings).__name__})"
            )
        required = (
            "from_table",
            "from_data_id",
            "from_frame_timestamp",
            "from_topic",
            "from_file_path",
            "to_frame_timestamp",
            "to_file_path",
        )
        for i, m in enumerate(mappings):
            if not isinstance(m, dict):
                raise ApiBaseError(
                    f"frame_mappings[{i}]必须是字典 (type={type(m).__name__})"
                )
            missing = [k for k in required if not m.get(k)]
            if missing:
                raise ApiBaseError(
                    f"frame_mappings[{i}]缺少必填字段: {', '.join(missing)}"
                )

    @staticmethod
    def _validate_clip_mappings(mappings: List[Dict[str, Any]]) -> None:
        if not isinstance(mappings, list):
            raise ApiBaseError(
                f"file_mappings必须是列表 (type={type(mappings).__name__})"
            )
        required = (
            "from_table",
            "from_data_id",
            "from_topic",
            "from_file_path",
            "to_file_path",
        )
        for i, m in enumerate(mappings):
            if not isinstance(m, dict):
                raise ApiBaseError(
                    f"file_mappings[{i}]必须是字典 (type={type(m).__name__})"
                )
            missing = [k for k in required if not m.get(k)]
            if missing:
                raise ApiBaseError(
                    f"file_mappings[{i}]缺少必填字段: {', '.join(missing)}"
                )

    def upload_vir_clip_mapping(
        self,
        data_id: str,
        table: str,
        frame_mappings: Optional[List[Dict[str, Any]]] = None,
        frame_meta_map: Optional[Dict[str, Dict]] = None,
        clip_mappings: Optional[List[Dict[str, Any]]] = None,
    ) -> RespBody:
        """
        Upload virtual clip frame and/or file mappings.

        Args:
            data_id: The ID of the virtual clip.
            table: The table name.
            frame_mappings: List of frame mapping dictionaries containing:
                - from_table: Source frame table.
                - from_data_id: Source clip ID.
                - from_frame_timestamp: Source frame timestamp.
                - from_topic: Source topic.
                - from_file_path: Source file path.
                - to_frame_timestamp: Target frame timestamp.
                - to_file_path: Target file path.
            frame_meta_map: Optional per-frame metadata mapping. Dict[str, Dict]
                - key: Frame timestamp.
                - value: Metadata dictionary.
            clip_mappings: List of file mapping dictionaries containing:
                - from_table: Source clip table.
                - from_data_id: Source clip ID.
                - from_topic: Source topic.
                - from_file_path: Source file path.
                - to_file_path: Target file path.

        Returns:
            RespBody Object indicating success or failure.
        """
        if not frame_mappings and not clip_mappings:
            raise ApiBaseError("frame_mappings, clip_mappings至少提供一个")
        if frame_meta_map and not frame_mappings:
            raise ApiBaseError("frame_meta_map需要frame_mappings")
        if frame_mappings:
            self._validate_frame_mappings(frame_mappings)
        if clip_mappings:
            self._validate_clip_mappings(clip_mappings)

        request_body = {
            "data_id": data_id,
            "table": table,
        }
        if frame_mappings:
            request_body["frame_mappings"] = frame_mappings
        if frame_meta_map:
            request_body["frame_meta_map"] = frame_meta_map
        if clip_mappings:
            request_body["clip_mappings"] = clip_mappings

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/frame/save_vir_clip_frame_mappings",
            json=request_body,
        )

        return resp.to_resp_body()

    def get_clip_frames(
        self,
        data_id: str = "",
        table: str = "",
        page: int = 1,
        size: int = 1000,
        bring_clip_files: bool = True,
    ) -> RespBody:
        """Get frames of a clip.

        Args:
            data_id: The clip id.
            table: The table name. If not specified, uses the default table.
            page: Page number (1-based).
            size: Page size.
            bring_clip_files: Whether to bring clip files.

        Returns:
            RespBody containing frames."""
        collection_name = table or self.table
        payload = {
            "data_id": data_id,
            "table": collection_name,
            "clip_flag": bring_clip_files,
            "page": page,
            "size": size,
        }
        resp = self._webapp_client.do_request(
            WebappClient.POST, "/frame/list", json=payload
        )
        return resp.to_resp_body()

    def iter_clip_frames(
        self,
        data_id: str = "",
        table: str = "",
        bring_clip_files: bool = True,
    ) -> Iterator[Dict[str, Any]]:
        """Iterate over all frames of a clip, handling pagination automatically.

        Args:
            data_id: The clip id.
            table: The table name. If not specified, uses the default table.
            bring_clip_files: Whether to bring clip files.

        Yields:
            Individual frame dictionaries.
        """
        page = 1
        _page_size = 1000
        while True:
            resp = self.get_clip_frames(
                data_id=data_id,
                table=table,
                page=page,
                size=_page_size,
                bring_clip_files=bring_clip_files,
            )
            if resp.resp_code() != 200:
                raise ApiBaseError(
                    f"获取clip frames失败: {resp.resp_message()} (data_id={data_id}, table={table})",
                    trace_id=resp.trace_id,
                )
            frame_page = resp.resp_data()["frame_data_page"]
            yield from frame_page["data"]
            if page >= frame_page["totalPages"]:
                break
            page += 1

    def _process_frame_alignment_files(
        self,
        storage_prefix,
        frame_data: List[FrameTopicRecord],
        local_path: str,
        existing_file_sizes: Dict[str, int],
    ) -> Tuple[
        List[Dict[str, Any]], List[Dict[str, str]], Dict[str, Dict[str, int]]
    ]:
        all_mappings, all_size_info = [], {}
        processed_frames: List[Dict[str, Any]] = []
        for frame in frame_data:
            processed_topics: Dict[str, Dict[str, Any]] = {}
            for record in frame.topics:
                item = {
                    "name": record.topic,
                    "type": record.type,
                    "metadata": record.metadata,
                    "sub_files": record.sub_files or [],
                }
                new_item, item_mappings, item_size_info = (
                    self._build_alignment_item(
                        storage_prefix, local_path, item, existing_file_sizes
                    )
                )
                processed_topics[record.topic] = new_item
                all_mappings.extend(item_mappings)
                all_size_info.update(item_size_info)
            new_frame: Dict[str, Any] = {
                "timestamp": frame.timestamp,
                "metadata": frame.metadata,
                "topics": processed_topics,
            }
            processed_frames.append(new_frame)

        return processed_frames, all_mappings, all_size_info

    def _process_clip_alignment_files(
        self,
        storage_prefix,
        clip_data: List[TopicRecord],
        local_path: str,
        existing_file_sizes: Dict[str, int],
    ) -> Tuple[
        Dict[str, Any], List[Dict[str, str]], Dict[str, Dict[str, int]]
    ]:
        """处理clip对齐文件，构建上传映射关系。支持 clip data 通过 folder_path 自动展开文件夹。"""
        processed_items = {}
        file_mappings = []
        all_size_info = {}
        for record in clip_data:
            sub_files = record.sub_files or []
            # 如果指定了 folder_path，自动展开文件夹中的所有文件
            if record.folder_path:
                expanded = self._expand_folder_to_sub_files(record.folder_path)
                sub_files = list(sub_files) + expanded
            item = {
                "name": record.topic,
                "type": record.type,
                "metadata": record.metadata,
                "sub_files": sub_files,
            }

            processed_item, item_mappings, item_size_info = (
                self._build_alignment_item(
                    storage_prefix, local_path, item, existing_file_sizes
                )
            )

            processed_items[record.topic] = processed_item
            file_mappings.extend(item_mappings)
            all_size_info.update(item_size_info)

        return processed_items, file_mappings, all_size_info

    def _build_alignment_item(
        self,
        storage_prefix: str,
        local_path: str,
        item: Dict[str, Any],
        existing_file_sizes: Dict[str, int],
    ) -> Tuple[
        Dict[str, Any], List[Dict[str, str]], Dict[str, Dict[str, int]]
    ]:
        """构建单个对齐条目"""
        new_item = {
            "name": item["name"],
            "type": item["type"],
            "metadata": item.get("metadata", {}),
            "sub_files": [],
        }

        file_mappings = []
        file_size_info = {}
        for sub_file in item.get("sub_files", []):
            full_local_path = sub_file.path
            local_file_size = os.path.getsize(full_local_path)
            relative_path = os.path.relpath(full_local_path, local_path)
            file_path = storage_prefix + relative_path

            existing_file_size = existing_file_sizes.get(file_path, 0)
            file_size_info[full_local_path] = {
                "local_size": local_file_size,
                "existing_size": existing_file_size,
            }

            file_mappings.append(
                {
                    "local_file": full_local_path,
                    "remote": relative_path,
                }
            )

            new_item["sub_files"].append(
                {
                    "file_key": sub_file.file_key,
                    "path": relative_path,
                    "mapping_path": "",
                    "file_size": local_file_size,
                    "physical_size": local_file_size,
                }
            )

        return new_item, file_mappings, file_size_info

    @staticmethod
    def _expand_folder_to_sub_files(folder_path: str) -> List[SubFile]:
        """展开文件夹，返回 SubFile 列表，file_key 为文件名。"""
        sub_files = []
        if not os.path.isdir(folder_path):
            raise ApiBaseError(f"folder_path不是目录: {folder_path}")
        for name in sorted(os.listdir(folder_path)):
            full_path = os.path.join(folder_path, name)
            if os.path.isfile(full_path):
                sub_files.append(SubFile(file_key=name, path=full_path))
        return sub_files
