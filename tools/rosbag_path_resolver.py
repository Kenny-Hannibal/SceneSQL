#!/usr/bin/env python3
"""
Rosbag 路径解析器 — 封装 dm_sdk 查询逻辑

一站式流程：
  回灌 bag_id (db 文件名)
    → 查询 ubm_vehicle_module_bin 得到 origins (原始 table + 原始 bag_id)
    → 查询原始 table 得到 storage_prefix (OSS 路径)
    → 根据 OSS_MOUNT_MAP 转换为本地挂载路径

依赖：dm_sdk
"""

import os
from typing import Optional, Dict
from dataclasses import dataclass

# Load .env so that os.getenv can read variables defined there
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class BagPathInfo:
    data_id: str                       # 回灌后的 bag_id（db 文件名）
    prod_table: Optional[str] = None   # 产线表名
    origin_table: Optional[str] = None # 原始表名（动态查询得到）
    origin_bag_id: Optional[str] = None # 原始 bag_id
    oss_path: Optional[str] = None     # 原始 rosbag OSS 路径 (storage_prefix)
    local_path: Optional[str] = None   # 本地挂载路径
    bag_name: Optional[str] = None     # bag 名称
    vin: Optional[str] = None          # 车辆 VIN
    vehicle_model: Optional[str] = None # 车型
    em_bin_oss_path: Optional[str] = None   # em bin 自身的 OSS 路径 (storage_prefix)
    em_bin_local_path: Optional[str] = None # em bin 本地挂载路径


def _parse_oss_mount_map(mount_map_str: Optional[str]) -> Dict[str, str]:
    result = {}
    if not mount_map_str:
        return result
    for pair in mount_map_str.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        oss_prefix, local_path = pair.split(":", 1)
        result[oss_prefix.strip()] = local_path.strip()
    return result


def _oss_to_local(oss_path: str, mount_map: Dict[str, str]) -> Optional[str]:
    if not oss_path:
        return None
    # 去掉尾部斜杠，方便匹配
    oss_path = oss_path.rstrip("/")
    # 去掉 oss:// 前缀
    if oss_path.startswith("oss://"):
        oss_path = oss_path[6:]
    # 按前缀长度降序匹配，避免短前缀误匹配
    for oss_prefix, local_prefix in sorted(mount_map.items(), key=lambda x: -len(x[0])):
        if oss_path.startswith(oss_prefix):
            relative = oss_path[len(oss_prefix):]
            if relative.startswith("/"):
                relative = relative[1:]
            local = os.path.join(local_prefix, relative)
            return os.path.normpath(local)
    # 未匹配到，当作本地路径返回
    return os.path.normpath(oss_path)


def resolve_db_path(db_path: str, mount_map_str: Optional[str] = None) -> str:
    """将 db 路径（可能是 OSS 路径）转换为本地绝对路径。"""
    mount_map = _parse_oss_mount_map(mount_map_str or os.getenv("OSS_MOUNT_MAP"))
    if db_path.startswith("oss://"):
        local = _oss_to_local(db_path, mount_map)
        if local is None:
            raise ValueError(f"Cannot convert OSS path: {db_path}")
        return local
    return os.path.abspath(db_path)


