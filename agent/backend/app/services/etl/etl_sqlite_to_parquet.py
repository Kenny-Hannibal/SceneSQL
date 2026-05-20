#!/usr/bin/env python3
"""
SQLite → Parquet ETL

功能：
1. 扫描一批 SQLite DB 目录（约 1.5 万个 .db 文件）
2. 提取核心表（range_tag, ego, dynamic_obj, intersection_info 等）
3. 添加 bag_id 列（基于文件名）
4. 写入 Parquet（按表分区，可选按 bag_id 子分区）
5. 生成映射表记录（YAML）

用法：
    cd /root/data/text2sql
    source .venv/bin/activate
    python agent/backend/app/services/etl/etl_sqlite_to_parquet.py \
        --source-dir /mnt/gacrnd-oss/.../sqlite_dbs/20260515_T68_1131_5bb5ec_1.5w \
        --output-dir /mnt/gacrnd-oss/.../parquet/20260515_T68_1131_5bb5ec_1.5w \
        --batch-id 20260515_T68_1131_5bb5ec_1.5w \
        --repo-hash 1df5784f
"""

import os
import sys
import argparse
import subprocess
import yaml
from pathlib import Path
from datetime import datetime, timezone

import duckdb

# 需要提取到 Parquet 的表（与 schema_structure.yaml 一致）
CORE_TABLES = [
    "range_tag",
    "intersection_info",
    "ego",
    "dynamic_obj",
    "static_obj",
    "static_lane",
    "static_link",
    "dynamic_lane",
    "dynamic_link",
]

BATCH_SIZE = 500  # 每批处理 500 个 DB，避免内存爆炸
OSS_MOUNT_PREFIX = "/mnt/gacrnd-oss"
OSS_BUCKET_PREFIX = "oss://gacrnd-oss"


