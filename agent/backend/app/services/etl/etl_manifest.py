#!/usr/bin/env python3
"""
ETL Manifest 管理器

管理 SQLite → Parquet 的映射关系，支持：
- 注册/查询 ETL 批次
- 根据 batch_id 获取 Parquet 路径
- 根据 bag_id 找到原 SQLite 路径

存储：
- manifest.yaml（人类可读）
- manifest.db（DuckDB/SQLite，程序查询）
"""

import os
import yaml
import duckdb
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass


@dataclass
class EtlBatch:
    batch_id: str
    created_at: str
    source_dir: str
    output_dir: str
    bag_count: int
    repo_hash: str
    schema_version: str
    tables: Dict[str, str]  # table_name -> parquet_path


class EtlManifestManager:
    """ETL 映射表管理器。"""

    def __init__(self, base_dir: Optional[str] = None):
        """
        base_dir: ETL 输出根目录。
        默认从环境变量 ETL_BASE_PATH 读取，否则用 /tmp/etl。
        """
        self.base_dir = Path(base_dir or os.environ.get("ETL_BASE_PATH", "/tmp/etl"))

    def get_manifest_db(self, batch_id: str) -> Optional[Path]:
        """获取某个批次的 manifest.db 路径。"""
        candidates = [
            self.base_dir / batch_id / "manifest.db",
            self.base_dir / batch_id / "manifest.yaml",
        ]
        for c in candidates:
            if c.exists():
                if c.suffix == ".db":
                    return c
                # 如果是 yaml，尝试找到同目录的 db
                db_path = c.with_suffix(".db")
                if db_path.exists():
                    return db_path
        return None

    def get_manifest_yaml(self, batch_id: str) -> Optional[Path]:
        """获取某个批次的 manifest.yaml 路径。"""
        p = self.base_dir / batch_id / "manifest.yaml"
        return p if p.exists() else None

    def load_batch(self, batch_id: str) -> Optional[EtlBatch]:
        """加载某个批次的信息。"""
        yaml_path = self.get_manifest_yaml(batch_id)
        if not yaml_path:
            return None

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return EtlBatch(
            batch_id=data["batch_id"],
            created_at=data["created_at"],
            source_dir=data["source_dir"],
            output_dir=data["output_dir"],
            bag_count=data["bag_count"],
            repo_hash=data["data_mining_repo_hash"],
            schema_version=data["schema_version"],
            tables={k: v["parquet_path"] for k, v in data["tables"].items()},
        )

    def list_batches(self) -> List[str]:
        """列出所有已注册的批次 ID。"""
        if not self.base_dir.exists():
            return []
        return sorted(
            [
                d.name
                for d in self.base_dir.iterdir()
                if d.is_dir() and (d / "manifest.yaml").exists()
            ]
        )

    def get_parquet_path(self, batch_id: str, table_name: str) -> Optional[str]:
        """获取某批次某表的 Parquet 路径。"""
        batch = self.load_batch(batch_id)
        if not batch:
            return None
        return batch.tables.get(table_name)

    def get_active_batch(self) -> Optional[str]:
        """从环境变量获取当前激活的批次。"""
        return os.environ.get("ETL_BATCH_ID")

    def get_connection(self, batch_id: Optional[str] = None) -> duckdb.DuckDBPyConnection:
        """
        获取 DuckDB 连接，已配置好当前批次的 Parquet 视图。

        使用方式：
            conn = manager.get_connection()
            conn.execute("SELECT * FROM range_tag WHERE bag_id = 'xxx'")
        """
        batch_id = batch_id or self.get_active_batch()
        if not batch_id:
            raise ValueError("未指定 batch_id，请设置 ETL_BATCH_ID 环境变量或传入参数")

        batch = self.load_batch(batch_id)
        if not batch:
            raise ValueError(f"批次 {batch_id} 不存在，请先执行 ETL")

        conn = duckdb.connect()

        # 为每张表创建视图
        for table_name, parquet_path in batch.tables.items():
            resolved_path = self._resolve_parquet_path(parquet_path, batch_id)
            conn.execute(f"""
                CREATE OR REPLACE VIEW {table_name} AS
                SELECT * FROM read_parquet('{resolved_path}')
            """)

        return conn

    def _resolve_parquet_path(self, parquet_path: str, batch_id: str) -> str:
        """修复 manifest.yaml 中记录的绝对路径，支持数据迁移到新的 base_dir。"""
        p = Path(parquet_path)
        try:
            if p.exists():
                return str(p)
        except OSError:
            pass  # FUSE 挂载断开等情况，继续尝试 fallback

        # 尝试用当前 base_dir 重新拼接路径
        fallback = self.base_dir / batch_id / p.name
        try:
            if fallback.exists():
                return str(fallback)
        except OSError:
            pass

        # 如果都找不到，返回 fallback 路径（优先使用当前 base_dir）
        return str(fallback)


# 全局单例（方便直接导入使用）
_default_manager: Optional[EtlManifestManager] = None


def get_manager() -> EtlManifestManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = EtlManifestManager()
    return _default_manager
