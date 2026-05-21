#!/usr/bin/env python3
"""
SQLite → Parquet ETL（v3：ossutil sync + 并行处理）

改造点（相比 v2）：
1. 优先使用 ossutil sync 将 OSS 上的 sqlite_dbs 文件夹整体拉到 /root/data/tmp
2. 本地处理完成后自动清理临时目录
3. 分批串行改为并发处理（concurrent.futures.ProcessPoolExecutor）
4. 每张表独立并行，表内按 batch 并行写临时 Parquet 后合并

用法：
    cd /root/data/text2sql
    source .venv/bin/activate
    python agent/backend/app/services/etl/etl_sqlite_to_parquet.py \
        --source-dir /mnt/gacrnd-oss/.../sqlite_dbs/20260515_T68_1131_5bb5ec_1.5w \
        --output-dir /mnt/gacrnd-oss/.../parquet/20260515_T68_1131_5bb5ec_1.5w \
        --batch-id 20260515_T68_1131_5bb5ec_1.5w \
        --repo-hash 1df5784f \
        --workers 4
"""

import os
import sys
import argparse
import shutil
import subprocess
import tempfile
import yaml
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed

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

BATCH_SIZE = 500  # 每批处理 500 个 DB
OSS_MOUNT_PREFIX = "/mnt/gacrnd-oss"
OSS_BUCKET_PREFIX = "oss://gacrnd-oss"
LOCAL_STAGING_ROOT = "/root/data/tmp"  # ossutil sync 本地暂存根目录

# 每个 DuckDB 子进程预估内存（MB），用于自适应 worker 计算
_DUCKDB_WORKER_MEM_MB = 512  # 保守估计：DuckDB + sqlite_scan 单连接约 300-500MB


