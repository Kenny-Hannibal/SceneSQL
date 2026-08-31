import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import alibabacloud_oss_v2 as oss

from dm_sdk.tools.api import ApiBaseError
from dm_sdk.tools.oss_client import OssClient
from dm_sdk.tools.webapp_client import WebappClient

# 阿里云 OSS SDK


class OSSToolManager:
    def __init__(
        self,
        backend_sts_url: str,
        webapp_client: WebappClient,
        read_part_size: int = None,
        read_parallel_num: int = None,
        read_block_size: int = None,
        upload_part_size: int = None,
        upload_parallel_num: int = None,
        download_part_size: int = None,
        download_parallel_num: int = None,
    ):
        """
        :param backend_sts_url: 后端获取 STS 的 API 地址，例如 "https://api.yourdomain.com/sts"
        :param region: OSS 区域
        :param read_part_size: 读取文件时的分片大小（字节）
        :param read_parallel_num: 读取文件时的并行线程数
        :param read_block_size: 读取文件时的块大小（字节）
        :param upload_part_size: 上传分片大小（字节）
        :param upload_parallel_num: 上传并发线程数
        :param download_part_size: 下载分片大小（字节）
        :param download_parallel_num: 下载并发线程数
        """
        self.backend_sts_url = backend_sts_url
        self._clients = {}  # {bucket_name: {"client": OssClient, "expire_time": datetime}}
        self._lock = threading.Lock()
        self._logger = logging.getLogger(__name__)
        self._webapp_client = webapp_client

        # OSS 读取参数
        self._read_part_size = read_part_size
        self._read_parallel_num = read_parallel_num
        self._read_block_size = read_block_size

        # 上传/下载参数（透传给 OssClient）
        self._upload_part_size = upload_part_size
        self._upload_parallel_num = upload_parallel_num
        self._download_part_size = download_part_size
        self._download_parallel_num = download_parallel_num

    def _fetch_sts_from_backend(self, table: str) -> dict:
        """向后端请求 STS 凭证"""
        params_tuple = [
            ("collectionName", table),
        ]
        resp = self._webapp_client.do_request(
            WebappClient.GET, "/common-oss/oss-info", params=params_tuple
        )
        oss_data = resp.get("data")
        if oss_data is None:
            message = resp.get("message", "")
            raise ValueError(f"获取STS凭证异常:{message}")
        # 验证必要字段
        required = ["ak", "sk", "securityToken", "expireTime", "endPoint"]
        if not all(k in oss_data for k in required):
            raise ValueError("Backend STS response missing required fields")
        return oss_data

    def get_bucket_name_by_table(
        self,
        table: str,
        bucket_type: str,
    ) -> str:
        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/dict/table-to-bucket",
            json={"table": table, "bucket_type": bucket_type},
        )

        if resp.get("code") != 200:
            raise ApiBaseError(
                f"获取bucket信息失败: {resp.get('message', '')} (table={table})"
            )

        bucket_list = resp.get("data", [])
        if not bucket_list:
            raise ApiBaseError(f"未配置bucket (table={table})")

        return bucket_list[0].get("bucketName")

    def _get_or_create_client(self, table: str) -> OssClient:
        """获取或创建（带缓存）对应 bucket 的 OssClient"""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            # 检查缓存是否存在且未过期（提前 15 分钟刷新）
            if table in self._clients:
                cached = self._clients[table]
                if cached["expire_time"] > now + timedelta(minutes=15):
                    return cached["client"]

            # 获取新 STS
            self._logger.info("表[%s]  正在向后端申请 STS 凭证...", table)
            sts = self._fetch_sts_from_backend(table)
            expire_time = datetime.strptime(
                sts["expireTime"], "%Y-%m-%dT%H:%M:%SZ"
            )
            client = OssClient(
                read_part_size=self._read_part_size,
                read_parallel_num=self._read_parallel_num,
                read_block_size=self._read_block_size,
                upload_part_size=self._upload_part_size,
                upload_parallel_num=self._upload_parallel_num,
                download_part_size=self._download_part_size,
                download_parallel_num=self._download_parallel_num,
            )
            client.initialize(
                sts.get("ak"),
                sts.get("sk"),
                sts.get("endPoint"),
                sts.get("securityToken"),
                sts.get("usePathStyle"),
            )
            # 缓存
            self._clients[table] = {
                "client": client,
                "expire_time": expire_time,
            }
            self._logger.info(
                "表[%s] ✅ STS 凭证已缓存，有效期至: %s",
                table,
                expire_time,
            )
            return client

    def list_directory(
        self,
        table: str,
        bucket_name: str,
        prefix: str = "",
        delimiter: str = "/",
    ) -> List[str]:
        if not prefix.endswith(delimiter):
            prefix += delimiter

        client = self._get_or_create_client(table)
        keys = client.list_directory(bucket_name, prefix)
        return keys

    def download_file(
        self, table: str, bucket_name: str, key: str, local_dir: str
    ) -> Tuple[str, bool, Optional[str]]:
        client = self._get_or_create_client(table)
        local_dir_parent = os.path.dirname(local_dir)
        os.makedirs(local_dir_parent, exist_ok=True)
        client.download_file(bucket_name, key, local_dir)
        return key, True, None

    def download_directory(
        self,
        table: str,
        remote_prefix: str,
        local_dir: str,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """
        批量下载目录或文件（支持 cp 风格目标路径）
        :param table: 表名
        :param remote_prefix: OSS 目录前缀或文件，如 "oss://bucket/path/dir" 或 "oss://bucket/path/file"
        :param local_dir: 本地目标路径
            - 目录语义：类似 cp -r a b/，会下载到 b/<a>/
            - 重命名语义：类似 cp -r a b，会下载到 b/ 下（目录名改为 b）
            - 若 remote_prefix 为文件，则下载到本地目录 local_dir 下
        :param max_workers: 并发线程数
        :return: {"success": int, "failed": List[tuple]}
        """
        if not remote_prefix.startswith("oss://"):
            raise ValueError("Invalid OSS URI")
        # 去掉 "oss://" 前缀
        parts = remote_prefix[6:].split("/", 1)  # 只分割一次

        if len(parts) != 2:
            raise ValueError("Invalid OSS URI format")

        bucket_name = parts[0]
        prefix = parts[1]
        client = self._get_or_create_client(table)

        def _ensure_dir_with_existing_parent(dest_path: str) -> None:
            normalized_path = dest_path.rstrip(os.sep)
            if not normalized_path:
                raise ValueError("本地路径不能为空")
            if os.path.exists(dest_path):
                if not os.path.isdir(dest_path):
                    raise ValueError(f"本地路径不是目录: {dest_path}")
                return
            parent_dir = os.path.dirname(normalized_path)
            if not parent_dir or not os.path.isdir(parent_dir):
                raise ValueError(
                    f"本地目录找不到: {parent_dir or normalized_path}"
                )
            os.mkdir(dest_path)

        # 先判断是否是文件
        if client.exist_object(bucket_name, prefix):
            _ensure_dir_with_existing_parent(local_dir)
            local_path = os.path.join(local_dir, os.path.basename(prefix))
            self._logger.info(
                "[%s] 正在下载文件: %s -> %s",
                bucket_name,
                prefix,
                local_path,
            )
            try:
                self.download_file(table, bucket_name, prefix, local_path)
                return {"success": 1, "failed": []}
            except Exception as e:
                return {"success": 0, "failed": [(prefix, str(e))]}

        # 目录下载逻辑（cp 语义）
        _ensure_dir_with_existing_parent(local_dir)
        source_dir_name = os.path.basename(prefix.rstrip("/"))
        target_root = (
            os.path.join(local_dir, source_dir_name)
            if local_dir.endswith(os.sep)
            else local_dir
        )

        self._logger.info("[%s] 正在列举目录: %s", bucket_name, prefix)
        file_keys = self.list_directory(table, bucket_name, prefix)
        if not file_keys:
            print(f" [{bucket_name}] 目录为空或不存在: {prefix}")
            return {"success": 0, "failed": []}

        self._logger.info(
            " [%s] 发现 %s 个文件，开始下载到 %s...",
            bucket_name,
            len(file_keys),
            target_root,
        )
        success_count = 0
        failed_files = []
        base_prefix = prefix if prefix.endswith("/") else prefix + "/"

        # 预创建所有目标目录
        unique_dirs = {
            os.path.dirname(
                os.path.join(target_root, file_key[len(base_prefix) :])
            )
            for file_key in file_keys
        }
        for d in unique_dirs:
            os.makedirs(d, exist_ok=True)

        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="OSS-Downloader"
        ) as executor:
            future_to_key = {}
            for file_key in file_keys:
                rel_path = file_key[len(base_prefix) :]
                local_path = os.path.join(target_root, rel_path)
                future = executor.submit(
                    self.download_file,
                    table,
                    bucket_name,
                    file_key,
                    local_path,
                )
                future_to_key[future] = file_key
            for future in as_completed(future_to_key):
                file_key, ok, each_err = future.result()
                if ok:
                    success_count += 1
                else:
                    failed_files.append((file_key, each_err))

        self._logger.info(
            "[%s] 下载完成！成功: %s, 失败: %s",
            bucket_name,
            success_count,
            len(failed_files),
        )
        return {"success": success_count, "failed": failed_files}

    def download_files(
        self, table, paths, target_dir, max_workers
    ) -> List[Dict[str, Any]]:
        """
        批量下载整个目录
        :param table: 表名
        :param paths: OSS key，如 "oss://gacrnd-ali-collection-issue/50930_112610/gac-hmi-parking_to_idc.bin"
        :param target_dir: 本地保存根目录
        :param max_workers: 并发线程数
        :return: List[Dict]，每项为 {"path": str, "success": bool, "error_msg": str}
        """
        results = []
        file_keys = []
        key_to_path = {}
        for path in paths:
            if not path.startswith("oss://"):
                results.append(
                    {"path": path, "success": False, "error_msg": "路径不合法"}
                )
                continue
            # 去掉 "oss://" 前缀
            parts = path[6:].split("/", 1)  # 只分割一次
            if len(parts) != 2:
                results.append(
                    {"path": path, "success": False, "error_msg": "路径不合法"}
                )
                continue

            bucket_name = parts[0]
            oss_key = parts[1]
            file_keys.append((bucket_name, oss_key))
            key_to_path[oss_key] = path
        if not file_keys:
            return results

        self._logger.info(
            " 发现 %s 个文件，开始下载到 %s...",
            len(file_keys),
            target_dir,
        )
        success_count = 0
        failed_count = 0

        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="OSS-Downloader"
        ) as executor:
            future_to_key = {
                executor.submit(
                    self.download_file,
                    table,
                    oss_key[0],
                    oss_key[1],
                    os.path.join(target_dir, os.path.basename(oss_key[1])),
                ): oss_key
                for oss_key in file_keys
            }
            for future in as_completed(future_to_key):
                file_key, ok, each_err = future.result()
                oss_path = key_to_path[file_key]
                if ok:
                    success_count = success_count + 1
                    results.append(
                        {"path": oss_path, "success": True, "error_msg": ""}
                    )
                else:
                    failed_count = failed_count + 1
                    results.append(
                        {
                            "path": oss_path,
                            "success": False,
                            "error_msg": each_err,
                        }
                    )

        self._logger.info(
            "下载完成! 成功: %s, 失败: %s",
            success_count,
            failed_count,
        )
        return results

    def download_files_with_mapping(
        self,
        bucket_table_map: Dict[str, str],
        file_mappings: List[Dict],
        target_dir: str,
        max_workers: int,
    ) -> List[Dict[str, Any]]:
        """
        批量下载文件，支持物理路径和逻辑路径映射
        :param table: 表名
        :param file_mappings: 文件映射列表，每个元素包含:
            - logical_path: 保存到本地时使用的逻辑路径
            - physical_path: 实际下载的 OSS 路径 (oss://bucket/...)
        :param target_dir: 本地保存根目录
        :param max_workers: 并发线程数
        Returns:
            List of dicts: {"path": str, "success": bool, "error_msg": str}
        """
        results = []
        download_tasks = []

        for mapping in file_mappings:
            physical_path = mapping["physical_path"]
            logical_path = mapping["logical_path"]

            local_path = os.path.join(target_dir, logical_path)
            parts = physical_path[6:].split("/", 1)
            bucket_name = parts[0]
            oss_key = parts[1]
            download_tasks.append(
                {
                    "bucket_name": bucket_name,
                    "oss_key": oss_key,
                    "local_path": local_path,
                    "logical_path": logical_path,
                }
            )
        if not download_tasks:
            return results

        # 预创建所有目标目录，避免多线程并发 makedirs 的重复系统调用
        unique_dirs = {
            os.path.dirname(task["local_path"]) for task in download_tasks
        }
        for d in unique_dirs:
            os.makedirs(d, exist_ok=True)

        self._logger.info(
            "发现 %s 个文件，开始下载到 %s...",
            len(download_tasks),
            target_dir,
        )
        success_count = 0
        failed_count = 0
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="OSS-Downloader"
        ) as executor:
            future_to_task = {
                executor.submit(
                    self.download_file,
                    bucket_table_map[task["bucket_name"]],
                    task["bucket_name"],
                    task["oss_key"],
                    task["local_path"],
                ): task
                for task in download_tasks
            }

            for future in as_completed(future_to_task):
                task = future_to_task[future]
                try:
                    _, ok, each_err = future.result()
                    if ok:
                        success_count += 1
                        results.append(
                            {
                                "path": task["logical_path"],
                                "success": True,
                                "error_msg": "",
                            }
                        )
                    else:
                        failed_count += 1
                        results.append(
                            {
                                "path": task["logical_path"],
                                "success": False,
                                "error_msg": each_err or "未知错误",
                            }
                        )
                except Exception as e:
                    failed_count += 1
                    results.append(
                        {
                            "path": task["logical_path"],
                            "success": False,
                            "error_msg": str(e),
                        }
                    )

        self._logger.info(
            "下载完成！成功: %s, 失败: %s",
            success_count,
            failed_count,
        )
        return results

    def upload_directory(
        self,
        table: str,
        bucket_name: str,
        remote_prefix: str,
        local_dir: str,
        max_workers: int = 16,
    ) -> Tuple[List[Dict], List[str]]:
        # 验证本地目录是否存在
        if not os.path.isdir(local_dir):
            raise ValueError(f"本地目录找不到: {local_dir}")
        # 先将 local_dir 转为绝对路径
        abs_local_dir = os.path.abspath(local_dir)
        # 生成文件列表
        file_list = self._generate_file_list(abs_local_dir)
        results = []
        files_fail = []

        # 1. 构建所有文件的 oss_key 列表
        file_to_key_map = {}
        oss_keys = []
        for local_file in file_list:
            oss_key = self._get_oss_key_for_file(
                local_file, remote_prefix, abs_local_dir
            )
            file_to_key_map[local_file] = oss_key
            oss_keys.append(oss_key)

        # 2. 批量并发获取远程文件信息
        self._logger.info(
            "批量获取 %s 个文件的远程信息...",
            len(oss_keys),
        )
        remote_info_map = self._batch_fetch_remote_info(
            table, bucket_name, oss_keys, max_workers=max_workers
        )

        existing_count = sum(
            1 for info in remote_info_map.values() if info.get("exists")
        )
        self._logger.info(
            "远程已存在 %s/%s 个文件",
            existing_count,
            len(oss_keys),
        )

        # 3. 使用线程池并发上传文件
        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="OSS-Uploader"
        ) as executor:
            futures = []

            for local_file in file_list:
                oss_key = file_to_key_map[local_file]
                remote_info = remote_info_map.get(oss_key)

                # 创建上传任务（传入预先获取的远程信息）
                future = executor.submit(
                    self._upload_file,
                    table,
                    bucket_name,
                    local_file,
                    oss_key,
                    remote_info,
                )
                futures.append((local_file, oss_key, future))

            # 4. 处理结果
            for local_file, oss_key, future in futures:
                try:
                    status, err = future.result()
                    if status:
                        results.append(
                            {
                                "status": "success",
                                "local_file": local_file,
                                "oss_key": oss_key,
                            }
                        )
                    else:
                        results.append(
                            {
                                "status": "failed",
                                "local_file": local_file,
                                "oss_key": None,
                                "result": None,
                                "error": f"Unexpected error: {str(err)}",
                            }
                        )
                        files_fail.append(local_file)

                except Exception as e:
                    # 处理线程池本身的异常
                    self._logger.error(
                        "Unexpected error processing %s: %s",
                        local_file,
                        e,
                    )
                    results.append(
                        {
                            "status": "failed",
                            "local_file": local_file,
                            "oss_key": None,
                            "result": None,
                            "error": f"Unexpected error: {str(e)}",
                        }
                    )
                    files_fail.append(local_file)
        return results, files_fail

    def upload_files(
        self,
        table: str,
        bucket_name: str,
        remote_prefix: str,
        file_mappings: List[Dict],
        remote_info_map: Dict[str, dict],
        max_workers: int = 16,
    ) -> Tuple[List[Dict], List[str]]:
        results = []
        files_upload_fail = []
        if not file_mappings:
            return results, files_upload_fail

        oss_keys = []
        local_to_oss_map = {}
        for file_mapping in file_mappings:
            local_file = file_mapping.get("local_file")
            remote = file_mapping.get("remote", "")
            oss_key = remote_prefix + remote
            oss_keys.append(oss_key)
            local_to_oss_map[local_file] = oss_key

        existing_count = sum(
            1 for info in remote_info_map.values() if info.get("exists")
        )
        self._logger.info(
            "远程已存在 %s/%s 个文件",
            existing_count,
            len(oss_keys),
        )

        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="OSS-Uploader"
        ) as executor:
            futures = []
            for file_mapping in file_mappings:
                local_file = file_mapping.get("local_file")
                remote = file_mapping.get("remote", "")
                oss_key = remote_prefix + remote
                remote_info = remote_info_map.get(oss_key)

                self._logger.info(
                    "开始上传文件： local_file=%s, oss_key=%s, remote_exists=%s",
                    local_file,
                    oss_key,
                    remote_info.get("exists") if remote_info else False,
                )
                future = executor.submit(
                    self._upload_file,
                    table,
                    bucket_name,
                    local_file,
                    oss_key,
                    remote_info,
                )
                futures.append((local_file, oss_key, future))

            # 4. 处理上传结果（先完成的先处理，避免大文件阻塞小文件）
            future_to_meta = {f: (lf, ok) for lf, ok, f in futures}
            for future in as_completed(future_to_meta):
                local_file, oss_key = future_to_meta[future]
                try:
                    status, err = future.result()
                    if status:
                        results.append(
                            {
                                "status": "success",
                                "local_file": local_file,
                                "oss_key": oss_key,
                            }
                        )
                    else:
                        results.append(
                            {
                                "status": "failed",
                                "local_file": local_file,
                                "oss_key": None,
                                "result": None,
                                "error": f"{err}",
                            }
                        )
                        files_upload_fail.append(local_file)
                except Exception as e:
                    self._logger.error(
                        "Unexpected error processing %s: %s",
                        local_file,
                        e,
                    )
                    results.append(
                        {
                            "status": "failed",
                            "local_file": local_file,
                            "oss_key": None,
                            "result": None,
                            "error": f"Unexpected error: {str(e)}",
                        }
                    )
                    files_upload_fail.append(local_file)
        succ = sum(1 for r in results if r.get("status") == "success")
        self._logger.info(
            "上传完成：成功 %s/%s，失败 %s",
            succ,
            len(results),
            len(files_upload_fail),
        )
        return results, files_upload_fail

    def _generate_file_list(self, local_dir: str) -> List[str]:
        """生成本地目录中的所有文件列表(递归遍历)"""
        file_paths = []
        for root, _, files in os.walk(local_dir):
            for file in files:
                local_file = os.path.join(root, file)
                file_paths.append(local_file)
        return file_paths

    def _get_oss_key_for_file(
        self, local_file: str, oss_prefix: str, local_dir: str = None
    ) -> str:
        """获取OSS中的目标key（处理路径分隔符）"""
        oss_file = local_file
        # 计算相当于local_dir的相对路径
        if local_dir is not None:
            rel_path = os.path.relpath(local_file, local_dir)
            # 系统的斜杠全部替换为/
            oss_file = rel_path.replace(os.sep, "/")
            # 确保oss_prefix以/结尾
            oss_prefix = oss_prefix.rstrip("/") + "/"
        # 构建OSS key
        return oss_prefix + oss_file

    def _upload_file(
        self,
        table: str,
        bucket_name: str,
        local_file: str,
        oss_key: str,
        remote_info: Optional[dict] = None,
    ):
        client = self._get_or_create_client(table)
        return client.upload_file(
            bucket_name, oss_key, local_file, remote_info
        )

    def _fetch_remote_file_info(
        self, table: str, bucket_name: str, oss_key: str
    ) -> dict:
        try:
            client = self._get_or_create_client(table)
            result = client.head_object(bucket_name, oss_key)
            return {
                "exists": True,
                "etag": result.etag,
                "size": result.content_length,
            }
        except Exception:
            return {"exists": False, "etag": None, "size": 0}

    def _batch_fetch_remote_info(
        self,
        table: str,
        bucket_name: str,
        oss_keys: List[str],
        max_workers: int = 16,
    ) -> Dict[str, dict]:
        remote_info_map = {}

        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="OSS-InfoFetcher"
        ) as executor:
            future_to_key = {
                executor.submit(
                    self._fetch_remote_file_info, table, bucket_name, oss_key
                ): oss_key
                for oss_key in oss_keys
            }

            for future in as_completed(future_to_key):
                oss_key = future_to_key[future]
                try:
                    remote_info = future.result()
                    remote_info_map[oss_key] = remote_info
                except Exception as e:
                    self._logger.error(
                        "获取远程文件信息失败 %s: %s",
                        oss_key,
                        e,
                    )
                    remote_info_map[oss_key] = {
                        "exists": False,
                        "etag": None,
                        "size": 0,
                    }

        return remote_info_map

    def get_bucket_name_and_remote_prefix(
        self, storage_prefix: str
    ) -> Tuple[str, str]:
        """Extract bucket name and remote prefix

        Example:
        - oss://cn-ad-collection-issue/clip/6969adf15712453ff7607b8d/
            -> cn-ad-collection-issue, clip/6969adf15712453ff7607b8d/
        """
        if not storage_prefix.startswith("oss://"):
            return "", storage_prefix

        parts = storage_prefix[6:].split("/", 1)
        bucket_name = parts[0]
        remote_prefix = parts[1] if len(parts) > 1 else ""
        return bucket_name, remote_prefix

    def delete_object(
        self, table: str, bucket_name: str, key: str
    ) -> Tuple[str, bool, Optional[str]]:
        """删除单个 OSS 对象。

        Returns:
            (key, success, error_msg)
        """
        client = self._get_or_create_client(table)
        try:
            ok = client.delete_object(bucket_name, key)
            if ok:
                return key, True, None
            return key, False, "删除失败"
        except Exception as e:
            return key, False, str(e)

    def batch_delete_objects(
        self,
        bucket_name: str,
        keys: List[str],
        table: str,
    ) -> Dict[str, Any]:
        """批量删除 OSS 对象（单次最多 1000 个，自动分片）。

        Args:
            bucket_name: OSS bucket 名称。
            keys: 要删除的对象 key 列表。
            table: 表名，用于获取 STS 凭证。

        Returns:
            {"success": int, "failed": List[Tuple[str, str]]}
        """
        total_success = 0
        all_failed: List[Tuple[str, str]] = []

        if not keys:
            return {"success": 0, "failed": []}

        client = self._get_or_create_client(table)
        # 阿里云单次批量删除最多 1000 个，分片处理
        chunk_size = 1000
        for i in range(0, len(keys), chunk_size):
            chunk = keys[i : i + chunk_size]
            try:
                result = client.batch_delete_objects(bucket_name, chunk)
                total_success += len(result.get("success", []))
                failed_keys = result.get("failed", [])
                all_failed.extend(
                    [(k, "批量删除失败") for k in failed_keys]
                )
            except Exception as e:
                self._logger.error(
                    "批量删除异常：bucket=%s, count=%s, err=%s",
                    bucket_name,
                    len(chunk),
                    e,
                )
                all_failed.extend([(k, str(e)) for k in chunk])

        return {"success": total_success, "failed": all_failed}

    def delete_by_prefix(
        self,
        table: str,
        bucket_name: str,
        prefix: str,
        max_workers: int = 5,
    ) -> Dict[str, Any]:
        """按前缀流式删除 OSS 对象（边列举边并发删除，统一返回结果）。

        单线程顺序列举，每满 1000 个 key 提交到线程池异步批量删除；
        等所有删除任务完成后统一返回成功/失败汇总，不会中途停止。

        Args:
            table: 表名，用于获取 STS 凭证。
            bucket_name: OSS bucket 名称。
            prefix: 对象前缀。若不以 '/' 结尾且非空，会自动追加 '/'。
            max_workers: 并发删除线程数，默认 5。

        Returns:
            {"success": int, "failed": List[Tuple[str, str]]}
        """
        if prefix and not prefix.endswith("/"):  
            prefix += "/"

        client = self._get_or_create_client(table)
        paginator = client._oss_client.list_objects_v2_paginator()

        total_success = [0]
        all_failed: List[Tuple[str, str]] = []
        lock = threading.Lock()

        def delete_batch(keys: List[str]) -> None:
            try:
                result = client.batch_delete_objects(bucket_name, keys)
                with lock:
                    total_success[0] += len(result.get("success", []))
                    failed = result.get("failed", [])
                    if failed:
                        all_failed.extend(
                            [(k, "批量删除失败") for k in failed]
                        )
            except Exception as e:
                with lock:
                    all_failed.extend([(k, str(e)) for k in keys])

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            batch: List[str] = []
            for page in paginator.iter_page(
                oss.ListObjectsV2Request(
                    bucket=bucket_name,
                    prefix=prefix,
                    max_keys=1000,
                )
            ):
                if page is None or page.contents is None:
                    continue
                for o in page.contents:
                    batch.append(o.key)
                    if len(batch) >= 1000:
                        executor.submit(delete_batch, batch[:])
                        batch = []
            if batch:
                executor.submit(delete_batch, batch[:])

        return {"success": total_success[0], "failed": all_failed}

    def read_file(
        self, table: str, bucket_name: str, key: str, **kwargs
    ) -> bytes:
        client = self._get_or_create_client(table)
        return client.get_object_bytes(bucket_name, key, **kwargs)

    def list_dir(
        self, table: str, bucket_name: str, prefix: str
    ) -> Dict[str, List[str]]:
        """List top-level folder names and file names directly under prefix."""
        client = self._get_or_create_client(table)
        return client.list_dir(bucket_name, prefix)

    def list_dir_recursive(
        self,
        table: str,
        bucket_name: str,
        prefix: str,
        depth: int = 1,
    ) -> Dict[str, Any]:
        """递归列举 OSS 目录结构，支持 depth 控制递归深度。

        Args:
            table: 表名（用于获取 STS 凭证）。
            bucket_name: OSS bucket 名称。
            prefix: OSS 目录前缀。
            depth: 递归深度。
                - 1: 仅列出当前目录下的文件和文件夹（delimiter 单层列举，高效）。
                - N > 1: 递归展开 N 层子目录，使用全量列举 + 内存构建目录树。
                - -1: 递归展开所有层级。

        Returns:
            目录树结构::

                {
                    "path": "当前目录相对路径",
                    "folders": ["dir1", "dir2"],   # depth=1 时为字符串列表
                    "files": ["file1", "file2"]
                }

            当 depth > 1 时，folders 中的元素变为::

                {"name": "dir1", "children": {"path": ..., "folders": [...], "files": [...]}}
        """
        if prefix and not prefix.endswith("/"):
            prefix += "/"

        if depth == 1:
            # 单层列举，使用 delimiter 高效获取
            result = self.list_dir(table, bucket_name, prefix)
            return {
                "path": prefix,
                "folders": result["folders"],
                "files": result["files"],
            }

        # depth > 1 或 -1: 全量列举 + 内存构建目录树
        all_keys = self.list_directory(table, bucket_name, prefix)

        # 将完整 key 转为相对路径
        rel_paths = []
        for key in all_keys:
            if key.startswith(prefix):
                rel = key[len(prefix) :]
                if rel:
                    rel_paths.append(rel)

        return self._build_dir_tree(rel_paths, prefix, depth)

    @staticmethod
    def _build_dir_tree(
        rel_paths: List[str], prefix: str, depth: int
    ) -> Dict[str, Any]:
        """从扁平相对路径列表构建目录树。

        Args:
            rel_paths: 相对于 prefix 的路径列表（如 ["a.txt", "sub/b.txt", "sub/deep/c.txt"]）。
            prefix: 当前目录的完整路径前缀。
            depth: 剩余递归深度。1=当前层，-1=无限。

        Returns:
            目录树字典。
        """
        folders_dict: Dict[
            str, List[str]
        ] = {}  # folder_name -> 该文件夹下的相对路径
        files: List[str] = []

        for rel in rel_paths:
            parts = rel.split("/", 1)
            if len(parts) == 1:
                # 文件（无 / 分隔）
                if parts[0]:
                    files.append(parts[0])
            else:
                # 目录下的文件
                folder_name = parts[0]
                if folder_name:
                    if folder_name not in folders_dict:
                        folders_dict[folder_name] = []
                    folders_dict[folder_name].append(parts[1])

        if depth == 1:
            return {
                "path": prefix,
                "folders": sorted(folders_dict.keys()),
                "files": files,
            }

        # 递归展开子目录
        next_depth = depth - 1 if depth > 1 else -1
        expanded_folders = []
        for name in sorted(folders_dict.keys()):
            sub_prefix = prefix + name + "/"
            children = OSSToolManager._build_dir_tree(
                folders_dict[name], sub_prefix, next_depth
            )
            expanded_folders.append({"name": name, "children": children})

        return {"path": prefix, "folders": expanded_folders, "files": files}
