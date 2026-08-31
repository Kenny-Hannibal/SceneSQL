import hashlib
import io
import logging
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

import alibabacloud_oss_v2 as oss

# 默认 checkpoint 目录（系统临时目录，避免污染用户工作目录）
_DEFAULT_UPLOAD_CHECKPOINT_DIR = os.path.join(
    tempfile.gettempdir(), ".dm_sdk_oss_upload_checkpoint"
)
_DEFAULT_DOWNLOAD_CHECKPOINT_DIR = os.path.join(
    tempfile.gettempdir(), ".dm_sdk_oss_download_checkpoint"
)


class UploadFile:
    def __init__(self, bucket_name: str, key: str, filepath: str):
        self.bucket_name = bucket_name
        self.key = key
        self.filepath = filepath


class OssClient:
    """
    OSS 下载/上传包装类
    """

    # 读取参数
    READ_PART_SIZE = 6 * 1024 * 1024  # 6MB
    READ_PARALLEL_NUM = 3
    READ_BLOCK_SIZE = 16 * 1024  # 16KB

    # 上传参数
    UPLOAD_PART_SIZE = 6 * 1024 * 1024  # 6MB
    UPLOAD_PARALLEL_NUM = 3

    # 下载参数
    DOWNLOAD_PART_SIZE = 6 * 1024 * 1024  # 6MB
    DOWNLOAD_PARALLEL_NUM = 3

    # 小文件阈值：≤10MB 的文件直接 PUT，不走 Uploader
    SMALL_FILE_LIMIT = 10 * 1024 * 1024

    def __init__(
        self,
        read_part_size: int = None,
        read_parallel_num: int = None,
        read_block_size: int = None,
        upload_part_size: int = None,
        upload_parallel_num: int = None,
        download_part_size: int = None,
        download_parallel_num: int = None,
    ):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        self._oss_client = None
        self._downloader: oss.Downloader = None  # 下载到磁盘
        self._reader: oss.Downloader = None  # 读取到内存
        self._uploader: oss.Uploader = None
        self._logger = logging.getLogger(__name__)

        # 读取文件时的分片参数
        self._read_part_size = read_part_size or self.READ_PART_SIZE
        self._read_parallel_num = read_parallel_num or self.READ_PARALLEL_NUM
        self._read_block_size = read_block_size or self.READ_BLOCK_SIZE

        # 上传参数
        self._upload_part_size = upload_part_size or self.UPLOAD_PART_SIZE
        self._upload_parallel_num = (
            upload_parallel_num or self.UPLOAD_PARALLEL_NUM
        )

        # 下载参数
        self._download_part_size = (
            download_part_size or self.DOWNLOAD_PART_SIZE
        )
        self._download_parallel_num = (
            download_parallel_num or self.DOWNLOAD_PARALLEL_NUM
        )

    def initialize(
        self,
        access_key_id: str,
        access_key_secret: str,
        endpoint: str,
        security_token: Optional[str] = None,
        use_path_style: bool = False,
    ):
        if security_token:
            credentials_provider = oss.credentials.StaticCredentialsProvider(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
                security_token=security_token,
            )
        else:
            credentials_provider = oss.credentials.StaticCredentialsProvider(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
            )
        oss_config = oss.config.load_default()
        oss_config.credentials_provider = credentials_provider
        oss_config.region = "cn-wulanchabu"
        oss_config.endpoint = endpoint
        oss_config.use_path_style = use_path_style
        self._oss_client = oss.Client(oss_config)

        self._uploader = self._oss_client.uploader(
            enable_checkpoint=True,
            checkpoint_dir=_DEFAULT_UPLOAD_CHECKPOINT_DIR,
            part_size=self._upload_part_size,
            parallel_num=self._upload_parallel_num,
        )

        self._downloader = self._oss_client.downloader(
            part_size=self._download_part_size,
            enable_checkpoint=True,
            checkpoint_dir=_DEFAULT_DOWNLOAD_CHECKPOINT_DIR,
            parallel_num=self._download_parallel_num,
        )
        self._reader = self._oss_client.downloader(
            part_size=self._read_part_size,
            parallel_num=self._read_parallel_num,
            block_size=self._read_block_size,
        )

    def download_file(
        self, bucket_name: str, key: str, filepath: str
    ) -> oss.DownloadResult:
        self._logger.debug(
            "下载文件：bucket=%s, key=%s, filepath=%s",
            bucket_name,
            key,
            filepath,
        )
        return self._downloader.download_file(
            oss.GetObjectRequest(bucket=bucket_name, key=key),
            filepath=filepath,
        )

    def _check_etag_and_skip(
        self,
        bucket_name: str,
        key: str,
        filepath: str,
        remote_info: Optional[dict],
    ) -> bool:
        """检查远程文件是否已存在且 ETag 匹配，匹配则返回 True（跳过上传）。"""
        if remote_info is not None:
            if remote_info.get("exists"):
                remote_etag = str(remote_info.get("etag", "")).strip('"')
                local_etag = self._calculate_local_etag(filepath)
                if local_etag.lower() == remote_etag.lower():
                    self._logger.info(
                        "文件已存在，无需上传：bucket=%s, key=%s",
                        bucket_name,
                        key,
                    )
                    return True
        else:
            if self.exist_object(bucket_name, key):
                remote_result = self.head_object(bucket_name, key)
                remote_etag = str(remote_result.etag).strip('"')
                local_etag = self._calculate_local_etag(filepath)
                if local_etag.lower() == remote_etag.lower():
                    self._logger.info(
                        "文件已存在，无需上传：bucket=%s, key=%s",
                        bucket_name,
                        key,
                    )
                    return True
        return False

    def upload_file(
        self,
        bucket_name: str,
        key: str,
        filepath: str,
        remote_info: Optional[dict] = None,
    ):
        # ETag 去重检查
        if self._check_etag_and_skip(bucket_name, key, filepath, remote_info):
            return True, None

        file_size = os.path.getsize(filepath)

        # 小文件直接 PUT，消除 checkpoint overhead
        if file_size <= self.SMALL_FILE_LIMIT:
            self._logger.info(
                "直接上传文件：bucket=%s, key=%s, filepath=%s, size=%s",
                bucket_name,
                key,
                filepath,
                file_size,
            )
            last_err = None
            for attempt in range(3):
                if attempt > 0:
                    self._logger.warning(
                        "上传失败，3s 后重试（第 %s/2 次）：key=%s, err=%s",
                        attempt,
                        key,
                        last_err,
                    )
                    time.sleep(3)
                try:
                    with open(filepath, "rb") as f:
                        self._oss_client.put_object(
                            oss.PutObjectRequest(
                                bucket=bucket_name, key=key, body=f
                            )
                        )
                    return True, None
                except Exception as e:
                    last_err = e

            self._logger.error(
                "上传重试 2 次后仍失败：key=%s, err=%s",
                key,
                last_err,
            )
            return False, str(last_err)

        # 大文件保持 Uploader 逻辑（支持断点续传）
        self._logger.info(
            "分片上传文件：bucket=%s, key=%s, filepath=%s, size=%s",
            bucket_name,
            key,
            filepath,
            file_size,
        )
        last_err = None
        for attempt in range(3):
            if attempt > 0:
                self._logger.warning(
                    "上传失败，3s 后重试（第 %s/2 次）：key=%s, err=%s",
                    attempt,
                    key,
                    last_err,
                )
                time.sleep(3)
            try:
                self._uploader.upload_file(
                    oss.PutObjectRequest(bucket=bucket_name, key=key),
                    filepath=filepath,
                )
                return True, None
            except Exception as e:
                last_err = e

        self._logger.error(
            "上传重试 2 次后仍失败：key=%s, err=%s",
            key,
            last_err,
        )
        return False, str(last_err)

    def _calculate_local_etag(self, filepath: str) -> str:
        """
        计算本地文件的 ETag
        对于大文件（超过 part_size），使用分块上传的 ETag 计算方式
        """
        file_size = os.path.getsize(filepath)
        part_size = self.UPLOAD_PART_SIZE

        # 如果文件小于 part_size，直接计算 MD5
        if file_size <= part_size:
            md5_hash = hashlib.md5()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5_hash.update(chunk)
            return md5_hash.hexdigest()

        # 大文件使用分块上传的 ETag 计算方式
        # 1. 计算每个分块的 MD5
        part_md5_list = []
        with open(filepath, "rb") as f:
            while True:
                part_data = f.read(part_size)
                if not part_data:
                    break
                part_md5 = hashlib.md5(
                    part_data
                ).digest()  # 注意这里使用 digest() 而不是 hexdigest()
                part_md5_list.append(part_md5)

        # 2. 将所有分块的 MD5 拼接后再计算 MD5
        combined_md5 = hashlib.md5(b"".join(part_md5_list)).hexdigest()

        # 3. 返回格式：{md5}-{分块数量}
        return f"{combined_md5}-{len(part_md5_list)}"

    def batch_upload_file(
        self, upload_file_list: List[UploadFile], max_workers: int = 16
    ):
        self._logger.debug("批量上传文件，文件数量：%s", len(upload_file_list))

        results = []
        # 使用线程池并发上传文件
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有上传任务
            future_to_upload = {
                executor.submit(
                    self.upload_file, uf.bucket_name, uf.key, uf.filepath
                ): uf
                for uf in upload_file_list
            }
            # 收集上传结果
            for future in as_completed(future_to_upload):
                try:
                    success, err = future.result()
                    results.append(success)
                    if not success:
                        uf = future_to_upload[future]
                        self._logger.error(
                            "文件上传失败：bucket=%s, key=%s",
                            uf.bucket_name,
                            uf.key,
                        )
                except Exception as e:
                    self._logger.error("文件上传异常：%s", e)
                    results.append(False)

        return all(results)

    def head_object(self, bucket_name: str, key: str):
        self._logger.debug(
            "获取文件信息：bucket=%s, key=%s",
            bucket_name,
            key,
        )
        return self._oss_client.head_object(
            oss.HeadObjectRequest(bucket=bucket_name, key=key)
        )

    def exist_object(self, bucket_name: str, key: str) -> bool:
        try:
            self.head_object(bucket_name, key)
            return True
        except Exception:
            return False

    def list_directory(self, bucket_name: str, prefix: str):
        keys = []
        paginator = self._oss_client.list_objects_v2_paginator()

        # 遍历对象列表的每一页
        for page in paginator.iter_page(
            oss.ListObjectsV2Request(
                bucket=bucket_name,
                prefix=prefix,
                max_keys=1000,
                # 指定前缀为 "exampledir/"，即只列出该目录下的所有对象
            )
        ):
            if page is not None and page.contents is not None:
                # 遍历每一页中的对象
                for o in page.contents:
                    keys.append(o.key)

        return keys

    def get_object_bytes(self, bucket_name: str, key: str, **kwargs) -> bytes:
        buf = io.BytesIO()
        self._reader.download_to(
            oss.GetObjectRequest(bucket=bucket_name, key=key),
            buf,
            **kwargs,
        )
        return buf.getvalue()

    def delete_object(self, bucket_name: str, key: str) -> bool:
        """删除单个 OSS 对象。

        Args:
            bucket_name: OSS bucket 名称。
            key: 对象 key。

        Returns:
            删除成功返回 True，失败返回 False。
        """
        self._logger.debug("删除对象：bucket=%s, key=%s", bucket_name, key)
        try:
            self._oss_client.delete_object(
                oss.DeleteObjectRequest(bucket=bucket_name, key=key)
            )
            return True
        except Exception as e:
            self._logger.error(
                "删除对象失败：bucket=%s, key=%s, err=%s", bucket_name, key, e
            )
            return False

    def batch_delete_objects(
        self, bucket_name: str, keys: List[str]
    ) -> Dict[str, List[str]]:
        """批量删除 OSS 对象（单次最多 1000 个）。

        Args:
            bucket_name: OSS bucket 名称。
            keys: 要删除的对象 key 列表。

        Returns:
            {"success": List[str], "failed": List[str]}
        """
        if not keys:
            return {"success": [], "failed": []}

        self._logger.debug(
            "批量删除对象：bucket=%s, count=%s", bucket_name, len(keys)
        )
        try:
            result = self._oss_client.delete_multiple_objects(
                oss.DeleteMultipleObjectsRequest(
                    bucket=bucket_name,
                    objects=[oss.DeleteObject(key=k) for k in keys],
                )
            )
            deleted = [d.key for d in (result.deleted_objects or [])]
            failed = [k for k in keys if k not in deleted]
            return {"success": deleted, "failed": failed}
        except Exception as e:
            self._logger.error(
                "批量删除对象失败：bucket=%s, count=%s, err=%s",
                bucket_name,
                len(keys),
                e,
            )
            return {"success": [], "failed": keys}

    def list_dir(self, bucket_name: str, prefix: str) -> dict:
        """List top-level folders and files directly under prefix using delimiter '/'."""
        if not prefix.endswith("/"):
            prefix += "/"

        folders = []
        files = []
        paginator = self._oss_client.list_objects_v2_paginator()

        for page in paginator.iter_page(
            oss.ListObjectsV2Request(
                bucket=bucket_name,
                prefix=prefix,
                delimiter="/",
                max_keys=1000,
            )
        ):
            if page is None:
                continue
            if page.contents is not None:
                for o in page.contents:
                    name = o.key[len(prefix) :]
                    if name:
                        files.append(name)
            if page.common_prefixes is not None:
                for cp in page.common_prefixes:
                    name = cp.prefix[len(prefix) :].rstrip("/")
                    if name:
                        folders.append(name)

        return {"folders": folders, "files": files}