class RosbagPathResolver:
    """Rosbag 路径解析器 — 一站式从回灌 bag_id 解析到本地路径。"""

    def __init__(
        self,
        access_token: Optional[str] = None,
        prod_table: str = "ubm_vehicle_module_bin",
        oss_mount_map: Optional[str] = None,
    ):
        self.access_token = access_token or os.getenv(
            "DM_ACCESS_TOKEN",
            "b8d98f0a7be24ec5ae9b1b48cc350ab4iSVDjwd6DbcMuUwBhwMa2TDyUM5GaWunmQoN-ZUJJ3E="
        )
        self.prod_table = prod_table or os.getenv("DM_PROD_TABLE", "ubm_vehicle_module_bin")
        self.mount_map = _parse_oss_mount_map(oss_mount_map or os.getenv("OSS_MOUNT_MAP"))
        try:
            from dm_sdk import ProdDataClient, RawDataClient
            self._ProdDataClient = ProdDataClient
            self._RawDataClient = RawDataClient
        except ImportError as exc:
            raise ImportError("dm_sdk not installed") from exc

    def resolve(self, data_id: str) -> BagPathInfo:
        """
        一站式解析：回灌 bag_id → 原始 bag_id + 原始 table → OSS 路径 → 本地路径
        """
        # Step 1: 查询产线表，获取 origins（原始 table + 原始 bag_id）
        prod_client = self._ProdDataClient(
            access_token=self.access_token,
            table=self.prod_table,
        )
        resp = prod_client.get_bag_metadata(data_id=data_id)
        if resp.resp_code() != 200:
            raise RuntimeError(f"ProdData query failed: {resp.msg}")

        prod_data = resp.resp_data()
        if not prod_data:
            raise ValueError(f"Bag {data_id} not found in {self.prod_table}")

        origins = prod_data.get("origins", [])
        if not origins:
            raise ValueError(f"Bag {data_id} has no origins info")

        origin = origins[0]
        origin_table = origin.get("table")
        origin_bag_id = origin.get("bag_id")

        if not origin_table or not origin_bag_id:
            raise ValueError(f"Origins info incomplete for {data_id}")

        # Step 2: 查询原始表，获取 storage_prefix（OSS 路径）
        raw_client = self._RawDataClient(
            access_token=self.access_token,
            table=origin_table,
        )
        raw_resp = raw_client.get_bag_metadata(bag_id=origin_bag_id)
        if raw_resp.resp_code() != 200:
            raise RuntimeError(f"RawData query failed: {raw_resp.msg}")

        raw_data = raw_resp.resp_data() or {}
        oss_path = raw_data.get("storage_prefix") or raw_data.get("raw_storage_prefix")

        # Step 3: OSS 路径 → 本地路径
        local_path = _oss_to_local(oss_path, self.mount_map) if oss_path else None

        return BagPathInfo(
            data_id=data_id,
            prod_table=self.prod_table,
            origin_table=origin_table,
            origin_bag_id=origin_bag_id,
            oss_path=oss_path,
            local_path=local_path,
            bag_name=raw_data.get("bag_name"),
            vin=raw_data.get("vin"),
            vehicle_model=raw_data.get("vehicle_model"),
        )

    def resolve_oss_path(self, data_id: str) -> Optional[str]:
        """只返回 OSS 路径（不转换本地路径）。"""
        info = self.resolve(data_id)
        return info.oss_path

    def resolve_local_path(self, data_id: str) -> Optional[str]:
        """只返回本地挂载路径。"""
        info = self.resolve(data_id)
        return info.local_path

    def resolve_em_bin_path(self, data_id: str) -> BagPathInfo:
        """
        解析 em bin 的本地路径 — 直接查询产线表中 em bin 自身的 storage_prefix，
        不走 origins（origins 指向原始 rosbag，那里没有 fusion_map_plus.bin）。

        用途：3D BEV 视图需要从 em bin 目录读取 fusion_map_plus.bin 等文件。
        """
        prod_client = self._ProdDataClient(
            access_token=self.access_token,
            table=self.prod_table,
        )
        resp = prod_client.get_bag_metadata(data_id=data_id)
        if resp.resp_code() != 200:
            raise RuntimeError(f"ProdData query failed for em bin: {resp.msg}")

        prod_data = resp.resp_data()
        if not prod_data:
            raise ValueError(f"Bag {data_id} not found in {self.prod_table}")

        # em bin 自身的 storage_prefix
        em_bin_oss_path = prod_data.get("storage_prefix")

        # 同时也获取 origins 信息（用于填充 rosbag 相关字段）
        origins = prod_data.get("origins", [])
        origin = origins[0] if origins else {}
        origin_table = origin.get("table")
        origin_bag_id = origin.get("bag_id")

        # OSS → 本地路径转换
        em_bin_local_path = _oss_to_local(em_bin_oss_path, self.mount_map) if em_bin_oss_path else None

        # 也解析原始 rosbag 路径（复用 resolve 逻辑）
        try:
            base_info = self.resolve(data_id)
        except Exception:
            base_info = BagPathInfo(data_id=data_id)

        return BagPathInfo(
            data_id=data_id,
            prod_table=self.prod_table,
            origin_table=origin_table,
            origin_bag_id=origin_bag_id,
            oss_path=base_info.oss_path,
            local_path=base_info.local_path,
            bag_name=base_info.bag_name,
            vin=base_info.vin,
            vehicle_model=base_info.vehicle_model,
            em_bin_oss_path=em_bin_oss_path,
            em_bin_local_path=em_bin_local_path,
        )
