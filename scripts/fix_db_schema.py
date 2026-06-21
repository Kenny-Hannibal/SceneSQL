#!/usr/bin/env python3
"""
修复 SQLite DB 中因空表导致的 schema 缺失问题。

用法：
  1. 在 .env 中设置 BATCH_DIR_FOR_SCHEMA_FIX=/path/to/batch
     例如：BATCH_DIR_FOR_SCHEMA_FIX=/mnt/ubm_code_nas/gac_huangzijian/common_data/sqlite_dbs/20260603_T68_1361_6ec7db_1.5w
  2. 运行：python scripts/fix_db_schema.py

说明：
  - 本脚本对每个 .db 文件检查 STANDARD_SCHEMA 中定义的表和列，缺失则通过
    ALTER TABLE ADD COLUMN 补齐，数据填 NULL。
  - 脚本是幂等的：已经存在的列不会重复添加。
  - 标准 schema 来源于 20260603_T68_1361_6ec7db_1.5w 批次 50 个 DB 的
    PRAGMA table_info 并集。如果后续批次 schema 有变化，可先用
    `python scripts/fix_db_schema.py --scan-only /path/to/batch` 扫描并更新。
"""

import os
import sys
import glob
import sqlite3
import logging
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# 加载 .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# =============================================================================
# 标准 schema：所有 DB 的同名表都应该有的列
# 来源：对 20260603_T68_1361_6ec7db_1.5w 批次 50 个 DB 做 PRAGMA table_info 并集
# =============================================================================
STANDARD_SCHEMA = {
    "ego": {
        "ts": "BIGINT",
        "acc_magnitude": "FLOAT",
        "ego_lane_id": "INTEGER",
        "ego_link_id": "INTEGER",
        "ego_static_map_link_id": "INTEGER",
        "ego_hq_lane_id": "INTEGER",
        "ego_lane_curvature": "FLOAT",
        "ego_to_centerline_dist": "FLOAT",
        "ego_to_left_boundary_dist": "FLOAT",
        "ego_to_right_boundary_dist": "FLOAT",
        "ego_corner_fl_2_left_boundary_dist": "FLOAT",
        "ego_corner_fr_2_left_boundary_dist": "FLOAT",
        "ego_corner_rl_2_left_boundary_dist": "FLOAT",
        "ego_corner_rr_2_left_boundary_dist": "FLOAT",
        "ego_corner_fl_2_right_boundary_dist": "FLOAT",
        "ego_corner_fr_2_right_boundary_dist": "FLOAT",
        "ego_corner_rl_2_right_boundary_dist": "FLOAT",
        "ego_corner_rr_2_right_boundary_dist": "FLOAT",
        "traffic_light_status": "INTEGER",
        "ego_hq_lane_ids_on_cross_section": "TEXT",
        "ego_lane_index_on_hq_cross_section": "INTEGER",
        "ego_lane_successor_count": "INTEGER",
        "ego_lane_predecessor_count": "INTEGER",
        "ego_lane_width": "FLOAT",
        "gl_dis_to_right": "FLOAT",
        "gl_dis_to_left": "FLOAT",
        "gl_lane_cnt": "INTEGER",
        "gl_left_cnt": "INTEGER",
        "gl_right_cnt": "INTEGER",
        "ego_dr_trajectory": "TEXT",
        "split_merge_tag": "TEXT",
        "specify_topology_tag": "TEXT",
        "topology_constraint_tag": "TEXT",
        "ts_ms": "FLOAT",
        "speed": "FLOAT",
        "steering_angle": "FLOAT",
        "latest_traffic_light_status": "TEXT",
        "latest_stop_line_direction": "FLOAT",
        "navigation_status": "TEXT",
        "wiper_status": "TEXT",
        "indicator_status": "TEXT",
        "cumulative_distance": "FLOAT",
        "utm_x": "FLOAT",
        "utm_y": "FLOAT",
        "utm_yaw": "FLOAT",
    },
    "dynamic_obj": {
        "ts": "BIGINT",
        "obj_id": "TEXT",
        "obs_lane_id": "INTEGER",
        "obs_static_map_link_id": "INTEGER",
        "obs_hq_lane_id": "INTEGER",
        "obs_to_left_boundary_dist": "FLOAT",
        "obs_to_right_boundary_dist": "FLOAT",
        "obs_corner_fl_2_left_boundary_dist": "FLOAT",
        "obs_corner_fr_2_left_boundary_dist": "FLOAT",
        "obs_corner_rl_2_left_boundary_dist": "FLOAT",
        "obs_corner_rr_2_left_boundary_dist": "FLOAT",
        "obs_corner_fl_2_right_boundary_dist": "FLOAT",
        "obs_corner_fr_2_right_boundary_dist": "FLOAT",
        "obs_corner_rl_2_right_boundary_dist": "FLOAT",
        "obs_corner_rr_2_right_boundary_dist": "FLOAT",
        "obs_to_centerline_dist": "FLOAT",
        "obs_dr_trajectory": "TEXT",
        "ts_ms": "FLOAT",
        "x": "FLOAT",
        "y": "FLOAT",
        "z": "FLOAT",
        "l": "FLOAT",
        "w": "FLOAT",
        "h": "FLOAT",
        "heading": "FLOAT",
        "type": "TEXT",
        "absolute_velocity_x": "FLOAT",
        "absolute_velocity_y": "FLOAT",
        "relative_velocity_x": "FLOAT",
        "relative_velocity_y": "FLOAT",
        "is_static": "BOOLEAN",
    },
    "static_obj": {
        "ts": "BIGINT",
        "obj_id": "TEXT",
        "ts_ms": "FLOAT",
        "x": "FLOAT",
        "y": "FLOAT",
        "z": "FLOAT",
        "l": "FLOAT",
        "w": "FLOAT",
        "h": "FLOAT",
        "heading": "FLOAT",
        "type": "TEXT",
        "param": "TEXT",
    },
    "static_link": {
        "link_id": "TEXT",
        "link_type": "TEXT",
        "link_turn_type": "TEXT",
        "link_class": "TEXT",
        "link_attribute": "TEXT",
        "link_speed_limit": "FLOAT",
        "link_predecessor": "TEXT",
        "link_successor": "TEXT",
        "link_exp_speed_limit": "FLOAT",
        "is_intersection_out": "TEXT",
    },
    "static_lane": {
        "lane_id": "TEXT",
        "lane_type": "TEXT",
        "lane_turn_type": "TEXT",
        "lane_trans_type": "TEXT",
        "link_id": "INTEGER",
        "lane_relate_obs_id": "TEXT",
    },
    "dynamic_link": {
        "ts": "BIGINT",
        "link_id": "TEXT",
        "link_type": "TEXT",
        "link_attribute": "TEXT",
        "link_exp_speed_limit": "FLOAT",
        "link_predecessor": "TEXT",
        "link_successor": "TEXT",
        "include_lane_ids": "TEXT",
    },
    "dynamic_lane": {
        "ts": "BIGINT",
        "lane_id": "TEXT",
        "ref_link_id": "INTEGER",
        "left_boundary_id": "INTEGER",
        "right_boundary_id": "INTEGER",
        "lane_type": "TEXT",
        "lane_turn_type": "TEXT",
        "lane_trans_type": "TEXT",
        "predecessors": "TEXT",
        "successors": "TEXT",
        "lane_relate_obs_ids": "TEXT",
    },
    "range_tag": {
        "start_ts": "BIGINT",
        "end_ts": "BIGINT",
        "tag_name": "TEXT",
        "param": "TEXT",
    },
    "intersection_info": {
        "intersection_id": "TEXT",
        "lane_count": "BIGINT",
        "ego_lane_index": "BIGINT",
        "lane_info": "TEXT",
    },
}


