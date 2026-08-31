import json
import logging
import threading
from importlib.metadata import version
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache

from dm_sdk.models import RespBody
from dm_sdk.tools.api import ApiBaseError, InvalidUserInputError
from dm_sdk.tools.dm_static_env import (
    DEFAULT_TIMEOUT_SECONDS,
    ENV_PROD,
    SERVICE_CERBERUS,
    SERVICE_PROD_DATA,
)
from dm_sdk.tools.oss_sts_client_manager import OSSToolManager
from dm_sdk.tools.tracker import Tracker
from dm_sdk.tools.webapp_client import WebappClient

from .models import DATA_TYPE_VIRTUAL_CLIP


class ProdDataBasic:
    def __init__(
        self,
        access_token: str,
        table: str,
        env: Optional[str] = None,
        *args,
        **kwargs,
    ):
        """
        Initialize ProdDataClient.

        Args:
            access_token: Authentication token.
            table: Default table name for operations.
            env: Environment name (dev/uat/prod)，不传时默认为 prod。
            read_part_size: OSS 读取文件时的分片大小（字节），默认 32MB。
                当文件大小 <= 分片大小时，自动使用单线程下载（无需并行）。
            read_parallel_num: OSS 读取文件时的最大并行线程数，默认 8。
                仅当文件大小 > 分片大小时才会启用多线程并行下载。
            read_block_size: OSS 读取文件时的块大小（字节），默认 1MB。
                每个线程每次读取的缓冲区大小。
        """
        # env 默认 prod
        env = env or ENV_PROD

        # service_targets 按服务名分别配置 X-Service-Target
        service_targets = kwargs.pop("service_targets", {}) or {}

        read_part_size = kwargs.pop("read_part_size", None)
        read_parallel_num = kwargs.pop("read_parallel_num", None)
        read_block_size = kwargs.pop("read_block_size", None)
        upload_part_size = kwargs.pop("upload_part_size", None)
        upload_parallel_num = kwargs.pop("upload_parallel_num", None)
        download_part_size = kwargs.pop("download_part_size", None)
        download_parallel_num = kwargs.pop("download_parallel_num", None)

        kwargs["timeout"] = DEFAULT_TIMEOUT_SECONDS
        ua = f"prod_data_sdk/{version('dm_sdk')}"
        self.table = table
        self._webapp_client = WebappClient(
            env, SERVICE_PROD_DATA,
            service_target=service_targets.get(SERVICE_PROD_DATA),
            *args, **kwargs
        )
        self._webapp_client.headers["User-Agent"] = ua
        self._webapp_client.headers["Access-Token"] = access_token

        self._cerberus_client = WebappClient(
            env,
            SERVICE_CERBERUS,
            service_target=service_targets.get(SERVICE_CERBERUS),
            *args,
            **kwargs,
        )
        self._cerberus_client.headers["User-Agent"] = ua
        self._cerberus_client.headers["Access-Token"] = access_token

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
        self._tracker = Tracker(access_token, SERVICE_PROD_DATA, env)
        self._logger = logging.getLogger(__name__)
        self._cache_lock = threading.Lock()
        # 文件路径缓存: {(data_id, table): {relative_path: (oss_table, bucket_name, key)}}
        self._file_path_cache: TTLCache = TTLCache(maxsize=128, ttl=600)
        # 缓存 {(data_id, table): storage_prefix + data_type
        self._meta_info_cache: TTLCache = TTLCache(maxsize=128, ttl=600)

    def _resolve_file_path(
        self, relative_path: str, data_id: str, table: str
    ) -> Optional[Tuple[str, str, str]]:
        """通过缓存查找 relative_path 对应的 (oss_table, bucket_name, key)。

        Args:
            relative_path: 文件在 clip/bag 中的相对路径（不含前导 /）。
            data_id: 数据 ID。
            table: 表名。

        Returns:
            (oss_table, bucket_name, key) 或 None。
        """
        cache_key = (data_id, table)

        # 读缓存加锁（TTLCache 非线程安全）
        with self._cache_lock:
            path_map = self._file_path_cache.get(cache_key)
            if path_map is not None:
                return path_map.get(relative_path)
            meta_info = self._meta_info_cache.get(cache_key)

        # cache miss：在锁外做网络请求，避免持锁阻塞其他线程
        if meta_info is None:
            _, meta = self._get_metadata(
                data_id, table, ["storage_prefix", "data_type"]
            )
            if meta.code != 200:
                raise ApiBaseError(
                    f"获取元数据失败: {meta.msg} (data_id={data_id}, table={table})",
                    trace_id=meta.trace_id,
                )
            meta_info = meta.resp_data() or {}
            storage_prefix = meta_info.get("storage_prefix", "")
            if not storage_prefix:
                raise ApiBaseError(
                    f"storage_prefix为空，无法解析文件路径 (data_id={data_id}, table={table})"
                )
            with self._cache_lock:
                self._meta_info_cache[cache_key] = meta_info
        else:
            storage_prefix = meta_info.get("storage_prefix", "")

        bucket_name, remote_prefix = (
            self._oss_tool.get_bucket_name_and_remote_prefix(storage_prefix)
        )
        data_type = meta_info.get("data_type", "")
        is_virtual = data_type == DATA_TYPE_VIRTUAL_CLIP

        if not is_virtual:
            # 普通 clip / bag: key 结构固定，直接拼接，无需列举 OSS 目录
            key = remote_prefix.rstrip("/") + "/" + relative_path
            return (table, bucket_name, key)

        # virtual clip: 文件可能分布在不同 bucket，必须从 DB 查完整 mapping
        new_map: Dict[str, Tuple[str, str, str]] = {}
        cross_buckets: set = set()

        for item in self._fetch_all_file_items(data_id, table):
            path = item.get("path")
            mapping_path = item.get("mapping_path")
            if not path:
                continue
            logical = (
                path[len(storage_prefix) :].lstrip("/")
                if path.startswith(storage_prefix)
                else path.lstrip("/")
            )
            if not logical:
                continue

            if mapping_path and mapping_path.startswith("oss://"):
                parts = mapping_path[6:].split("/", 1)
                if len(parts) == 2:
                    bn, key = parts
                    cross_buckets.add(bn)
                    new_map[logical] = (None, bn, key)
            elif path.startswith("oss://"):
                parts = path[6:].split("/", 1)
                if len(parts) == 2:
                    new_map[logical] = (table, parts[0], parts[1])

        if cross_buckets:
            bucket_table_map = self.get_bucket_name_to_table_map(
                list(cross_buckets)
            )
            for lp, val in new_map.items():
                t, bn, key = val
                if t is None:
                    new_map[lp] = (bucket_table_map.get(bn, table), bn, key)

        with self._cache_lock:
            self._file_path_cache[cache_key] = new_map
        return new_map.get(relative_path)

    def _get_storage_prefix(
        self, data_id: str, table: Optional[str] = None
    ) -> str:
        table = table or self.table
        _, meta = self._get_metadata(data_id, table, ["storage_prefix"])
        if meta.code != 200:
            raise ApiBaseError(
                f"获取storage_prefix失败: {meta.msg} (data_id={data_id}, table={table})",
                trace_id=meta.trace_id,
            )

        storage_prefix = (meta.resp_data() or {}).get("storage_prefix")
        if not storage_prefix:
            raise ApiBaseError(
                f"storage_prefix为空，无法上传/下载 (data_id={data_id}, table={table})"
            )

        return storage_prefix

    def _get_metadata(
        self,
        data_id: str,
        table: Optional[str] = None,
        fields: Optional[List[str]] = None,
    ) -> Tuple[str, RespBody]:
        table = table or self.table
        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/data/detail",
            json={"data_id": data_id, "table": table},
        )
        data = resp.get("data") or {}
        data_id = data.get("data_id", "")
        return data_id, resp.to_resp_body(filter_fields=fields)

    def _prepare_remote_prefix(self, data_id: str, table: str) -> str:
        table = table or self.table
        storage_prefix = self._get_storage_prefix(data_id, table)
        _, remote_prefix = self._oss_tool.get_bucket_name_and_remote_prefix(
            storage_prefix
        )
        return remote_prefix

    def get_bucket_name_to_table_map(
        self,
        bucket_name_list: List[str],
    ) -> Dict[str, str]:
        if not bucket_name_list:
            raise InvalidUserInputError("bucket_name_list cannot be empty")

        params = {
            "bucket_name_list": bucket_name_list,
        }

        resp = self._webapp_client.do_request(
            WebappClient.POST, "/dict/bucket-relation", json=params
        )

        if resp.get("code") != 200:
            raise ApiBaseError(
                f"获取bucket映射失败: {resp.get('message', '')} (bucket_name_list={bucket_name_list!r})",
                trace_id=resp.trace_id,
            )

        # Extract bucket_name to table_name mapping
        bucket_map = {}
        data = resp.get("data", [])
        for item in data:
            bucket_name = item.get("bucketName", "")
            table_name = item.get("tableName", "")
            if bucket_name and table_name:
                bucket_map[bucket_name] = table_name

        return bucket_map

    def get_business_info(self, table_name: str):
        params = [("table_name", table_name)]
        resp = self._webapp_client.do_request(
            WebappClient.GET, "/data/business-type", params=params
        )
        if resp.get("code") != 200:
            raise ApiBaseError(
                f"获取业务信息失败: {resp.get('message', '')} (table_name={table_name})",
                trace_id=resp.trace_id,
            )
        return resp.get("data", {})

    def get_table_type(self, table: str) -> str:
        """通过表名获取表类型（原始数据 / 产线数据）。

        Args:
            table_name: 表名。

        Returns:
            表类型字符串，"RawData" 表示原始数据，"ProdData" 表示产线数据。
        """
        params = [("tableName", table)]
        resp = self._cerberus_client.do_request(
            WebappClient.GET,
            "/dataPermission/getTableTypeByTableName",
            params=params,
        )
        if resp.get("code") != 200:
            raise ApiBaseError(
                f"获取表类型失败: {resp.get('message', '')} (table_name={table})",
                trace_id=resp.trace_id,
            )
        data = resp.get("data")
        if data == 1:
            return "RawData"
        if data == 2:
            return "ProdData"
        raise ApiBaseError(f"未知的表类型值: {data!r} (table_name={table})")

    def _fetch_all_file_items(self, data_id: str, table: str) -> List[Dict]:
        """分页拉取 /frame/file-list 的所有条目。"""
        all_items = []
        page, size = 1, 10000
        while True:
            payload = {
                "data_id": data_id,
                "table": table,
                "page": page,
                "size": size,
            }
            resp = self._webapp_client.do_request(
                WebappClient.POST, "/frame/file-list", json=payload
            )
            if resp.get("code") != 200:
                break
            page_data = resp.get("data", {})
            file_items = page_data.get("data", [])
            if not file_items:
                break
            all_items.extend(file_items)
            if page >= page_data.get("totalPages", 0):
                break
            page += 1
        return all_items

    def _should_save_file_stats(self, data_id: str, table: str) -> bool:
        """determine if file metadata should be tracked."""
        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/frame/saveFileOrNot",
            json={"data_id": data_id, "table": table},
        )
        return resp.get("data") is True

    def _get_file_size_from_db(
        self, data_id: str, table: str
    ) -> Dict[str, int]:
        existing_sizes = {}
        for item in self._fetch_all_file_items(data_id, table):
            path = item.get("path")
            if path:
                existing_sizes[path] = item.get("file_size", 0)
        return existing_sizes

    def _upload_task_fail(self, req_id: str, fail_reason: str) -> RespBody:
        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/file/upload_task_fail",
            json={"req_id": req_id, "fail_reason": fail_reason},
        )
        return resp.to_resp_body()

    def _file_pre_upload(
        self, data_id, table, clip_data: Dict[str, Dict[str, Any]]
    ) -> Optional[Tuple[str, int, int, int]]:
        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/file/pre-update",
            json={"data_id": data_id, "table": table, "clip_data": clip_data},
        )
        if resp.get("code") != 200:
            return None
        d = resp.get("data", {})
        file_stats = d.get("file_stats") or {}
        return (
            d.get("req_id"),
            file_stats.get("file_cnt") or 0,
            file_stats.get("total_size") or 0,
            file_stats.get("physical_size") or 0,
        )

    def _file_pre_delete(
        self, data_id: str, table: str, files: List[Dict[str, Any]]
    ) -> str:
        """调用 /file/pre-delete 预删除文件。

        Args:
            data_id: 数据 ID。
            table: 表名。
            files: 待删除文件列表，每项包含 file_key, path, mapping_path, file_size。

        Returns:
            req_id: 预删除请求 ID。

        Raises:
            ApiBaseError: 预删除失败。
        """
        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/file/pre-delete",
            json={"data_id": data_id, "table": table, "files": files},
        )
        if resp.get("code") != 200:
            raise ApiBaseError(
                f"预删除失败: {resp.get('message', '')} (data_id={data_id}, table={table})",
                trace_id=resp.trace_id,
            )
        data = resp.get("data", {}) or {}
        if isinstance(data, dict):
            return data.get("req_id", "")
        return ""

    def _get_clip_delete_candidates(
        self, data_id: str, table: str, topic: str, file_paths: List[str]
    ) -> List[Dict[str, Any]]:
        """从 DB 获取待删除文件的完整信息。

        Args:
            data_id: 数据 ID。
            table: 表名。
            file_paths: 用户传入的相对路径列表。

        Returns:
            待删除文件信息列表，每项包含 file_key, path, mapping_path, file_size, relative_path。
        """
        storage_prefix = self._get_storage_prefix(data_id, table)
        path_set = set(file_paths)
        # 提取 storage_prefix 中 bucket 之后的 prefix，用于拼接 delete_key
        prefix_parts = storage_prefix.replace("oss://", "").split("/", 1)
        key_prefix = prefix_parts[1].rstrip("/") + "/" if len(prefix_parts) == 2 else ""

        # storage_prefix 完整前缀，用于从 item.path 中截取出相对路径
        full_prefix = storage_prefix.rstrip("/") + "/"

        candidates = []
        for item in self._fetch_all_file_items(data_id, table):
            item_path = item.get("path", "")
            # item.path 可能是完整 OSS 路径，截取出相对路径
            if item_path.startswith(full_prefix):
                rel_path = item_path[len(full_prefix):]
            else:
                rel_path = item_path
            # topic 也需要一致
            if item.get("topic", "") == topic and rel_path in path_set:
                candidates.append(
                    {
                        "file_key": item.get("file_key", ""),
                        "path": rel_path,
                        "delete_key": key_prefix + rel_path.lstrip("/"),
                        "mapping_path": item.get("mapping_path", ""),
                        "file_size": item.get("file_size", 0),
                    }
                )
        return candidates

    def _delete_oss_candidates(
        self,
        data_id: str,
        table: str,
        candidates: List[Dict[str, Any]],
        bucket_name: str,
    ) -> Dict[str, Any]:
        """删除 OSS 文件，跳过 mapping 文件。

        同一 data_id + table 下所有文件都在同一个 bucket，由调用方传入。

        Args:
            data_id: 数据 ID。
            table: 表名。
            candidates: 待删除文件信息列表。
            bucket_name: OSS bucket 名称。

        Returns:
            {"success": int, "failed": List[Tuple[str, str]]}
        """
        keys: List[str] = []
        for c in candidates:
            if c.get("mapping_path"):
                # 映射文件，跳过不删 OSS
                continue
            delete_key = c.get("delete_key")
            if delete_key:
                keys.append(delete_key)

        if not keys:
            return {"success": 0, "failed": []}

        return self._oss_tool.batch_delete_objects(
            bucket_name, keys, table
        )

    def _calc_remaining_stats(
        self, data_id: str, table: str, candidates: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """计算删除后的 remaining stats。

        从 metadata 获取当前 file_stats，减去待删除文件。
        virtual-clip 的映射文件只减 total_size，不减 physical_size。

        Args:
            data_id: 数据 ID。
            table: 表名。
            candidates: 待删除文件信息列表。

        Returns:
            {"file_cnt": int, "total_size": int, "physical_size": int}
        """
        # 从 metadata 获取当前 file_stats
        _, meta = self._get_metadata(data_id, table, ["file_stats"])
        if meta.code != 200:
            raise ApiBaseError(
                f"获取 metadata 失败: {meta.msg} (data_id={data_id}, table={table})"
            )

        current_stats = (meta.resp_data() or {}).get("file_stats") or {}
        total_cnt = current_stats.get("file_cnt", 0)
        total_size = current_stats.get("total_size", 0)
        physical_size = current_stats.get("physical_size", 0)

        # 减去待删除的
        delete_cnt = 0
        delete_total = 0
        delete_physical = 0
        for c in candidates:
            size = c.get("file_size", 0)
            delete_cnt += 1
            delete_total += size
            if not c.get("mapping_path"):
                delete_physical += size

        return {
            "file_cnt": max(0, total_cnt - delete_cnt),
            "total_size": max(0, total_size - delete_total),
            "physical_size": max(0, physical_size - delete_physical),
        }

    @staticmethod
    def _filter_fields(
        item: Dict[str, Any], fields: Optional[List[str]]
    ) -> Dict[str, Any]:
        if not fields:
            return item
        return {k: item[k] for k in fields if k in item}