def auto_workers(table_count: int = len(CORE_TABLES)) -> int:
    """根据系统资源自适应计算并行 worker 数。

    约束条件：
    1. 不超过 CPU 核数 - 1（留 1 核给主进程 + 合并）
    2. 不超过可用内存 / 单 worker 内存预估
    3. 表间并行数不超过表数量
    4. 最少 1 个 worker

    返回：建议的 worker 数
    """
    import multiprocessing

    cpu_count = multiprocessing.cpu_count()
    # 可用内存（MB）
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    avail_mb = int(line.split()[1]) // 1024  # kB → MB
                    break
            else:
                avail_mb = 8192  # 读不到则保守估计 8GB
    except Exception:
        avail_mb = 8192

    # CPU 约束：留 1 核给主进程
    cpu_workers = max(1, cpu_count - 1)

    # 内存约束：可用内存 / 单 worker 预估，留 2GB 给系统 + 主进程
    mem_workers = max(1, (avail_mb - 2048) // _DUCKDB_WORKER_MEM_MB)

    # 表数量约束
    table_workers = max(1, table_count)

    workers = min(cpu_workers, mem_workers, table_workers)
    workers = max(1, workers)

    print(
        f"[INFO] 自适应 worker 计算: cpu={cpu_count} → {cpu_workers}, "
        f"mem_avail={avail_mb}MB → {mem_workers}, "
        f"tables={table_count} → {table_workers}, "
        f"result={workers}"
    )
    return workers


# ---------------------------------------------------------------------------
# 阶段 1：数据拉取（ossutil sync 优先）
# ---------------------------------------------------------------------------

def sync_from_oss(source_dir: str) -> str:
    """将 OSS 上的 source-dir 整体同步到本地 /root/data/tmp 子目录。

    返回本地目录路径。如果 source-dir 已经是本地路径，直接返回原路径。

    策略：
    1. OSS 路径 → ossutil sync 整个文件夹到本地
    2. 本地路径 → 直接使用
    """
    source_dir = source_dir.rstrip("/")

    if not source_dir.startswith(OSS_MOUNT_PREFIX):
        # 已经是本地路径，无需同步
        if os.path.isdir(source_dir):
            print(f"[INFO] 源目录为本地路径，跳过同步: {source_dir}")
            return source_dir
        else:
            print(f"[ERROR] 本地源目录不存在: {source_dir}")
            sys.exit(1)

    # OSS 挂载路径 → ossutil sync
    oss_path = source_dir.replace(OSS_MOUNT_PREFIX, OSS_BUCKET_PREFIX, 1)

    # 生成本地暂存路径：/root/data/tmp/<batch_dir_name>
    dir_name = os.path.basename(source_dir)
    local_dir = os.path.join(LOCAL_STAGING_ROOT, dir_name)

    if os.path.exists(local_dir) and os.listdir(local_dir):
        print(f"[WARN] 本地暂存目录已有内容，跳过同步: {local_dir}")
        print(f"[INFO] 使用已有本地目录: {local_dir}")
        return local_dir

    os.makedirs(local_dir, exist_ok=True)

    print(f"[INFO] ossutil sync: {oss_path} → {local_dir}")
    print(f"[INFO] 同步中，请耐心等待（1.5w 个 db 文件约需 5-15 分钟）...")

    try:
        result = subprocess.run(
            ["ossutil64", "sync", oss_path, local_dir,
             "--update",          # 增量同步，跳过已存在且未修改的文件
             "--jobs", "10",      # 并行上传/下载线程数
             "--parallel", "5"],  # 单文件分片并行数
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1800,  # 30 分钟超时
        )
        if result.returncode != 0:
            print(f"[WARN] ossutil sync 返回非零 ({result.returncode}): {result.stderr}")
            print(f"[WARN] 回退到直接读取 OSS 挂载路径")
            shutil.rmtree(local_dir, ignore_errors=True)
            return source_dir

        db_count = sum(1 for f in os.listdir(local_dir) if f.endswith(".db"))
        print(f"[OK] ossutil sync 完成，本地 {db_count} 个 .db 文件: {local_dir}")
        return local_dir

    except subprocess.TimeoutExpired:
        print(f"[ERROR] ossutil sync 超时（30分钟），回退到 OSS 挂载路径")
        shutil.rmtree(local_dir, ignore_errors=True)
        return source_dir
    except FileNotFoundError:
        print(f"[WARN] ossutil64 不可用，回退到 OSS 挂载路径")
        shutil.rmtree(local_dir, ignore_errors=True)
        return source_dir
    except Exception as e:
        print(f"[ERROR] ossutil sync 失败: {e}，回退到 OSS 挂载路径")
        shutil.rmtree(local_dir, ignore_errors=True)
        return source_dir


def cleanup_staging(local_dir: str, original_source_dir: str):
    """处理完成后清理本地暂存目录。

    仅在 local_dir 是我们创建的暂存目录（/root/data/tmp/ 下）时才删除，
    避免误删用户的原始本地目录。
    """
    if local_dir.startswith(LOCAL_STAGING_ROOT) and local_dir != original_source_dir:
        db_count = sum(1 for f in os.listdir(local_dir) if f.endswith(".db")) if os.path.isdir(local_dir) else 0
        print(f"[INFO] 清理暂存目录: {local_dir} ({db_count} 个 .db 文件)")
        shutil.rmtree(local_dir, ignore_errors=True)
        print(f"[OK] 暂存目录已清理")
    else:
        print(f"[INFO] 源目录为用户原始路径，跳过清理: {local_dir}")


# ---------------------------------------------------------------------------
# 阶段 2：DB 文件发现
# ---------------------------------------------------------------------------

def find_db_files(source_dir: str) -> list[str]:
    """扫描 .db 文件。

    如果 source_dir 是本地暂存目录（非 FUSE 挂载），直接用 os.listdir 最快。
    否则回退到 ossutil64 ls / ls / find 三级策略。
    """
    source_dir = source_dir.rstrip("/")

    # 本地目录（非 FUSE 挂载）直接 os.listdir
    if os.path.isdir(source_dir) and not source_dir.startswith(OSS_MOUNT_PREFIX):
        files = [
            os.path.join(source_dir, f)
            for f in os.listdir(source_dir)
            if f.endswith(".db")
        ]
        if files:
            print(f"[INFO] 本地目录扫描发现 {len(files)} 个 SQLite DB 文件")
            return sorted(files)

    # OSS 挂载路径：三级回退（保持原有逻辑）
    # 1. ossutil64 ls
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

    # 2. ls
    try:
        result = subprocess.run(
            ["ls", source_dir],
            capture_output=True, text=True, timeout=60,
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

    # 3. find
    cmd = ["find", source_dir, "-name", "*.db", "-type", "f"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    files = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    print(f"[INFO] find 发现 {len(files)} 个 SQLite DB 文件")
    return files


# ---------------------------------------------------------------------------
# 阶段 3：并行 ETL（核心逻辑）
# ---------------------------------------------------------------------------

def _get_db_columns(db_path: str, table_name: str) -> list[str] | None:
    """获取某 DB 中某表的列名，表不存在则返回 None。

    注意：此函数在子进程中运行，需独立创建 DuckDB 连接。
    """
    safe_path = db_path.replace("'", "''")
    conn = duckdb.connect()
    try:
        conn.execute("INSTALL sqlite; LOAD sqlite;")
        result = conn.execute(
            f"SELECT * FROM sqlite_scan('{safe_path}', '{table_name}') LIMIT 0"
        )
        return [desc[0] for desc in result.description]
    except Exception:
        return None
    finally:
        conn.close()


def _process_batch(
    batch_idx: int,
    db_files: list[str],
    table_name: str,
    output_dir: str,
) -> str | None:
    """处理一个 batch 的单张表，输出到临时 Parquet 文件。

    返回临时 Parquet 路径；如果该 batch 无数据则返回 None。
    此函数设计为可在子进程中独立运行。
    """
    conn = duckdb.connect()
    try:
        conn.execute("INSTALL sqlite; LOAD sqlite;")

        # 探测列名并集
        all_cols = set()
        db_cols_map: dict[str, list[str]] = {}
        for f in db_files:
            safe_path = f.replace("'", "''")
            try:
                result = conn.execute(
                    f"SELECT * FROM sqlite_scan('{safe_path}', '{table_name}') LIMIT 0"
                )
                cols = [desc[0] for desc in result.description]
                all_cols.update(cols)
                db_cols_map[f] = cols
            except Exception:
                continue

        if not all_cols:
            return None

        all_cols = sorted(all_cols)

        # 构造 UNION ALL
        parts = []
        for f in db_files:
            if f not in db_cols_map:
                continue
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
                f"SELECT '{bag_id}' AS bag_id, {', '.join(col_exprs)} "
                f"FROM sqlite_scan('{safe_path}', '{table_name}')"
            )

        if not parts:
            return None

        union_sql = " UNION ALL ".join(parts)

        # 写入临时 Parquet（每 batch 一个独立文件）
        tmp_parquet = os.path.join(output_dir, f"_tmp_{table_name}_batch_{batch_idx}.parquet")
        conn.execute(
            f"COPY ({union_sql}) TO '{tmp_parquet}' "
            f"(FORMAT PARQUET, ROW_GROUP_SIZE 100000, COMPRESSION 'ZSTD')"
        )
        return tmp_parquet

    except Exception as e:
        print(f"[ERROR] {table_name} batch {batch_idx} 失败: {e}")
        return None
    finally:
        conn.close()


def etl_table_parallel(
    db_files: list[str],
    table_name: str,
    output_dir: Path,
    batch_size: int = BATCH_SIZE,
    workers: int = 4,
) -> str | None:
    """并行提取一张表：将 db_files 分批，每批独立写临时 Parquet，最后合并。

    并行策略：
    - 表间并行：每张表独立子进程
    - 表内并行：batch 级别并发写临时文件，最后串行合并
    """
    parquet_path = output_dir / f"{table_name}.parquet"
    total = len(db_files)

    # 构造 batch 列表
    batches = []
    for i in range(0, total, batch_size):
        batch = db_files[i : i + batch_size]
        batches.append((i // batch_size, batch))

    if not batches:
        print(f"[WARN] {table_name}: 无 DB 文件可处理")
        return None

    print(f"[INFO] {table_name}: {total} 个 DB，{len(batches)} 个 batch，{workers} 并发")

    tmp_parquets: list[str] = []

    # 并行处理每个 batch
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for batch_idx, batch in batches:
            future = pool.submit(
                _process_batch,
                batch_idx, batch, table_name, str(output_dir),
            )
            futures[future] = batch_idx

        for future in as_completed(futures):
            batch_idx = futures[future]
            try:
                tmp_path = future.result()
                if tmp_path:
                    tmp_parquets.append(tmp_path)
                processed = min((batch_idx + 1) * batch_size, total)
                print(
                    f"[INFO] {table_name}: batch {batch_idx} done "
                    f"({processed}/{total}, {processed/total*100:.1f}%)"
                )
            except Exception as e:
                print(f"[ERROR] {table_name} batch {batch_idx} 异常: {e}")

    if not tmp_parquets:
        print(f"[WARN] {table_name}: 所有 batch 均无数据，跳过")
        return None

    # 串行合并所有临时 Parquet
    print(f"[INFO] {table_name}: 合并 {len(tmp_parquets)} 个临时 Parquet → {parquet_path}")
    conn = duckdb.connect()
    try:
        if len(tmp_parquets) == 1:
            # 只有一个 batch，直接重命名
            os.rename(tmp_parquets[0], parquet_path)
        else:
            # 多个 batch：UNION ALL 合并
            parts = [f"SELECT * FROM read_parquet('{p}')" for p in tmp_parquets]
            union_sql = " UNION ALL ".join(parts)
            conn.execute(
                f"COPY ({union_sql}) TO '{parquet_path}' "
                f"(FORMAT PARQUET, ROW_GROUP_SIZE 100000, COMPRESSION 'ZSTD')"
            )
            # 清理临时文件
            for p in tmp_parquets:
                try:
                    os.unlink(p)
                except OSError:
                    pass
    except Exception as e:
        print(f"[ERROR] {table_name} 合并失败: {e}")
        # 清理临时文件
        for p in tmp_parquets:
            try:
                os.unlink(p)
            except OSError:
                pass
        return None
    finally:
        conn.close()

    print(f"[OK] {table_name} → {parquet_path}")
    return str(parquet_path)


# ---------------------------------------------------------------------------
# 阶段 4：Manifest
# ---------------------------------------------------------------------------

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
    # YAML
    yaml_path = output_dir / "manifest.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.dump(manifest, f, allow_unicode=True, sort_keys=False, width=200)
    print(f"[OK] Manifest YAML: {yaml_path}")

    # SQLite
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="SQLite → Parquet ETL (v3: sync + parallel)")
    parser.add_argument("--source-dir", required=True, help="SQLite DB 目录（OSS 挂载路径或本地路径）")
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
    parser.add_argument("--workers", type=int, default=0, help="并行 worker 数（0=自适应，默认 0）")
    parser.add_argument("--skip-sync", action="store_true", help="跳过 ossutil sync，直接使用源目录")
    parser.add_argument("--keep-staging", action="store_true", help="处理完成后保留本地暂存目录")
    args = parser.parse_args()

    original_source_dir = str(Path(args.source_dir).resolve())
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 批次 ID: {args.batch_id}")
    print(f"[INFO] 源目录: {original_source_dir}")
    print(f"[INFO] 输出目录: {output_dir}")

    # ---- 自适应 worker 数 ----
    if args.workers == 0:
        workers = auto_workers(table_count=len(args.tables))
        print(f"[INFO] workers: 自适应 → {workers}")
    else:
        workers = args.workers
        print(f"[INFO] workers: 手动指定 → {workers}")

    # ---- 阶段 1：数据拉取 ----
    if args.skip_sync:
        local_source_dir = original_source_dir
        print(f"[INFO] 跳过 sync，直接使用: {local_source_dir}")
    else:
        local_source_dir = sync_from_oss(original_source_dir)

    try:
        # ---- 阶段 2：扫描 DB 文件 ----
        db_files = find_db_files(local_source_dir)
        if args.max_db > 0 and len(db_files) > args.max_db:
            db_files = db_files[:args.max_db]
            print(f"[INFO] 试点模式：仅处理前 {args.max_db} 个 DB")
        if not db_files:
            print("[ERROR] 未找到任何 .db 文件")
            sys.exit(1)

        # ---- 阶段 3：并行 ETL ----
        table_parquet_map: dict[str, str | None] = {}

        # 表间并行 + 表内并行
        with ProcessPoolExecutor(max_workers=min(workers, len(args.tables))) as pool:
            futures = {}
            for table_name in args.tables:
                print(f"\n[INFO] 启动表处理: {table_name}")
                future = pool.submit(
                    etl_table_parallel,
                    db_files, table_name, output_dir,
                    BATCH_SIZE, max(1, workers // len(args.tables)),
                )
                futures[future] = table_name

            for future in as_completed(futures):
                table_name = futures[future]
                try:
                    path = future.result()
                    table_parquet_map[table_name] = path
                except Exception as e:
                    print(f"[ERROR] 表 {table_name} 处理失败: {e}")
                    table_parquet_map[table_name] = None

        # ---- 阶段 4：Manifest ----
        manifest = generate_manifest(
            batch_id=args.batch_id,
            source_dir=original_source_dir,
            output_dir=output_dir,
            db_count=len(db_files),
            repo_hash=args.repo_hash,
            schema_version=args.schema_version,
            table_parquet_map=table_parquet_map,
        )
        save_manifest(manifest, output_dir)

        print(f"\n[OK] ETL 完成: {args.batch_id}")
        print(f"[OK] 输出目录: {output_dir}")

    finally:
        # ---- 阶段 5：清理暂存 ----
        if not args.keep_staging:
            cleanup_staging(local_source_dir, original_source_dir)
        else:
            print(f"[INFO] 保留暂存目录: {local_source_dir}")


if __name__ == "__main__":
    main()