def get_existing_columns(conn: sqlite3.Connection, table: str) -> set:
    """获取指定表的已有列名集合。"""
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def merge_schema(base_schema: dict, scan_schema: dict) -> dict:
    """
    合并基础 schema 和扫描得到的 schema。
    - 列取并集
    - 类型冲突时，base_schema 优先
    """
    merged = {}
    all_tables = set(base_schema.keys()) | set(scan_schema.keys())
    for table in all_tables:
        merged[table] = {}
        base_cols = base_schema.get(table, {})
        scan_cols = scan_schema.get(table, {})
        all_cols = set(base_cols.keys()) | set(scan_cols.keys())
        for col in all_cols:
            # base_schema 优先定义类型；base 里没有再从 scan 里取
            dtype = base_cols.get(col) or scan_cols.get(col)
            merged[table][col] = dtype
    return merged


def scan_batch_schema(batch_dir: str, sample_size: int = 100) -> dict:
    """
    扫描批次内 DB 的 schema 并集。

    为了兼顾速度和准确性：
    - 如果 DB 数量 <= sample_size，全部扫描
    - 如果 DB 数量 > sample_size，均匀采样 sample_size 个

    实际场景中，同一批次绝大多数 DB 的 schema 是一致的，采样足够发现
    缺失列和新增列。空表/残缺表通常只占很小比例。
    """
    table_cols = defaultdict(lambda: defaultdict(set))
    db_files = sorted(glob.glob(os.path.join(batch_dir, "*.db")))
    if not db_files:
        logger.warning(f"No .db files found in {batch_dir}")
        return {}

    total = len(db_files)
    if total <= sample_size:
        sampled = db_files
        logger.info(f"扫描全部 {total} 个 DB 的 schema")
    else:
        step = max(1, total // sample_size)
        sampled = db_files[::step][:sample_size]
        logger.info(f"从 {total} 个 DB 中采样 {len(sampled)} 个进行 schema 扫描")

    for db_path in sampled:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cursor.fetchall()]
            for table in tables:
                cursor.execute(f"PRAGMA table_info({table})")
                for row in cursor.fetchall():
                    col_name, col_type = row[1], row[2]
                    table_cols[table][col_name].add(col_type)
        finally:
            conn.close()

    # 转换为 {table: {col: type}} 形式，类型冲突时取出现次数最多的
    result = {}
    for table, cols in table_cols.items():
        result[table] = {}
        for col, types in cols.items():
            result[table][col] = sorted(types, key=lambda t: list(types).count(t))[-1]
    return result


