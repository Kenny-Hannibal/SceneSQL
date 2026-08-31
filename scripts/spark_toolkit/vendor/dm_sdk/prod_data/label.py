import os
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from dm_sdk.models import RespBody
from dm_sdk.prod_data.base import ProdDataBasic
from dm_sdk.prod_data.models import (
    DATA_TYPE_CLIP,
    DATA_TYPE_VIRTUAL_CLIP,
    MergeLabelItem,
)
from dm_sdk.tools.api import ApiBaseError
from dm_sdk.tools.webapp_client import WebappClient


class LabelMixin(ProdDataBasic):
    """标注结果上传/下载 Mixin。"""

    _OCC_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d")

    def _get_label_bucket_and_data_type(self, table: str) -> Tuple[str, str]:
        """获取 label bucket name 和 data_type，不合法则抛异常。"""
        bucket_name = self._oss_tool.get_bucket_name_by_table(table, "label")
        if not bucket_name:
            raise ApiBaseError(f"bucket配置无效 (table={table})")

        business_info = self.get_business_info(table)
        data_type = business_info.get("data_type")
        if data_type not in (DATA_TYPE_CLIP, DATA_TYPE_VIRTUAL_CLIP):
            raise ApiBaseError(
                f"data_type无效，不支持该数据类型 (table={table}, data_type={data_type})"
            )
        return bucket_name, data_type

    def _upload_label_to_oss(
        self,
        table: str,
        bucket_name: str,
        remote_prefix: str,
        local_dir: str,
        max_workers: int,
        error_msg: str,
    ) -> Optional[RespBody]:
        """上传目录到 OSS label bucket，失败时返回错误 RespBody，成功返回 None。"""
        results, failed_files = self._oss_tool.upload_directory(
            table, bucket_name, remote_prefix, local_dir, max_workers
        )
        if failed_files:
            return RespBody(
                code=500,
                msg=error_msg,
                data={
                    "failed_files": [
                        {
                            "local_file": r.get("local_file", ""),
                            "error_msg": r.get("error", "Unknown error"),
                        }
                        for r in results
                        if r.get("status") == "failed"
                    ]
                },
            )
        return None

    def _download_label_results(
        self,
        table: str,
        endpoint: str,
        payload: Dict[str, Any],
        target_path: str,
        max_workers: int,
        path_key: str,
        error_prefix: str,
        save_subdir_fn: Optional[
            Callable[[Dict[str, Any]], Optional[str]]
        ] = None,
        raise_if_empty: bool = True,
        relative_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        通用标注结果下载流程：调用 API -> 校验 -> 遍历下载。

        Args:
            table: 表名。
            endpoint: API 路径，如 "/label/get-auto-label-result"。
            payload: 请求体。
            target_path: 本地保存目录。
            max_workers: 并发下载数。
            path_key: 从返回 item 中取路径的 key，如 "result_path" 或 "storage_prefix"。
            error_prefix: 错误信息前缀。
            save_subdir_fn: 可选函数，接收 item dict，返回子目录名（如版本号）。
            raise_if_empty: 数据为空时是否抛异常，默认 True。
            relative_path: 相对于 storage_prefix 的相对路径，支持子目录或单个文件。
        """
        resp = self._webapp_client.do_request(
            WebappClient.POST,
            endpoint,
            json=payload,
        )

        if resp.get("code") != 200:
            raise ApiBaseError(
                f"{error_prefix}: {resp.get('message', '')} (table={table})",
                trace_id=resp.trace_id,
            )

        label_data = resp.get("data", [])
        if not label_data:
            if raise_if_empty:
                raise ApiBaseError(
                    f"{error_prefix}，未找到数据 (table={table})"
                )
            return {}

        os.makedirs(target_path, exist_ok=True)
        download_result_dict = {}
        for item in label_data:
            item_path = item.get(path_key)
            if not item_path:
                continue

            # 兼容纯路径和完整 oss:// URI
            if not item_path.startswith("oss://"):
                bucket_name = self._oss_tool.get_bucket_name_by_table(
                    table, "label"
                )
                if bucket_name:
                    item_path = f"oss://{bucket_name}/{item_path.lstrip('/')}"

            if relative_path:
                rel = relative_path.strip("/")
                if rel:
                    item_path = item_path.rstrip("/") + "/" + rel

            save_path = target_path
            if save_subdir_fn:
                subdir = save_subdir_fn(item)
                if subdir:
                    save_path = os.path.join(target_path, subdir)

            download_result_dict[item_path] = (
                self._oss_tool.download_directory(
                    table,
                    item_path,
                    save_path,
                    max_workers,
                )
            )
        return download_result_dict

    def get_clip_label_types(
        self,
        data_id: str,
        dataset_version_id: str,
        project_name: Optional[str] = None,
        version: Optional[str] = None,
        table: Optional[str] = None,
    ) -> List[str]:
        """
        获取某个 clip 存在的标注类型列表。

        调用 /label-delivery/get-label-delivery-result 接口，不传入 label_type，
        从返回结果中提取所有 label_type 并去重后返回。

        Args:
            data_id: 数据 ID。
            dataset_version_id: 数据集版本 ID。
            project_name: 项目名称，可选。
            version: 版本号，可选。
            table: 表名。如果未指定，使用默认表名。

        Returns:
            该 clip 存在的标注类型列表（去重后）。

        Raises:
            ApiBaseError: 当 API 调用失败时。
        """
        table = table or self.table

        payload = {
            "table": table,
            "data_id": data_id,
            "dataset_version_id": dataset_version_id,
        }
        if project_name:
            payload["project_name"] = project_name
        if version:
            payload["version"] = version

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/label-delivery/get-label-delivery-result",
            json=payload,
        )

        if resp.get("code") != 200:
            raise ApiBaseError(
                f"获取标注交付结果失败: {resp.get('message', '')} (data_id={data_id}, table={table})",
                trace_id=resp.trace_id,
            )

        label_data = resp.get("data", [])
        label_types = {
            item.get("label_type")
            for item in label_data
            if item.get("label_type")
        }
        return sorted(list(label_types))

    def read_label_delivery_file(
        self,
        data_id: str,
        relative_path: str,
        dataset_version_id: str,
        label_type: str,
        table: Optional[str] = None,
        read_part_size: int = None,
        read_parallel_num: int = None,
        read_block_size: int = None,
    ) -> bytes:
        """
        在线读取标注交付结果文件。

        调用 /label-delivery/get-label-delivery-result 接口获取 storage_prefix，
        然后与 relative_path 拼接后读取文件内容。

        Args:
            data_id: 数据 ID。
            relative_path: 文件相对路径（相对于 storage_prefix）。
            dataset_version_id: 数据集版本 ID。
            label_type: 标注类型。
            table: 表名。如果未指定，使用默认表名。
            read_part_size: 读取分片大小（字节），不传则使用客户端默认值。
            read_parallel_num: 并行线程数，不传则使用客户端默认值。
            read_block_size: 缓冲区块大小（字节），不传则使用客户端默认值。

        Returns:
            文件内容的 bytes。

        Raises:
            ApiBaseError: 当 API 调用失败或文件不存在时。
        """
        table = table or self.table

        payload = {
            "table": table,
            "data_id": data_id,
            "dataset_version_id": dataset_version_id,
            "label_type": label_type,
        }

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/label-delivery/get-label-delivery-result",
            json=payload,
        )

        if resp.get("code") != 200:
            raise ApiBaseError(
                f"获取标注交付结果失败: {resp.get('message', '')} (data_id={data_id}, table={table})",
                trace_id=resp.trace_id,
            )

        label_data = resp.get("data", [])
        if not label_data:
            raise ApiBaseError(
                f"未找到标注交付结果 (data_id={data_id}, table={table})"
            )

        storage_prefix = label_data[0].get("storage_prefix")
        if not storage_prefix:
            raise ApiBaseError(
                f"标注交付结果中storage_prefix为空 (data_id={data_id}, table={table})"
            )

        relative_path = relative_path.lstrip("/")

        # 从 storage_prefix 解析 bucket_name 和 key_prefix
        bucket_name, key_prefix = (
            self._oss_tool.get_bucket_name_and_remote_prefix(storage_prefix)
        )
        key = f"{key_prefix.rstrip('/')}/{relative_path}"

        read_kwargs = {}
        if read_part_size is not None:
            read_kwargs["part_size"] = read_part_size
        if read_parallel_num is not None:
            read_kwargs["parallel_num"] = read_parallel_num
        if read_block_size is not None:
            read_kwargs["block_size"] = read_block_size
        return self._oss_tool.read_file(table, bucket_name, key, **read_kwargs)

    def browse_label_dir(
        self,
        data_id: str,
        dataset_version_id: str,
        label_type: str,
        path: str = "",
        depth: int = 1,
        table: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        浏览标注交付结果的 OSS 目录结构（类似 cd + ls），支持递归展开子目录。

        调用 /label-delivery/get-label-delivery-result 接口获取 storage_prefix，
        然后浏览其下的目录和文件结构。

        Args:
            data_id: 数据 ID。
            dataset_version_id: 数据集版本 ID。
            label_type: 标注类型。
            path: 相对于 storage_prefix 的目录路径，默认浏览根目录。
                无论是否带尾部斜杠效果相同，内部统一归一化处理：
                - "" 或 "/" → 浏览根目录
                - "camera" 和 "camera/" → 等价，浏览 camera 子目录
                - "camera/FLpipe" 和 "camera/FLpipe/" → 等价
            depth: 递归深度，默认 1。
                - 1: 仅列出当前目录下的文件和文件夹（类似 ls）
                - N > 1: 递归展开 N 层子目录
                - -1: 递归展开所有层级（类似 tree）
            table: 表名。如果未指定，使用默认表名。

        Returns:
            目录树结构：

                depth=1 时：
                {
                    "path": "auto_label/.../",
                    "folders": ["camera", "lidar"],
                    "files": ["overview.json"]
                }

                depth>1 时，folders 中的元素递归展开：
                {
                    "path": "auto_label/.../",
                    "folders": [
                        {
                            "name": "camera",
                            "children": {
                                "path": "auto_label/.../camera/",
                                "folders": ["FLpipe"],
                                "files": ["config.yaml"]
                            }
                        }
                    ],
                    "files": ["overview.json"]
                }

        Raises:
            ApiBaseError: 当 API 调用失败或 storage_prefix 为空时。
        """
        table = table or self.table

        payload = {
            "table": table,
            "data_id": data_id,
            "dataset_version_id": dataset_version_id,
            "label_type": label_type,
        }

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/label-delivery/get-label-delivery-result",
            json=payload,
        )

        if resp.get("code") != 200:
            raise ApiBaseError(
                f"获取标注交付结果失败: {resp.get('message', '')} (data_id={data_id}, table={table})",
                trace_id=resp.trace_id,
            )

        label_data = resp.get("data", [])
        if not label_data:
            raise ApiBaseError(
                f"未找到标注交付结果 (data_id={data_id}, table={table})"
            )

        storage_prefix = label_data[0].get("storage_prefix")
        if not storage_prefix:
            raise ApiBaseError(
                f"标注交付结果中storage_prefix为空 (data_id={data_id}, table={table})"
            )

        # 从 storage_prefix 解析 bucket_name 和 remote_prefix
        bucket_name, remote_prefix = (
            self._oss_tool.get_bucket_name_and_remote_prefix(storage_prefix)
        )

        # 拼接目标路径："/" 或空串视为根目录
        normalized = path.strip("/")
        if normalized:
            target_prefix = remote_prefix.rstrip("/") + "/" + normalized
        else:
            target_prefix = remote_prefix

        return self._oss_tool.list_dir_recursive(
            table, bucket_name, target_prefix, depth
        )

    def download_auto_label_result(
        self,
        data_id: str,
        target_path: str,
        project_name: str,
        label_type: Optional[str] = None,
        version: Optional[str] = None,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """
        Download auto label result files for a given data_id.

        Args:
            data_id: The clip data ID.
            project_name: project name.
            target_path: Local directory to save the downloaded files.
            label_type: Optional label type filter.
            version: Optional version filter.
            table: Table name. If not specified, uses the default table.
            max_workers: Number of concurrent download workers.

        Returns:
            Dictionary with download results:
            - success (int): Number of successfully downloaded files.
            - failed (List[str]): List of failed file paths.
        """
        table = table or self.table
        self._tracker.track(data_id, table, "download_label_result")

        payload = {
            "table": table,
            "data_id": data_id,
            "project_name": project_name,
        }
        if label_type:
            payload["label_type"] = label_type
        if version:
            payload["version"] = version

        return self._download_label_results(
            table=table,
            endpoint="/label/get-auto-label-result",
            payload=payload,
            target_path=target_path,
            max_workers=max_workers,
            path_key="storage_prefix",
            error_prefix="自动标注结果查询失败",
            save_subdir_fn=lambda item: item.get("label_type"),
        )

    def download_merge_label_result(
        self,
        data_id: str,
        target_path: str,
        project_name: str,
        label_type: str,
        version: Optional[str] = None,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """
        Download merge label result files for a given data_id.

        Args:
            data_id: The clip data ID.
            project_name: Project name.
            target_path: Local directory to save the downloaded files.
            label_type: Label type.
            version: Optional version filter.
            table: Table name. If not specified, uses the default table.
            max_workers: Number of concurrent download workers.

        Returns:
            Dictionary with download results:
            - success (int): Number of successfully downloaded files.
            - failed (List[str]): List of failed file paths.
        """
        table = table or self.table
        self._tracker.track(data_id, table, "download_merge_label_result")

        payload = {
            "table": table,
            "data_id": data_id,
            "project_name": project_name,
            "label_type": label_type,
        }
        if version:
            payload["version"] = version

        return self._download_label_results(
            table=table,
            endpoint="/label/get-merge-label-result",
            payload=payload,
            target_path=target_path,
            max_workers=max_workers,
            path_key="storage_prefix",
            error_prefix="合并标注结果查询失败",
            save_subdir_fn=lambda item: item.get("label_type"),
        )

    def _check_label_delivered(
        self,
        data_id: str,
        project_name: str,
        label_type: str,
        version: str,
        table: str,
    ) -> None:
        """检查标注结果是否已交付，已交付则抛出异常。"""
        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/label-delivery/get-label-delivery-result",
            json={
                "table": table,
                "data_id": data_id,
                "project_name": project_name,
                "label_type": label_type,
                "version": version,
            },
        )

        if resp.get("code") == 200 and resp.get("data"):
            raise ApiBaseError(
                f"标注结果已交付，无法重复上传 (data_id={data_id}, label_type={label_type}, version={version})"
            )

    def get_auto_label_result(
        self,
        data_id: str,
        project_name: str,
        label_type: Optional[str] = None,
        version: Optional[str] = None,
        table: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取自动标注结果信息。

        调用 /label/get-auto-label-result 接口查询自动标注的结果信息。

        Args:
            data_id: 数据 ID。
            project_name: 项目名称。
            label_type: 标注类型，可选。
            version: 版本号，可选。
            table: 表名。如果未指定，使用默认表名。

        Returns:
            自动标注结果列表，每个元素包含标注信息。

        Raises:
            ApiBaseError: 当 API 调用失败时。
        """
        table = table or self.table
        payload = {
            "table": table,
            "data_id": data_id,
            "project_name": project_name,
        }
        if label_type:
            payload["label_type"] = label_type
        if version:
            payload["version"] = version

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/label/get-auto-label-result",
            json=payload,
        )

        if resp.get("code") != 200:
            raise ApiBaseError(
                f"Failed to get auto label result: {resp.get('message', '')}",
                trace_id=resp.trace_id,
            )

        return resp.get("data", [])

    def get_manual_label_result(
        self,
        data_id: str,
        project_name: str,
        label_type: str,
        version: Optional[str] = None,
        table: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取人工标注结果信息。

        调用 /label/get-manual-label-result 接口查询人工标注的结果信息。

        Args:
            data_id: 数据 ID。
            project_name: 项目名称。
            label_type: 标注类型。
            version: 版本号，可选。
            table: 表名。如果未指定，使用默认表名。

        Returns:
            人工标注结果列表，每个元素包含标注信息。

        Raises:
            ApiBaseError: 当 API 调用失败时。
        """
        table = table or self.table
        payload = {
            "table": table,
            "data_id": data_id,
            "project_name": project_name,
            "label_type": label_type,
        }
        if version:
            payload["version"] = version

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/label/get-manual-label-result",
            json=payload,
        )

        if resp.get("code") != 200:
            raise ApiBaseError(
                f"Failed to get manual label result: {resp.get('message', '')}",
                trace_id=resp.trace_id,
            )

        return resp.get("data", [])

    def get_merge_label_result(
        self,
        data_id: str,
        project_name: str,
        label_type: str,
        version: Optional[str] = None,
        table: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取融合标注结果信息。

        调用 /label/get-merge-label-result 接口查询融合标注的结果信息。

        Args:
            data_id: 数据 ID。
            project_name: 项目名称。
            label_type: 标注类型。
            version: 版本号，可选。
            table: 表名。如果未指定，使用默认表名。

        Returns:
            融合标注结果列表，每个元素包含标注信息。

        Raises:
            ApiBaseError: 当 API 调用失败时。
        """
        table = table or self.table
        payload = {
            "table": table,
            "data_id": data_id,
            "project_name": project_name,
            "label_type": label_type,
        }
        if version:
            payload["version"] = version

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/label/get-merge-label-result",
            json=payload,
        )

        if resp.get("code") != 200:
            raise ApiBaseError(
                f"Failed to get merge label result: {resp.get('message', '')}",
                trace_id=resp.trace_id,
            )

        return resp.get("data", [])

    def upload_auto_label_result(
        self,
        data_id: str,
        project_name: str,
        label_type: str,
        version: str,
        local_dir: str,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> RespBody:
        """
        Upload local label result files to OSS and register the result.

        Args:
            data_id: The data ID.
            project_name: Project name.
            label_type: Type of label.
            version: Label version.
            local_dir: Local directory containing label result files.
            table: Table name. If not specified, uses the default table.
            max_workers: Number of concurrent upload workers.

        Returns:
            RespBody with upload results.
        """
        if not os.path.isdir(local_dir):
            raise ApiBaseError(f"local_dir目录不存在: {local_dir}")

        table = table or self.table

        # 检查是否已交付
        self._check_label_delivered(
            data_id, project_name, label_type, version, table
        )

        self._tracker.track(data_id, table, "upload_label_result")

        bucket_name, data_type = self._get_label_bucket_and_data_type(table)
        remote_prefix = (
            f"auto_label/{table}/{data_type}/{data_id}/{label_type}/{version}/"
        )

        failed_resp = self._upload_label_to_oss(
            table,
            bucket_name,
            remote_prefix,
            local_dir,
            max_workers,
            "Failed to upload label result",
        )
        if failed_resp:
            return failed_resp

        update_resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/label/put-auto-label-result",
            json={
                "table": table,
                "data_id": data_id,
                "storage_prefix": f"oss://{bucket_name}/{remote_prefix}",
                "label_type": label_type,
                "version": version,
                "project_name": project_name,
            },
        )
        return update_resp.to_resp_body(data={"failed_files": []})

    def upload_merge_label_result(
        self,
        data_id: str,
        project_name: str,
        label_provider: str,
        label_type: str,
        version: str,
        manual_label: List[MergeLabelItem],
        auto_label: List[MergeLabelItem],
        local_dir: str,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> RespBody:
        """
        Upload local merge label result files to OSS and register the result.

        Args:
            data_id: The clip ID.
            project_name: Project name.
            label_provider: Label provider name.
            label_type: Label type.
            version: Merge label version.
            manual_label: List of manual label records.
            auto_label: List of auto label records.
            local_dir: Local directory containing merge label result files.
            table: Table name. If not specified, uses the default table.
            max_workers: Number of concurrent upload workers.

        Returns:
            RespBody with upload results.
        """
        if not os.path.isdir(local_dir):
            raise ApiBaseError(f"local_dir目录不存在: {local_dir}")

        table = table or self.table

        # 检查是否已交付
        self._check_label_delivered(
            data_id, project_name, label_type, version, table
        )

        self._tracker.track(data_id, table, "upload_merge_label_result")

        bucket_name, data_type = self._get_label_bucket_and_data_type(table)
        remote_prefix = f"merge_label/{table}/{data_type}/{data_id}/{label_type}/{version}/"

        failed_resp = self._upload_label_to_oss(
            table,
            bucket_name,
            remote_prefix,
            local_dir,
            max_workers,
            "Failed to upload merge label result",
        )
        if failed_resp:
            return failed_resp

        update_resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/label/put-merge-label-result",
            json={
                "table": table,
                "data_id": data_id,
                "label_provider": label_provider,
                "label_type": label_type,
                "project_name": project_name,
                "version": version,
                "manual_label": [item.to_dict() for item in manual_label],
                "auto_label": [item.to_dict() for item in auto_label],
                "storage_prefix": f"oss://{bucket_name}/{remote_prefix}",
            },
        )
        return update_resp.to_resp_body(data={"failed_files": []})

    def upload_occ_ground_truth(
        self,
        data_id: str,
        project_name: str,
        local_dir: str,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> RespBody:
        """
        Upload OCC ground truth files and register the result.

        Args:
            data_id: The data ID (clip_id).
            project_name: Project name.
            local_dir: Local directory containing OCC ground truth files.
            table: Table name. If not specified, uses the default table.
            max_workers: Number of concurrent upload workers.

        Returns:
            RespBody with upload results.
        """
        if not os.path.isdir(local_dir):
            raise ApiBaseError(f"local_dir目录不存在: {local_dir}")

        table = table or self.table
        self._tracker.track(data_id, table, "upload_occ_ground_truth")

        bucket_name, data_type = self._get_label_bucket_and_data_type(table)
        occ_record_id = str(
            uuid.uuid5(self._OCC_NAMESPACE, f"{data_id}:{table}")
        )
        remote_prefix = f"occ_result/{table}/{data_type}/{data_id}/{occ_record_id}/"

        failed_resp = self._upload_label_to_oss(
            table,
            bucket_name,
            remote_prefix,
            local_dir,
            max_workers,
            "Failed to upload OCC ground truth",
        )
        if failed_resp:
            return failed_resp

        resp = self._webapp_client.do_request(
            WebappClient.POST,
            "/occ/put-occ-result",
            json={
                "table": table,
                "data_id": data_id,
                "storage_prefix": f"oss://{bucket_name}/{remote_prefix}",
                "project_name": project_name,
            },
        )
        return resp.to_resp_body()

    def download_occ_ground_truth(
        self,
        data_id: str,
        target_path: str,
        project_name: str,
        relative_path: Optional[str] = None,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """
        Download OCC ground truth files for a given data_id.

        Args:
            data_id: The clip data ID.
            target_path: Local directory to save the downloaded files.
            project_name: Project name.
            table: Table name. If not specified, uses the default table.
            max_workers: Number of concurrent download workers.
            relative_path: 相对于 storage_prefix 的相对路径，支持子目录或单个文件。

        Returns:
            Dictionary with download results:
            - success (int): Number of successfully downloaded files.
            - failed (List[str]): List of failed file paths.
        """
        table = table or self.table
        self._tracker.track(data_id, table, "download_occ_ground_truth")

        return self._download_label_results(
            table=table,
            endpoint="/occ/get-occ-result",
            payload={
                "table": table,
                "data_id": data_id,
                "project_name": project_name,
            },
            target_path=target_path,
            max_workers=max_workers,
            path_key="storage_prefix",
            error_prefix="OCC结果查询失败",
            raise_if_empty=False,
            relative_path=relative_path,
        )

    def download_manual_label_result(
        self,
        data_id: str,
        target_path: str,
        project_name: str,
        label_type: str,
        version: Optional[str] = None,
        table: Optional[str] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """
        Download manual label result files for a given data_id.

        Args:
            data_id: The clip data ID.
            project_name: Project name.
            target_path: Local directory to save the downloaded files.
            label_type: Label type.
            version: Optional version filter.
            table: Table name. If not specified, uses the default table.
            max_workers: Number of concurrent download workers.

        Returns:
            Dictionary with download results:
            - success (int): Number of successfully downloaded files.
            - failed (List[str]): List of failed file paths.
        """
        table = table or self.table
        self._tracker.track(data_id, table, "download_manual_label_result")

        payload = {
            "table": table,
            "data_id": data_id,
            "project_name": project_name,
            "label_type": label_type,
            "version": version,
        }

        return self._download_label_results(
            table=table,
            endpoint="/label/get-manual-label-result",
            payload=payload,
            target_path=target_path,
            max_workers=max_workers,
            path_key="storage_prefix",
            error_prefix="人工标注结果查询失败",
            save_subdir_fn=lambda item: item.get("label_type"),
        )
