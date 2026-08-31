#!/usr/bin/env python3
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.metadata import version
from typing import Any, Dict, Generator, List, Optional, Tuple

from dm_sdk.models import QualityCheckInfo, RespBody
from dm_sdk.tools.api import ApiBaseError
from dm_sdk.tools.dm_static_env import (
    DEFAULT_TIMEOUT_SECONDS,
    ENV_PROD,
    SERVICE_RAW_DATA,
)
from dm_sdk.tools.oss_sts_client_manager import OSSToolManager
from dm_sdk.tools.tracker import Tracker
from dm_sdk.tools.webapp_client import WebappClient


class RawDataClient:
    def __init__(
        self,
        access_token,
        table: str,
        env: Optional[str] = None,
        *args,
        **kwargs,
    ):
        kwargs["timeout"] = DEFAULT_TIMEOUT_SECONDS
        ua = f"raw_data_sdk/{version('dm_sdk')}"
        self.table = table

        # env 默认 prod
        env = env or ENV_PROD

        # service_targets 按服务名分别配置 X-Service-Target
        service_targets = kwargs.pop("service_targets", {}) or {}

        # 提取 OSS 读取参数
        read_part_size = kwargs.pop("read_part_size", None)
        read_parallel_num = kwargs.pop("read_parallel_num", None)
        read_block_size = kwargs.pop("read_block_size", None)
        upload_part_size = kwargs.pop("upload_part_size", None)
        upload_parallel_num = kwargs.pop("upload_parallel_num", None)
        download_part_size = kwargs.pop("download_part_size", None)
        download_parallel_num = kwargs.pop("download_parallel_num", None)

        self._webapp_client = WebappClient(
            env, SERVICE_RAW_DATA,
            service_target=service_targets.get(SERVICE_RAW_DATA),
            *args, **kwargs
        )
        self._webapp_client.headers["User-Agent"] = ua
        self._webapp_client.headers["Access-Token"] = access_token
        self._oss_tool = OSSToolManager(
            backend_sts_url="/common-oss/oss-info",
            webapp_client=self._webapp_client,
            read_part_size=read_part_size,
            read_parallel_num=read_parallel_num,
            read_block_size=read_block_size,
            upload_part_size=upload_part_size,
            upload_parallel_num=upload_parallel_num,
            download_part_size=download_part_size,
            download_parallel_num=download_parallel_num,
        )
        self._tracker = Tracker(access_token, SERVICE_RAW_DATA, env)

    def get_bag_metadata(
        self,
        table: Optional[str] = None,
        bag_id: str = "",
        bag_name: str = "",
        fields: Optional[List[str]] = None,
    ) -> RespBody:
        """
        获取原始包元数据。
        :param table: 表名
        :param bag_id: 包id
        :param bag_name: 包名称
        :param fields: 返回的field字段，不传默认返回全部
        :return: 返回response
        """
        table = table or self.table
        params_tuple: Dict[str, Any] = {
            "collectionName": table,
        }
        query_fields = []
        if bag_id:
            query_fields.append({"field": "bag_id", "value": bag_id})
        if bag_name:
            query_fields.append({"field": "bag_name", "value": bag_name})

        if query_fields:
            params_tuple["queryParams"] = query_fields
        if fields:
            params_tuple["resultFields"] = fields

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/mongo/metadata-query",
            json=params_tuple,
        )
        data = (resp.get("data") or {}).get("data")
        filtered = None
        if isinstance(data, list) and len(data) > 0:
            first_item = data[0]
            self._tracker.track(
                first_item.get("bag_id", ""), table, "get_bag_metadata"
            )
            filtered = first_item
            if fields:
                filtered = self._filter_fields(first_item, fields)
        return resp.to_resp_body(data=filtered)

    def download_bag(
        self,
        table: Optional[str] = None,
        bag_id: str = "",
        bag_name: str = "",
        target_path: str = "",
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """
        下载原始包，根据 bag_id 或 bag_name 查询元数据后从 OSS 下载到指定路径。

        Args:
            table (Optional[str]): 表名，为空时使用实例默认表名
            bag_id (str): 包 ID，与 bag_name 至少提供一个
            bag_name (str): 包名称，与 bag_id 至少提供一个
            target_path (str): 下载目标本地路径
            max_workers (int): 下载并发线程数，默认 16

        Returns:
            Dict[str, Any]: 下载结果，包含成功数量和失败列表，格式为 {"success": int, "failed": List[tuple]}

        Raises:
            ApiBaseError: 获取元数据失败、未找到对应 bag、或 OSS 存储路径为空时抛出
        """
        table = table or self.table
        data = self.get_bag_metadata(
            table, bag_id, bag_name, ["bag_id", "storage_prefix"]
        )
        if data.resp_code() != 200:
            raise ApiBaseError(
                f"获取bag元数据失败: {data.msg} "
                f"(table={table}, bag_id={bag_id!r}, bag_name={bag_name!r})",
                trace_id=data.trace_id,
            )
        if data.resp_data() is None:
            raise ApiBaseError(
                f"未找到bag，请确认查询条件正确 "
                f"(table={table}, bag_id={bag_id!r}, bag_name={bag_name!r})"
            )
        storage_prefix = data.resp_data().get("storage_prefix", "")
        if not storage_prefix:
            raise ApiBaseError(
                f"bag {data.resp_data().get('bag_id', '')} 的OSS存储路径为空，无法下载 "
                f"(table={table}, bag_name={bag_name!r})"
            )

        self._tracker.track(
            data.resp_data().get("bag_id", ""), table, "download_bag"
        )
        return self._oss_tool.download_directory(
            table, storage_prefix, target_path, max_workers
        )

    def download_bag_files(
        self,
        bag_id: str,
        relative_paths: List[str],
        target_path: str,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """Download specific files or directories from a bag by relative paths.

        Each path is classified by convention: paths ending with "/" are treated
        as directories (all files underneath are downloaded with directory
        structure preserved); all other paths are treated as individual files.

        Args:
            bag_id: bag ID
            relative_paths: List of relative paths to download. Paths ending with
                "/" are treated as directories (e.g., "camera/front/"), otherwise
                as files (e.g., "camera/front/001.jpg", "metadata.yaml").
            target_path: Local target directory
            table: Table name. If not specified, uses the default table.
            max_workers: Number of concurrent download threads

        Returns:
            {"success": int, "failed": list}
        """
        if not relative_paths:
            return {"success": 0, "failed": []}

        table = table or self.table

        # Get storage prefix from bag metadata
        data = self.get_bag_metadata(
            table, bag_id=bag_id, fields=["storage_prefix"]
        )
        if data.resp_code() != 200:
            raise ApiBaseError(
                f"获取bag元数据失败: {data.msg} (table={table}, bag_id={bag_id!r})",
                trace_id=data.trace_id,
            )
        if not data.resp_data():
            raise ApiBaseError(f"未找到bag (table={table}, bag_id={bag_id!r})")

        storage_prefix = data.resp_data().get("storage_prefix", "")
        if not storage_prefix:
            raise ApiBaseError(
                f"bag {bag_id} 的OSS存储路径为空，无法下载文件 (table={table})"
            )

        # Build full OSS paths
        bucket_name, remote_prefix = (
            self._oss_tool.get_bucket_name_and_remote_prefix(storage_prefix)
        )
        # Normalize remote_prefix to end with /
        if remote_prefix and not remote_prefix.endswith("/"):
            remote_prefix += "/"

        total_success = 0
        total_failed = []

        # Classify paths by convention: trailing "/" → directory, otherwise → file
        file_oss_paths = []
        dir_entries = []  # (oss_prefix, local_target_path)

        for rel_path in relative_paths:
            normalized = rel_path.strip("/")
            if rel_path.endswith("/"):
                # Directory: download to target_path/<normalized>/
                oss_prefix = f"oss://{bucket_name}/{remote_prefix}{normalized}"
                local_target = os.path.join(target_path, normalized)
                dir_entries.append((oss_prefix, local_target))
            else:
                # File
                file_oss_paths.append(
                    f"oss://{bucket_name}/{remote_prefix}{normalized}"
                )

        # Download individual files
        if file_oss_paths:
            results = self._oss_tool.download_files(
                table, file_oss_paths, target_path, max_workers
            )
            total_success += sum(1 for r in results if r.get("success"))
            total_failed.extend(
                [r.get("path", "") for r in results if not r.get("success")]
            )

        # Download directories
        for oss_prefix, local_target in dir_entries:
            result = self._oss_tool.download_directory(
                table, oss_prefix, local_target, max_workers
            )
            total_success += result.get("success", 0)
            total_failed.extend([str(f) for f in result.get("failed", [])])

        # Track
        if total_success > 0 or total_failed:
            self._tracker.track(bag_id, table, "download_bag_files")

        return {"success": total_success, "failed": total_failed}

    def download_bags_by_topics(
        self,
        bag_id: str = "",
        bag_name: str = "",
        table: Optional[str] = None,
        topics: List[str] = None,
        target_path: str = "",
        max_workers: int = 16,
    ) -> List[Dict[str, Any]]:
        if topics is None:
            topics = []
        table = table or self.table
        data = self.get_bag_metadata(
            table,
            bag_id,
            bag_name,
            ["bag_id", "storage_prefix", "topics"],
        )
        if data.resp_code() != 200:
            raise ApiBaseError(
                f"获取bag元数据失败: {data.msg} "
                f"(table={table}, bag_id={bag_id!r}, bag_name={bag_name!r})",
                trace_id=data.trace_id,
            )
        if data.resp_data() is None:
            raise ApiBaseError(
                f"未找到bag，请确认查询条件正确 "
                f"(table={table}, bag_id={bag_id!r}, bag_name={bag_name!r})"
            )

        storage_prefix = data.resp_data().get("storage_prefix", "")
        if not storage_prefix:
            raise ApiBaseError(
                f"bag {data.resp_data().get('bag_id', '')} 的OSS存储路径为空，无法按topic下载 "
                f"(table={table}, bag_name={bag_name!r})"
            )

        self._tracker.track(
            data.resp_data().get("bag_id", ""),
            table,
            "download_bags_by_topics",
        )
        storage_prefix = (
            f"{storage_prefix}/"
            if not storage_prefix.endswith("/")
            else storage_prefix
        )
        all_topics = data.resp_data().get("topics", {})

        topic_to_group = {}
        for topic_name in topics:
            if topic_name in all_topics:
                topic_info = all_topics[topic_name]
                if isinstance(topic_info, dict):
                    group = topic_info.get("group")
                    if group is not None:
                        topic_to_group[topic_name] = group

        path_to_topics = {}
        for topic, group in topic_to_group.items():
            path = f"{storage_prefix}{group}.bag"
            if path not in path_to_topics:
                path_to_topics[path] = []
            path_to_topics[path].append(topic)

        # 没有匹配任何 group, 下载所有分组包
        paths = list(path_to_topics.keys()) or [
            f"{storage_prefix}{g}.bag" for g in ["default", "camera", "lidar"]
        ]

        metadata_file_path = f"{storage_prefix}metadata.yaml"
        paths.append(metadata_file_path)
        download_res = self._oss_tool.download_files(
            table, paths, target_path, max_workers
        )

        return [
            {
                "path": res["path"],
                "success": res.get("success", False),
                "error": res.get("error_msg", ""),
                "topics": path_to_topics.get(res["path"], []),
            }
            for res in download_res
        ]

    def get_topics_metadata(
        self,
        table: Optional[str] = None,
        bag_id: str = "",
        topics: Optional[List[str]] = None,
        fields: Optional[List[str]] = None,
    ) -> RespBody:
        """
        获取topic元数据。
        :param table: 表名
        :param bag_id: 包id
        :param topics: topic名称列表
        :param fields: 返回field列表，默认不传返回所有字段
        :return: RespBody
        """
        table = table or self.table
        params_tuple = {
            "collectionName": table,
            "pageSize": 2000,
        }
        query_fields = []
        if bag_id:
            query_fields.append({"field": "bag_id", "value": bag_id})

        if topics:
            query_fields.append(
                {"field": "topic_name", "value": topics, "operator": "in"}
            )

        if query_fields:
            params_tuple["queryParams"] = query_fields

        resp = self._webapp_client.do_request(
            WebappClient.POST, "/mongo/topics-query", json=params_tuple
        )
        data = (resp.get("data") or {}).get("data")
        filtered = None
        if isinstance(data, list) and len(data) > 0:
            filtered = []
            for each_data in data:
                self._tracker.track(
                    each_data.get("bag_id", ""), table, "get_topics_metadata"
                )

                if fields:
                    filtered.append(self._filter_fields(each_data, fields))
                else:
                    filtered.append(each_data)
        return resp.to_resp_body(data=filtered)

    def download_topics(
        self,
        table: str,
        bag_id: str,
        topics: Optional[List[str]] = None,
        target_path: str = "",
        max_workers: int = 16,
    ) -> List[Dict[str, Any]]:
        """
        下载topic包。
        :param table: 表名
        :param bag_id: 包id
        :param topics: topic名称列表
        :param target_path: 下载本地目录
        :param max_workers: 多线程数=5
        Returns:
            List of tuples: (topic_name, path, success, error_msg)
        """
        all_results = []

        table = table or self.table
        topics_data = self.get_topics_metadata(
            table, bag_id, topics, ["topic_name", "path"]
        )
        # 1. 判断是否为 None 或非 list
        if topics_data.resp_data() is None or not isinstance(
            topics_data.resp_data(), list
        ):
            if topics_data.resp_code() != 200:
                msg = topics_data.msg
                raise ApiBaseError(
                    f"获取topic元数据失败: {msg} (table={table}, bag_id={bag_id!r})",
                    trace_id=topics_data.trace_id,
                )
            else:
                raise ApiBaseError(
                    f"该bag没有topic数据 (table={table}, bag_id={bag_id!r})"
                )

        # 2. 判断是否为空列表
        if len(topics_data.resp_data()) == 0:
            raise ApiBaseError(
                f"未找到指定的topic (table={table}, bag_id={bag_id!r}, topics={topics!r})"
            )

        # 3. 提取所有 path（安全方式）
        paths = []
        path_to_topic = {}  # 用于反查 topic_name
        for item in topics_data.resp_data():
            if isinstance(item, dict) and "path" in item:
                path_val = item["path"]
                if (
                    isinstance(path_val, str) and path_val.strip()
                ):  # 只处理非空字符串
                    paths.append(path_val)
                    path_to_topic[path_val] = item["topic_name"]
        if not paths:
            raise ApiBaseError(
                f"topic数据中没有有效的OSS路径，无法下载 (table={table}, bag_id={bag_id!r})"
            )

        download_results = self._oss_tool.download_files(
            table, paths, target_path, max_workers
        )
        if len(download_results) > 0:
            self._tracker.track(bag_id, table, "download_topics")

        # 3. 组装最终结果：(topic_name, path, success, error_msg)
        for each_result in download_results:
            topic_name = path_to_topic.get(each_result["path"], "")
            each_result["topic_name"] = topic_name
            all_results.append(each_result)

        return all_results

    # 仅内部使用
    def _filter_fields(self, item, fields):
        """
        根据 fields 列表过滤 item 中的字段。
        :param item: dict，原始 JSON 对象
        :param fields: list[str] 或 None，要保留的字段名
        :return: 过滤后的 dict
        """
        if not fields:
            return item  # 或 return {} / item.copy()，根据需求
        return {key: item[key] for key in fields if key in item}

    def preview_bags(
        self,
        table: Optional[str] = None,
        result_fields: Optional[List[str]] = None,
        exclude_fields: Optional[List[str]] = None,
    ) -> RespBody:
        """Preview one bag record.

        Args:
            table: Table name. If not specified, uses the default table.
            result_fields: List of fields to include. If not specified, returns all fields.
            exclude_fields: List of fields to exclude. Applied after fetching data.

        Returns:
            RespBody containing 1 bag record with filtered fields.
        """
        resp = self._search_bags(None, table, 1, None, result_fields)

        # Apply exclude_fields filter if provided
        if exclude_fields and resp.resp_data():
            data = resp.resp_data()
            if isinstance(data, dict) and "data" in data:
                # The actual records are in data['data'] which is a list
                records = data["data"]
                if isinstance(records, list) and len(records) > 0:
                    # Filter fields for each record
                    filtered_records = []
                    for record in records:
                        if isinstance(record, dict):
                            filtered_record = {
                                k: v
                                for k, v in record.items()
                                if k not in exclude_fields
                            }
                            filtered_records.append(filtered_record)
                        else:
                            filtered_records.append(record)

                    filtered_data = data.copy()
                    filtered_data["data"] = filtered_records
                    return resp.to_resp_body(data=filtered_data)

        return resp

    def search_bags(
        self,
        condition: Dict,
        table: Optional[str] = None,
        size: int = 50,
        search_after: Optional[List[Any]] = None,
        result_fields: Optional[List[str]] = None,
    ) -> RespBody:
        """
        执行一次分页查询(推荐 size <= 1000)

        :param table: mongo table
        :param condition: 由 query_builder 构建的嵌套条件
        :param size: 每页大小（50~1000）
        :param search_after: 上一页返回的 last_sort 值
        :param result_fields: 返回字段列表，如 ["create_time", "_id"]

        :return: {
            "data": {},         # 当前页数据
            "code": 200,    # 下次分页用(可能为 None)
            "msg": ""       # 是否还有更多数据
        }
        """
        if not (50 <= size <= 1000):
            raise ApiBaseError(
                f"每页查询数量size必须在50~1000之间 (size={size})"
            )

        return self._search_bags(
            condition, table, size, search_after, result_fields
        )

    def _search_bags(
        self,
        condition: Optional[Dict] = None,
        table: Optional[str] = None,
        size: int = 50,
        search_after: Optional[List[Any]] = None,
        result_fields: Optional[List[str]] = None,
    ) -> RespBody:
        table = table or self.table
        payload: Dict[str, Any] = {"indexName": table, "size": size}
        if condition is not None:
            payload["condition"] = condition
        if search_after is not None:
            payload["searchAfter"] = search_after
        if result_fields is not None:
            payload["resultFields"] = result_fields

        resp = self._webapp_client.do_request(
            WebappClient.POST, "/es/dynamic-query", json=payload
        )
        return resp.to_resp_body()

    def iter_search_bags(
        self,
        condition: Dict,
        table: Optional[str] = None,
        size: int = 50,
        result_fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
    ):
        """
        自动分页迭代查询，逐条返回记录。

        :param table: 索引名称
        :param condition: 由 query_builder 构建的嵌套条件
        :param size: 每页大小（50~1000）
        :param result_fields: 返回字段列表，如 ["create_time", "_id"]
        :param limit: 最多返回多少条记录；为 None 时则一直迭代到结束
        """
        search_after = None
        yielded = 0
        table = table or self.table
        while True:
            resp = self.search_bags(
                table=table,
                condition=condition,
                size=size,
                search_after=search_after,
                result_fields=result_fields,
            )
            if resp.resp_code() != 200:
                raise ApiBaseError(
                    f"分页查询bags失败: {resp.msg} (table={table})",
                    trace_id=resp.trace_id,
                )

            data = resp.resp_data() or {}
            items = data.get("data", [])
            if isinstance(items, dict):
                items = items.get("data", items)

            next_search_after = data.get("nextSearchAfter")
            if not items:
                break
            for item in items:
                yield item
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if not next_search_after or next_search_after == search_after:
                break
            search_after = next_search_after

    def count_bags(
        self,
        table: Optional[str] = None,
        condition: Optional[Dict] = None,
    ) -> int:
        """
        统计满足条件的bag数量

        :param table: 索引名称
        :param condition: 由 query_builder 构建的嵌套条件
        :return: 符合条件的记录总数
        """
        table = table or self.table
        payload: Dict[str, Any] = {"indexName": table}
        if condition is not None:
            payload["condition"] = condition

        resp = self._webapp_client.do_request(
            WebappClient.POST, "/es/dynamic-query-total", json=payload
        )
        if resp.get("code", 0) != 200:
            raise ApiBaseError(
                str(resp.get('message', '')),
                trace_id=resp.trace_id,
            )
        return resp.get("data", {}).get("total", 0)

    def upsert_bag_tags(
        self,
        bag_id: str,
        tag_source: str,
        tags: Dict[str, Any],
        table: Optional[str] = None,
        tags_replace: bool = True,
        version: str = "v_1_1",
    ) -> RespBody:
        """insert or update bag tags, by tag source."""
        table = table or self.table
        payload: Dict[str, Any] = {
            "table": table,
            "bag_id": bag_id,
            "tag_source": tag_source,
            "tags": tags,
            "tagsReplace": tags_replace,
            "version": version,
        }
        resp = self._webapp_client.do_request(
            WebappClient.POST, "/dataTags/upsert", json=payload
        )

        if resp.get("code", 0) == 200:
            self._tracker.track(bag_id, table, "upsert_bag_tags")
        return resp.to_resp_body()

    def get_bag_tags(
        self,
        bag_id: str,
        tag_source: Optional[str] = None,
        table: Optional[str] = None,
        version: Optional[str] = None,
    ) -> RespBody:
        """get bag tags. when tag_source is None, returns data from all tag sources by default"""
        table = table or self.table
        payload: Dict[str, Any] = {
            "table": table,
            "bag_id": bag_id,
        }
        if version:
            payload["version"] = version
        if tag_source:
            payload["tag_source"] = tag_source
        resp = self._webapp_client.do_request(
            WebappClient.POST, "/dataTags/detail", json=payload
        )

        if resp.get("code", 0) == 200:
            self._tracker.track(bag_id, table, "get_bag_tags")
        return resp.to_resp_body()

    def report_bag_quality_status(
        self,
        bag_id: str,
        bag_name: str,
        pipeline_type: str,
        vin: str,
        collect_date: str,
        is_success: bool,
        check_info: QualityCheckInfo,
        error_reason: Optional[str] = None,
        table: Optional[str] = None,
        check_type: str = "base",
    ) -> bool:
        """
        上报（写入/更新）bag 处理情况。同一 bag_id + pipeline_type 全量覆盖

        :param bag_id: bag 唯一标识
        :param bag_name: bag 名称
        :param pipeline_type: 产线类型，如 "UPM"
        :param vin: 车辆 vin
        :param collect_date: 采集日期，如 "2026-03-01"
        :param is_success: 整体是否成功
        :param check_info: 结构化质检详情，使用 CheckInfo 构造
        :param error_reason: 失败简明原因（可选）
        :param table: 表名，不传则使用初始化时的 table
        :param check_type: 质检类型，默认 "base"
        :return: RespBody
        """
        if not re.fullmatch(r"\d{8}", collect_date):
            raise ValueError(
                f"collect_date 格式错误，应为 YYYYMMDD，收到: {collect_date!r}"
            )

        table = table or self.table
        payload: Dict[str, Any] = {
            "bag_id": bag_id,
            "bag_name": bag_name,
            "pipeline_type": pipeline_type.lower(),
            "vin": vin,
            "collect_date": collect_date,
            "is_success": is_success,
            "check_info": check_info.to_dict(),
            "check_type": check_type.lower(),
            "table": table,
        }
        if error_reason is not None:
            payload["error_reason"] = error_reason

        resp = self._webapp_client.do_request(
            WebappClient.POST, "/bagQualityCheck/report", json=payload
        )
        if resp.get("code", 0) != 200:
            raise ApiBaseError(
                f"Report bag quality check failed, error: {resp.get('message', '')}",
                trace_id=resp.trace_id,
            )

        self._tracker.track(bag_id, table, "report_bag_quality_status")
        return True

    def get_bag_quality_summary(
        self,
        vin: str,
        collect_date: str,
        pipeline_type: str,
        bag_ids: Optional[List[str]] = None,
        table: Optional[str] = None,
        check_type: Optional[str] = "base",
    ) -> RespBody:
        """
        获取 bag 质检摘要

        :param vin: 车辆 vin
        :param collect_date: 采集日期，如 "2026-03-01"
        :param pipeline_type: 产线类型，如 "upm"
        :param bag_ids: 可选，指定 bag_id 列表进行过滤
        :param table: 表名，不传则使用初始化时的 table
        :param check_type: 质检类型，默认 "base"
        :return: RespBody，data 格式：
            { "total": 100, "success": 95, "fail": 5, "failed_bag_ids": ["<失败的 bag_id>", ...] }
        """
        table = table or self.table
        payload: Dict[str, Any] = {
            "vin": vin,
            "collect_date": collect_date,
            "pipeline_type": pipeline_type.lower(),
            "check_type": check_type.lower(),
            "table": table,
        }
        if bag_ids is not None:
            payload["bag_ids"] = bag_ids

        resp = self._webapp_client.do_request(
            WebappClient.POST, "/bagQualityCheck/summary", json=payload
        )
        return resp.to_resp_body()

    def get_bag_quality_detail(
        self,
        pipeline_type: str,
        bag_id: Optional[str] = None,
        bag_name: Optional[str] = None,
        table: Optional[str] = None,
        check_type: str = "base",
    ) -> RespBody:
        """
        根据 bag_id 和 pipeline_type 获取完整质检记录

        :param bag_id: bag 唯一标识
        :param bag_name: bag 唯一标识
        :param pipeline_type: 产线类型，如 "upm"
        :param table: 表名，不传则使用初始化时的 table
        :param check_type: 质检类型，默认 "base"
        :return: RespBody
        """
        if not bag_id and not bag_name:
            raise ValueError("bag_id or bag_name must be specified")

        table = table or self.table
        payload: Dict[str, Any] = {
            "pipeline_type": pipeline_type.lower(),
            "check_type": check_type.lower(),
            "table": table,
        }
        if bag_id:
            payload["bag_id"] = bag_id
        if bag_name:
            payload["bag_name"] = bag_name
        resp = self._webapp_client.do_request(
            WebappClient.POST, "/bagQualityCheck/detail", json=payload
        )

        if resp.get("code", 0) == 200:
            self._tracker.track(
                (resp.get("data") or {}).get("bag_id"),
                table,
                "get_bag_quality_detail",
            )
        return resp.to_resp_body()

    def get_frames(
        self,
        bag_id: str,
        topic: str,
        table: Optional[str] = None,
        frame_interval: int = 1,
    ) -> List[str]:
        """获取图片帧信息
        Args:
            bag_id: bag 包唯一标识
            topic: topic 名称
            table: 可选，指定原始数据表（bag_metadata 表名）
            frame_interval: 采样间隔，每 N 帧取 1 帧，默认 1（不跳帧）

        Returns:
            返回图片帧具体 oss path 列表
        """
        table = table or self.table

        # 查询 frame metadata（后端自动做 _bag_metadata → _bag_frame 转换）
        params = {
            "collectionName": table,
            "topicName": topic,
            "bagIds": [bag_id],
        }

        resp = self._webapp_client.do_request(
            WebappClient.POST, "/mongo/batch-frames-query", json=params
        )
        if resp.get("code") != 200:
            raise ApiBaseError(
                f"Failed to query frames: {resp}",
                trace_id=resp.trace_id,
            )

        data = resp.get("data")
        if not isinstance(data, list) or len(data) == 0:
            return []

        # 从每条记录提取 storage_prefix，通过 OSS list_directory 获取帧文件列表
        all_oss_paths = []
        for record in data:
            storage_prefix = record.get("storage_prefix", "")
            if not storage_prefix:
                continue

            bucket_name, prefix = (
                self._oss_tool.get_bucket_name_and_remote_prefix(
                    storage_prefix
                )
            )
            if not bucket_name or not prefix:
                continue

            try:
                file_keys = self._oss_tool.list_directory(
                    table, bucket_name, prefix
                )
            except Exception:
                continue

            # 帧文件按纳秒时间戳命名且有序，frame_interval=N 表示每 N 帧取 1 帧
            for idx, key in enumerate(file_keys):
                if frame_interval > 1 and idx % frame_interval != 0:
                    continue
                all_oss_paths.append(f"oss://{bucket_name}/{key}")

        return all_oss_paths

    def iter_frame_paths(
        self,
        bag_ids: List[str],
        topic: str,
        table: Optional[str] = None,
        frame_interval: int = 1,
    ):
        """批量获取图片帧信息，单个 topic。先一次性从后端获取所有 bag 的 storage_prefix，
        再用线程池并发做 OSS list_directory，完成的 bag 立即 yield。

        Args:
            bag_ids: bag_id 列表，最大 10000 个
            topic: 单个 topic 名称
            table: 可选，指定原始数据表（bag_metadata 表名）
            frame_interval: 采样间隔，每 N 帧取 1 帧，默认 1（不跳帧）

        Yields:
            (bag_id, [oss_path, ...]) 元组
        """
        if len(bag_ids) > 10000:
            raise ValueError("bag_ids 数量不能超过 10000")
        if not topic:
            raise ValueError("topic 不能为空")

        table = table or self.table

        _max_workers = 16

        # 1. 一次性从后端获取所有 bag 的 storage_prefix
        bid_to_prefix: Dict[str, str] = {}
        try:
            params = {
                "collectionName": table,
                "topicName": topic,
                "bagIds": bag_ids,
            }
            resp = self._webapp_client.do_request(
                WebappClient.POST,
                "/mongo/batch-frames-query",
                json=params,
            )
            if resp.get("code") == 200:
                data = resp.get("data")
                if isinstance(data, list):
                    for record in data:
                        bid = record.get("bag_id", "")
                        prefix = record.get("storage_prefix", "")
                        if bid and prefix:
                            bid_to_prefix[bid] = prefix
        except Exception:
            pass

        # 2. 线程池并发做 OSS list_directory，完成的 bag 立即 yield
        def _list_oss(bid: str) -> Tuple[str, List[str]]:
            storage_prefix = bid_to_prefix.get(bid, "")
            if not storage_prefix:
                return bid, []
            bucket_name, prefix = (
                self._oss_tool.get_bucket_name_and_remote_prefix(
                    storage_prefix
                )
            )
            if not bucket_name or not prefix:
                return bid, []
            try:
                file_keys = self._oss_tool.list_directory(
                    table, bucket_name, prefix
                )
            except Exception:
                return bid, []
            paths = []
            for idx, key in enumerate(file_keys):
                if frame_interval > 1 and idx % frame_interval != 0:
                    continue
                paths.append(f"oss://{bucket_name}/{key}")
            return bid, paths

        with ThreadPoolExecutor(max_workers=_max_workers) as pool:
            futures = {pool.submit(_list_oss, bid): bid for bid in bag_ids}
            for future in as_completed(futures):
                bid, paths = future.result()
                yield bid, paths

    def download_frames(
        self,
        bag_id: str,
        topic: str,
        local_dir: str,
        table: Optional[str] = None,
        timestamp_range: Optional[Tuple[int, int]] = None,
        frame_interval: int = 1,
    ) -> Dict[str, Any]:
        """下载图片帧到本地目录

        Args:
            bag_id: bag 包唯一标识
            topic: topic 名称
            local_dir: 本地目录
            table: 可选，指定原始数据表（bag_metadata 表名）
            timestamp_range: 可选，时间戳范围（开始，结束）
            frame_interval: 采样间隔，每 N 帧取 1 帧，默认 1（不跳帧）

        Returns:
            dict: {
                "success": int,  # 文件成功数
                "failed": list   # 失败文件列表 (str)
            }
        """
        table = table or self.table

        # 校验 timestamp_range
        if timestamp_range:
            start_ts, end_ts = timestamp_range
            if start_ts > end_ts:
                raise ValueError(
                    f"timestamp_range 无效: start_ts ({start_ts}) 不能大于 end_ts ({end_ts})"
                )

        all_oss_paths = self.get_frames(bag_id, topic, table, frame_interval)
        # 判断 timestamp range
        if timestamp_range:
            start_ts, end_ts = timestamp_range
            filtered_paths = []
            for oss_path in all_oss_paths:
                # 从 OSS 路径中提取文件名（纳秒时间戳）
                filename = oss_path.split("/")[-1]
                # 移除文件扩展名，获取纯时间戳
                timestamp_str = (
                    filename.rsplit(".", 1)[0] if "." in filename else filename
                )
                try:
                    timestamp = int(timestamp_str)
                    if start_ts <= timestamp <= end_ts:
                        filtered_paths.append(oss_path)
                except (ValueError, IndexError):
                    # 如果无法解析时间戳，跳过该文件
                    continue
            all_oss_paths = filtered_paths

        if not all_oss_paths:
            return {"success": 0, "failed": []}

        # 批量下载文件
        download_results = self._oss_tool.download_files(
            table, all_oss_paths, local_dir, max_workers=5
        )

        # 转换返回格式
        success_count = sum(1 for r in download_results if r.get("success"))
        failed_list = [
            r.get("path", "") for r in download_results if not r.get("success")
        ]

        return {"success": success_count, "failed": failed_list}

    def iter_frames_bytes(
        self,
        bag_id: str,
        topic: str,
        table: Optional[str] = None,
        timestamp_range: Optional[Tuple[int, int]] = None,
        frame_interval: int = 1,
        max_workers: int = 16,
        read_part_size: int = None,
        read_parallel_num: int = None,
        read_block_size: int = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """迭代下载图片帧到内存（bytes），不落盘。通过生成器逐帧 yield，每帧下载完成后立即返回。

        Args:
            bag_id: bag 包唯一标识
            topic: topic 名称
            table: 可选，指定原始数据表（bag_metadata 表名）
            timestamp_range: 可选，时间戳范围（开始，结束）
            frame_interval: 采样间隔，每 N 帧取 1 帧，默认 1（不跳帧）
            max_workers: 并发下载线程数，默认 16
            read_part_size: 读取分片大小（字节），不传则使用客户端默认值。
            read_parallel_num: 并行线程数，不传则使用客户端默认值。
            read_block_size: 缓冲区块大小（字节），不传则使用客户端默认值。

        Yields:
            dict: {
                "timestamp": int,     # 纳秒时间戳（解析失败为 -1）
                "data": bytes,        # 图片二进制数据（失败时为 b""）
                "success": bool,      # 是否下载成功
                "error_msg": str,     # 失败时的错误信息
            }
        """
        table = table or self.table

        if timestamp_range:
            start_ts, end_ts = timestamp_range
            if start_ts > end_ts:
                raise ValueError(
                    f"timestamp_range 无效: start_ts ({start_ts}) 不能大于 end_ts ({end_ts})"
                )

        all_oss_paths = self.get_frames(bag_id, topic, table, frame_interval)
        if timestamp_range:
            start_ts, end_ts = timestamp_range
            filtered_paths = []
            for oss_path in all_oss_paths:
                filename = oss_path.split("/")[-1]
                timestamp_str = (
                    filename.rsplit(".", 1)[0] if "." in filename else filename
                )
                try:
                    timestamp = int(timestamp_str)
                    if start_ts <= timestamp <= end_ts:
                        filtered_paths.append(oss_path)
                except (ValueError, IndexError):
                    continue
            all_oss_paths = filtered_paths

        if not all_oss_paths:
            return

        def _extract_timestamp(oss_path: str) -> int:
            filename = oss_path.split("/")[-1]
            timestamp_str = (
                filename.rsplit(".", 1)[0] if "." in filename else filename
            )
            try:
                return int(timestamp_str)
            except (ValueError, IndexError):
                return -1

        read_kwargs = {}
        if read_part_size is not None:
            read_kwargs["part_size"] = read_part_size
        if read_parallel_num is not None:
            read_kwargs["parallel_num"] = read_parallel_num
        if read_block_size is not None:
            read_kwargs["block_size"] = read_block_size

        def _download_one(oss_path: str) -> Dict[str, Any]:
            ts = _extract_timestamp(oss_path)
            try:
                parts = oss_path[6:].split("/", 1)
                bucket_name, key = parts[0], parts[1]
                data = self._oss_tool.read_file(
                    table, bucket_name, key, **read_kwargs
                )
                return {
                    "timestamp": ts,
                    "data": data,
                    "success": True,
                    "error_msg": "",
                }
            except Exception as e:
                return {
                    "timestamp": ts,
                    "data": b"",
                    "success": False,
                    "error_msg": str(e),
                }

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_download_one, p): p for p in all_oss_paths}
            for future in as_completed(futures):
                yield future.result()