def fix_db(db_path: str, standard_schema: dict) -> int:
    """
    修复单个 DB 的 schema，返回添加的列数。
    """
    added_count = 0
    conn = sqlite3.connect(db_path)
    try:
        for table, cols in standard_schema.items():
            existing = get_existing_columns(conn, table)
            if not existing:
                # 表不存在就跳过（不应擅自建表，避免污染）
                continue
            for col, dtype in cols.items():
                if col not in existing:
                    sql = f"ALTER TABLE {table} ADD COLUMN {col} {dtype}"
                    conn.execute(sql)
                    logger.info(f"[{os.path.basename(db_path)}] ADD COLUMN {table}.{col} {dtype}")
                    added_count += 1
        conn.commit()
    finally:
        conn.close()
    return added_count


def main():
    parser = argparse.ArgumentParser(description="Fix SQLite DB schema for empty tables")
    parser.add_argument(
        "--scan-only",
        metavar="BATCH_DIR",
        help="只扫描批次内所有 DB 的 schema 并集，不修改任何文件",
    )
    parser.add_argument(
        "--batch-dir",
        metavar="BATCH_DIR",
        help="指定批次目录（优先级高于 .env 中的 BATCH_DIR_FOR_SCHEMA_FIX）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="并发线程数（默认从环境变量 SCHEMA_FIX_WORKERS 读取，否则 8）",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="禁用进度条（适合后台运行重定向到日志）",
    )
    args = parser.parse_args()

    if args.scan_only:
        logger.info(f"Scan-only mode: {args.scan_only}")
        schema = scan_batch_schema(args.scan_only)
        print("\n# 扫描得到的 schema 并集，可用于更新 STANDARD_SCHEMA：\n")
        print("STANDARD_SCHEMA = {")
        for table, cols in sorted(schema.items()):
            print(f'    "{table}": {{')
            for col, dtype in sorted(cols.items()):
                print(f'        "{col}": "{dtype}",')
            print("    },")
        print("}")
        return

    batch_dir = args.batch_dir or os.getenv("BATCH_DIR_FOR_SCHEMA_FIX")
    if not batch_dir:
        logger.error(
            "请指定批次目录：\n"
            "  1) .env 中设置 BATCH_DIR_FOR_SCHEMA_FIX=/path/to/batch\n"
            "  2) 或命令行传入 --batch-dir /path/to/batch"
        )
        sys.exit(1)

    if not os.path.isdir(batch_dir):
        logger.error(f"批次目录不存在: {batch_dir}")
        sys.exit(1)

    db_files = sorted(glob.glob(os.path.join(batch_dir, "*.db")))
    if not db_files:
        logger.warning(f"目录下没有 .db 文件: {batch_dir}")
        return

    # 先扫描批次 schema 并集，再和 STANDARD_SCHEMA 合并
    sample_size = int(os.getenv("SCHEMA_SCAN_SAMPLE_SIZE", "100"))
    logger.info(f"开始扫描批次 schema: {batch_dir}")
    scan_schema = scan_batch_schema(batch_dir, sample_size=sample_size)
    standard_schema = merge_schema(STANDARD_SCHEMA, scan_schema)

    # 打印合并后的 schema 摘要
    total_cols = sum(len(cols) for cols in standard_schema.values())
    logger.info(
        f"合并完成：{len(standard_schema)} 个表，共 {total_cols} 列 "
        f"（STANDARD_SCHEMA {sum(len(cols) for cols in STANDARD_SCHEMA.values())} 列，"
        f"扫描新增 {total_cols - sum(len(cols) for cols in STANDARD_SCHEMA.values())} 列）"
    )

    logger.info(f"开始修复，共 {len(db_files)} 个 DB 文件")

    # 并发 worker 数
    max_workers = args.workers
    if max_workers is None:
        max_workers = int(os.getenv("SCHEMA_FIX_WORKERS", "8"))
    logger.info(f"并发数: {max_workers}")

    total_added = 0
    failed = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_db = {
            executor.submit(fix_db, db_path, standard_schema): db_path
            for db_path in db_files
        }

        # 用 tqdm 显示进度；非 TTY 或 --no-progress 时自动关闭
        use_progress = not args.no_progress and sys.stdout.isatty()
        with tqdm(total=len(db_files), desc="修复 DB", unit="file", disable=not use_progress) as pbar:
            for future in as_completed(future_to_db):
                db_path = future_to_db[future]
                try:
                    added = future.result()
                    total_added += added
                except Exception as exc:
                    logger.error(f"修复失败 {db_path}: {exc}")
                    failed.append(db_path)
                pbar.update(1)
                # 可选：在进度条上显示实时统计
                pbar.set_postfix({"added": total_added, "failed": len(failed)})

    logger.info(f"完成。共添加 {total_added} 列，失败 {len(failed)} 个 DB")
    if failed:
        for p in failed:
            logger.info(f"  失败: {p}")


if __name__ == "__main__":
    main()