def find_db_files(source_dir: str) -> list[str]:
    """扫描 .db 文件：优先 ossutil64（OSS 原生 API），其次 ls，最后 find。

    注意：对 OSS 路径避免使用 subprocess.PIPE（capture_output=True），
    因为 nohup 后台运行时，子进程写满 PIPE 缓冲区（仅 8KB）会导致死锁。
    改为 stdout 重定向到临时文件。
    """
    source_dir = source_dir.rstrip("/")

    # 1. 尝试 ossutil64（最快，绕过 FUSE）
    if source_dir.startswith(OSS_MOUNT_PREFIX) and os.path.exists("/usr/bin/ossutil64"):
        oss_path = source_dir.replace(OSS_MOUNT_PREFIX, OSS_BUCKET_PREFIX, 1)
        tmp_path = f"/tmp/etl_ossutil_{os.getpid()}.txt"
        try:
            with open(tmp_path, "w") as tmpf:
                result = subprocess.run(
                    ["ossutil64", "ls", oss_path],
                    stdout=tmpf,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                )
            if result.returncode == 0 and os.path.exists(tmp_path):
                with open(tmp_path, "r") as f:
                    files = []
                    for line in f:
                        line = line.strip()
                        if not line.endswith(".db"):
                            continue
                        parts = line.split()
                        if not parts:
                            continue
                        oss_obj = parts[-1]
                        if not oss_obj.startswith("oss://"):
                            continue
                        local_path = oss_obj.replace(OSS_BUCKET_PREFIX, OSS_MOUNT_PREFIX, 1)
                        files.append(local_path)
                os.unlink(tmp_path)
                if files:
                    print(f"[INFO] ossutil64 发现 {len(files)} 个 SQLite DB 文件")
                    return sorted(files)
        except Exception as e:
            print(f"[WARN] ossutil64 失败: {e}，回退到 ls")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # 2. 尝试 ls（FUSE 挂载，单行返回较快）
    try:
        result = subprocess.run(
            ["ls", source_dir],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            files = [
                os.path.join(source_dir, line.strip())
                for line in result.stdout.splitlines()
                if line.strip().endswith(".db")
            ]
            if files:
                print(f"[INFO] ls 发现 {len(files)} 个 SQLite DB 文件")
                return sorted(files)
    except Exception as e:
        print(f"[WARN] ls 失败: {e}，回退到 find")

    # 3. 回退 find（最慢但最可靠）
    cmd = ["find", source_dir, "-name", "*.db", "-type", "f"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    print(f"[INFO] find 发现 {len(files)} 个 SQLite DB 文件")
    return files


def _get_db_columns(conn: duckdb.DuckDBPyConnection, db_path: str, table_name: str) -> list[str] | None:
    """获取某 DB 中某表的列名，表不存在则返回 None。"""
    safe_path = db_path.replace("'", "''")
    try:
        result = conn.execute(
            f"SELECT * FROM sqlite_scan('{safe_path}', '{table_name}') LIMIT 0"
        )
        return [desc[0] for desc in result.description]
    except Exception:
        return None


def etl_table(
    conn: duckdb.DuckDBPyConnection,
    db_files: list[str],
    table_name: str,
    output_dir: Path,
    batch_size: int = BATCH_SIZE,
):
    """将某张表从所有 SQLite DB 中提取并写入 Parquet。

    修复点：不同 DB 的同一张表可能出现列数/列名不一致（例如 dynamic_obj
    某些 DB 只有 ts+obj_id 两列）。本函数会在每个 batch 内探测列名并集，
    对缺失列填充 NULL，保证 UNION ALL 成功。
    """
    parquet_path = output_dir / f"{table_name}.parquet"
    total = len(db_files)
    processed = 0
    has_written = False  # 标记是否已写出第一批数据

    for i in range(0, total, batch_size):
        batch = db_files[i : i + batch_size]

        # ---- 探测该 batch 内所有 DB 的列名并集 ----
        all_cols = set()
        db_cols_map: dict[str, list[str]] = {}
        for f in batch:
            cols = _get_db_columns(conn, f, table_name)
            if cols is not None:
                all_cols.update(cols)
                db_cols_map[f] = cols

        if not all_cols:
            # 该 batch 所有 DB 都没有此表，直接跳过
            processed += len(batch)
            print(
                f"[INFO] {table_name}: {processed}/{total} ({processed/total*100:.1f}%)  (skip: no table)"
            )
            continue

        all_cols = sorted(all_cols)

        # ---- 构造显式列名的 UNION ALL 查询 ----
        parts = []
        for f in batch:
            if f not in db_cols_map:
                continue  # 该 DB 无此表，跳过
            bag_id = Path(f).stem
            safe_path = f.replace("'", "''")
            db_cols_set = set(db_cols_map[f])
            col_exprs = []
            for col in all_cols:
                if col in db_cols_set:
                    col_exprs.append(f'"{col}"')
                else:
                    col_exprs.append(f'NULL AS "{col}"')
            parts.append(
                f"SELECT '{bag_id}' AS bag_id, {', '.join(col_exprs)} FROM sqlite_scan('{safe_path}', '{table_name}')"
            )

        if not parts:
            processed += len(batch)
            print(
                f"[INFO] {table_name}: {processed}/{total} ({processed/total*100:.1f}%)  (skip: no valid DB)"
            )
            continue

        union_sql = " UNION ALL ".join(parts)

        # ---- 写入 Parquet ----
        try:
            if not has_written:
                # 第一批：直接写入（覆盖旧文件）
                conn.execute(
                    f"""
                    COPY ({union_sql})
                    TO '{str(parquet_path)}'
                    (FORMAT PARQUET, ROW_GROUP_SIZE 100000, COMPRESSION 'ZSTD')
                    """
                )
                has_written = True
            else:
                # 后续批：先写到临时文件，再合并
                tmp_parquet = output_dir / f"{table_name}_tmp.parquet"
                try:
                    conn.execute(
                        f"""
                        COPY ({union_sql})
                        TO '{str(tmp_parquet)}'
                        (FORMAT PARQUET, ROW_GROUP_SIZE 100000, COMPRESSION 'ZSTD')
                        """
                    )
                    # 合并
                    conn.execute(
                        f"""
                        COPY (
                            SELECT * FROM read_parquet('{str(parquet_path)}')
                            UNION ALL
                            SELECT * FROM read_parquet('{str(tmp_parquet)}')
                        )
                        TO '{str(parquet_path)}'
                        (FORMAT PARQUET, ROW_GROUP_SIZE 100000, COMPRESSION 'ZSTD')
                        """
                    )
                finally:
                    if tmp_parquet.exists():
                        tmp_parquet.unlink()

            processed += len(batch)
            print(
                f"[INFO] {table_name}: {processed}/{total} ({processed/total*100:.1f}%)"
            )
        except Exception as e:
            print(f"[ERROR] {table_name} batch {i//batch_size + 1} 失败: {e}")
            # 继续处理下一批，不中断整个 ETL

    if has_written:
        print(f"[OK] {table_name} → {parquet_path}")
        return str(parquet_path)
    else:
        print(f"[WARN] {table_name}: 所有 DB 均无此表，跳过")
        return None


def generate_manifest(
    batch_id: str,
    source_dir: str,
    output_dir: Path,
    db_count: int,
    repo_hash: str,
    schema_version: str,
    table_parquet_map: dict[str, str | None],
) -> dict:
    """生成 ETL 映射表（manifest）。"""
    manifest = {
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "source_dir": source_dir,
        "output_dir": str(output_dir),
        "bag_count": db_count,
        "data_mining_repo_hash": repo_hash,
        "schema_version": schema_version,
        "tables": {
            name: {
                "parquet_path": path,
                "source_table": name,
            }
            for name, path in table_parquet_map.items()
            if path is not None
        },
    }
    return manifest


def save_manifest(manifest: dict, output_dir: Path):
    """保存 manifest 到 YAML 和 SQLite 两种格式。"""
    # YAML（人类可读）
    yaml_path = output_dir / "manifest.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(manifest, f, allow_unicode=True, sort_keys=False, width=200)
    print(f"[OK] Manifest YAML: {yaml_path}")

    # SQLite（程序快速查询）
    db_path = output_dir / "manifest.db"
    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etl_manifest (
            batch_id TEXT,
            created_at TEXT,
            source_dir TEXT,
            output_dir TEXT,
            bag_count INTEGER,
            repo_hash TEXT,
            schema_version TEXT,
            table_name TEXT,
            parquet_path TEXT,
            PRIMARY KEY (batch_id, table_name)
        )
    """)
    for table_name, info in manifest["tables"].items():
        conn.execute("""
            INSERT OR REPLACE INTO etl_manifest
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            manifest["batch_id"],
            manifest["created_at"],
            manifest["source_dir"],
            manifest["output_dir"],
            manifest["bag_count"],
            manifest["data_mining_repo_hash"],
            manifest["schema_version"],
            table_name,
            info["parquet_path"],
        ))
    conn.close()
    print(f"[OK] Manifest DB: {db_path}")


