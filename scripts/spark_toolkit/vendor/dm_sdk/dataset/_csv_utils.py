import os
import uuid
from typing import List


def count_csv_members(filepath: str, delimiter: str = "|^0-^|") -> int:
    """统计 CSV 文件中的 member 行数（排除表头）。

    Args:
        filepath: CSV 文件路径。
        delimiter: 列分隔符，默认为 ``|^0-^|``。

    Returns:
        数据行数（排除表头）。空文件或只有表头返回 0。
    """
    count = 0
    with open(filepath, encoding="utf-8") as f:
        for i, _ in enumerate(f):
            if i == 0:
                continue  # 跳过表头
            count += 1
    return count


def list_csv_files(csv_dir: str) -> List[str]:
    """扫描目录下所有 `.csv` 文件（递归），按文件名排序。

    Args:
        csv_dir: 本地目录路径。

    Returns:
        所有 csv 文件的绝对路径列表。
    """
    csv_files = []
    for root, _, files in os.walk(csv_dir):
        for name in sorted(files):
            if name.lower().endswith(".csv"):
                csv_files.append(os.path.join(root, name))
    return csv_files


def generate_upload_filename(original_name: str) -> str:
    """在原始文件名基础上追加 uuid，避免 OSS 覆盖。

    Args:
        original_name: 原始文件名，如 ``part-00001.csv``。

    Returns:
        带 uuid 后缀的文件名，如 ``part-00001_a1b2c3d4.csv``。
    """
    name, ext = os.path.splitext(original_name)
    suffix = uuid.uuid4().hex[:8]
    return f"{name}_{suffix}{ext}"


def upload_csv_files_to_oss(
    oss_tool,
    bucket_name: str,
    member_location_prefix: str,
    csv_files: List[str],
) -> int:
    """将 CSV 文件上传到 OSS 指定前缀下。

    Args:
        oss_tool: OSSToolManager 实例。
        bucket_name: OSS bucket 名称。
        member_location_prefix: 后端返回的存储前缀，如 ``oss://bucket/prefix/``。
        csv_files: 本地 CSV 文件路径列表。

    Returns:
        所有 CSV 文件的 member 总行数。

    Raises:
        ApiBaseError: 上传失败时抛出。
    """
    from dm_sdk.tools.api import ApiBaseError

    total = 0
    for filepath in csv_files:
        total += count_csv_members(filepath)

    _, remote_prefix = oss_tool.get_bucket_name_and_remote_prefix(
        member_location_prefix
    )
    if remote_prefix and not remote_prefix.endswith("/"):
        remote_prefix += "/"

    oss_client = oss_tool._get_or_create_client("")
    for filepath in csv_files:
        filename = os.path.basename(filepath)
        upload_name = generate_upload_filename(filename)
        oss_key = remote_prefix + upload_name
        success, err = oss_client.upload_file(
            bucket_name, oss_key, filepath
        )
        if not success:
            raise ApiBaseError(
                f"上传 CSV 文件失败: {err} "
                f"(file={filepath}, oss_key={oss_key})"
            )

    return total