def main():
    parser = argparse.ArgumentParser(description="SQLite → Parquet ETL")
    parser.add_argument("--source-dir", required=True, help="SQLite DB 目录")
    parser.add_argument("--output-dir", required=True, help="Parquet 输出目录")
    parser.add_argument("--batch-id", required=True, help="批次 ID")
    parser.add_argument("--repo-hash", default="", help="数据挖掘仓库 commit hash")
    parser.add_argument("--schema-version", default="1.2.0", help="Schema 版本")
    parser.add_argument(
        "--tables",
        nargs="+",
        default=CORE_TABLES,
        help=f"需要 ETL 的表，默认: {CORE_TABLES}",
    )
    parser.add_argument("--max-db", type=int, default=0, help="最大处理 DB 数量（0=无限制，用于试点）")
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 批次 ID: {args.batch_id}")
    print(f"[INFO] 源目录: {source_dir}")
    print(f"[INFO] 输出目录: {output_dir}")

    # 1. 扫描 DB 文件
    db_files = find_db_files(str(source_dir))
    if args.max_db > 0 and len(db_files) > args.max_db:
        db_files = db_files[:args.max_db]
        print(f"[INFO] 试点模式：仅处理前 {args.max_db} 个 DB")
    if not db_files:
        print("[ERROR] 未找到任何 .db 文件")
        sys.exit(1)

    # 2. ETL 每张表
    conn = duckdb.connect()
    conn.execute("INSTALL sqlite; LOAD sqlite;")

    table_parquet_map: dict[str, str | None] = {}
    for table_name in args.tables:
        print(f"\n[INFO] 开始处理表: {table_name}")
        path = etl_table(conn, db_files, table_name, output_dir)
        table_parquet_map[table_name] = path

    conn.close()

    # 3. 生成 Manifest
    manifest = generate_manifest(
        batch_id=args.batch_id,
        source_dir=str(source_dir),
        output_dir=output_dir,
        db_count=len(db_files),
        repo_hash=args.repo_hash,
        schema_version=args.schema_version,
        table_parquet_map=table_parquet_map,
    )
    save_manifest(manifest, output_dir)

    print(f"\n[OK] ETL 完成: {args.batch_id}")
    print(f"[OK] 输出目录: {output_dir}")


if __name__ == "__main__":
    main()
