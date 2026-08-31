# encoding=utf-8
"""
湖仓表 sqlite_query_result_table 搜索结果 -> 数据集 转换脚本（自动倒查车型版）

结果表里的 bag_id 是回灌后的 em bin id，通过 dm_sdk 查 ubm_vehicle_module_bin
的 origins 自动得到 origin_table（车型），无需人工指定。

用法分两步：
  # 第一步：只查询+查验（不写任何东西）
  python query_result_to_dataset.py --sql_id 849401f4-f737-477f-8233-61077aae091e \
      --tag_name straight_intersection_with_trafficlight --limit 20

  # 第二步：确认无误后加 --write 真正写入
  python query_result_to_dataset.py --sql_id 849401f4-f737-477f-8233-61077aae091e \
      --tag_name straight_intersection_with_trafficlight --write
"""
import argparse
import datetime
import logging
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

import pymysql
from tqdm import tqdm

# 调大连接池，避免并发倒查时 "Connection pool is full" 警告
# （requests.Session 的默认池大小在定义时固定，只能 patch __init__）
try:
    import requests
    _orig_session_init = requests.Session.__init__

    def _patched_session_init(self, *a, **kw):
        _orig_session_init(self, *a, **kw)
        self.mount('https://', requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64))
        self.mount('http://', requests.adapters.HTTPAdapter(pool_connections=64, pool_maxsize=64))

    requests.Session.__init__ = _patched_session_init
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

MYSQL_CONFIG = {
    "mysql_host": "fe-c-ca4b4d642153fa7e-internal.starrocks.aliyuncs.com",
    "mysql_port": "9030",
    "mysql_user": "gacrnd-paimon",
    "mysql_password": "Zu^19UlX",
    "mysql_database": "gac_dlf.default",
}

# origin_table -> data_mining_table 映射关系（与 dlf_to_collection.py 一致）
TABLE_NAME_MAPPING = {
    "collection_t68_thor_bag_metadata": "data_mining_collection_t68_thor_bag",
    "collection_ay5_thor_bag_metadata": "data_mining_collection_ay5_thor_bag",
    "collection_a02_j6_bag_metadata": "data_mining_collection_a02_j6_bag",
}

# 时间单位 -> 转纳秒的乘数
UNIT_TO_NS = {"s": 10**9, "ms": 10**6, "us": 10**3, "ns": 1}

DEFAULT_TOKEN = "2ddad57eaeec4bb189a205a832ed7867pX1plzjPfr1OkzJvV13kuHIUlbXd-HaHcNq-arQh8Qs="


def execute_query(sql: str, params: tuple = None) -> List[dict]:
    conn = pymysql.connect(
        host=MYSQL_CONFIG['mysql_host'],
        user=MYSQL_CONFIG['mysql_user'],
        password=MYSQL_CONFIG['mysql_password'],
        database=MYSQL_CONFIG['mysql_database'],
        port=int(MYSQL_CONFIG['mysql_port']),
        connect_timeout=600,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        results = cursor.fetchall()
        cursor.close()
        return list(results)
    finally:
        conn.close()


def get_query_results(sql_id: str, tag_name_list: List[str] = None, limit: int = None) -> List[dict]:
    query = """
        SELECT bag_id AS origin_bag_id,
               tag_name,
               start_time AS start_ts,
               end_time   AS end_ts
        FROM sqlite_query_result_table
        WHERE sql_id = %s
    """
    params = [sql_id]
    if tag_name_list:
        placeholders = ", ".join(["%s"] * len(tag_name_list))
        query += f" AND tag_name IN ({placeholders})"
        params += list(tag_name_list)
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    return execute_query(query, tuple(params))


def detect_time_unit(value) -> str:
    """按数值量级判断时间单位（2026 年附近：秒~1.8e9 / 毫秒~1.8e12 / 微秒~1.8e15 / 纳秒~1.8e18）"""
    v = abs(int(value or 0))
    if v >= 10**17:
        return "ns"
    if v >= 10**14:
        return "us"
    if v >= 10**11:
        return "ms"
    return "s"


def resolve_origin_table(client, em_bin_id: str) -> str:
    """em bin id -> origins[0].table（如 collection_a02_j6_bag_metadata）"""
    resp = client.get_bag_metadata(data_id=em_bin_id)
    d = resp.to_dict()
    if d.get('code') != 200:
        raise RuntimeError(f"code={d.get('code')} msg={d.get('msg')}")
    origins = (d.get('data') or {}).get('origins') or []
    if not origins or not origins[0].get('table'):
        raise ValueError("no origins")
    return origins[0]['table']


def resolve_origin_tables(bag_ids: List[str], access_token: str, env: str, workers: int) -> Dict[str, str]:
    """并发倒查所有 em bin 的车型表，返回 {bag_id: origin_table}，失败的跳过并计数"""
    from dm_sdk import ProdDataClient

    # 每个线程一个 client，避免并发问题
    local_clients = {}

    def get_client():
        import threading
        tid = threading.get_ident()
        if tid not in local_clients:
            local_clients[tid] = ProdDataClient(
                access_token=access_token, table='ubm_vehicle_module_bin', env=env)
        return local_clients[tid]

    result = {}
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(resolve_origin_table, get_client(), b): b for b in bag_ids}
        for fut in tqdm(as_completed(futures), total=len(futures), desc="倒查车型", unit="bag"):
            b = futures[fut]
            try:
                result[b] = fut.result()
            except Exception as e:
                failed.append((b, str(e)[:80]))
    if failed:
        tqdm.write(f"[警告] {len(failed)} 个 bag 倒查失败（将跳过），示例: {failed[:3]}")
    return result


def aggregate_records(results: List[dict]) -> List[dict]:
    """同 bag + 同时间窗的多条记录合并标签，避免 upsert 时 tags_replace 互相覆盖"""
    merged = {}
    for r in results:
        key = (r['origin_bag_id'], r['start_ts'], r['end_ts'], r.get('origin_table'))
        if key not in merged:
            merged[key] = dict(r)
            merged[key]['tags'] = {}
        merged[key]['tags'][r['tag_name']] = 1
    return list(merged.values())


def preview(results: List[dict], args):
    print("\n========== 查询结果预览 ==========")
    print(f"总条数: {len(results)}")
    if not results:
        print("没有查到数据！请检查 sql_id / tag_name，或 Spark 作业是否已跑完。")
        return None

    tag_counter = Counter(r['tag_name'] for r in results)
    bag_ids = sorted({r['origin_bag_id'] for r in results})
    print(f"tag 分布: {dict(tag_counter)}")
    print(f"去重 bag(em bin) 数: {len(bag_ids)}")

    print("\n前 5 条样例:")
    for r in results[:5]:
        print(f"  bag_id={r['origin_bag_id']}  tag={r['tag_name']}  "
              f"start_ts={r['start_ts']}  end_ts={r['end_ts']}")

    # 查验点 1：时间单位
    unit = detect_time_unit(results[0]['start_ts'])
    print("\n========== 查验点1: 时间单位 ==========")
    sample = int(results[0]['start_ts'])
    print(f"start_ts 样例值 = {sample} ({len(str(abs(sample)))} 位) -> 判定为 {unit}，写入时自动按此转纳秒")

    # 查验点 2：车型倒查（预览时抽样）
    print("\n========== 查验点2: 车型倒查(抽样) ==========")
    sample_ids = bag_ids[:args.preview_bags]
    origin_map = resolve_origin_tables(sample_ids, args.access_token, args.env, args.workers)
    dist = Counter(origin_map.values())
    print(f"抽样 {len(sample_ids)} 个 bag 的车型分布: {dict(dist)}")
    unknown = set(TABLE_NAME_MAPPING)
    for t in dist:
        if t not in TABLE_NAME_MAPPING:
            print(f"  [警告] 出现映射表之外的车型表: {t}，写入前需先补充 TABLE_NAME_MAPPING")
    return unit


def load_existing_data_ids(client, task_id: str) -> Dict[tuple, str]:
    """查询本任务已写入的全部 bag，返回 {(em_bin_id, start_ns): data_id}，用于防重复写入"""
    from dm_sdk import _eq
    cond = _eq('metadata.task_id', task_id)
    try:
        total = client.count_bags(cond)
    except Exception as e:
        print(f"  [警告] 查询已写记录失败({e})，按全新写入处理")
        return {}
    if not total:
        return {}
    existing = {}
    it = client.iter_search_bags(condition=cond, size=1000,
                                   result_fields=['data_id', 'origins', 'start_timestamp'])
    for rec in tqdm(it, total=total, desc="加载已写记录", unit="bag"):
        origins = rec.get('origins') or []
        if not origins or not rec.get('data_id'):
            continue
        existing[(origins[0].get('bag_id'), rec.get('start_timestamp'))] = rec['data_id']
    return existing


def create_or_extend_collection(ds_client, coll_name: str, table_name: str,
                                 member_ids: List[str], chunk_size: int):
    """建数据集：小批量创建 + 分批追加；集合已存在则拉取现有成员去重后续传"""
    # 1. 查询集合是否已存在
    exists = False
    try:
        resp = ds_client.get_data_collection(name=coll_name).to_dict()
        if resp.get('code') == 200 and resp.get('data'):
            exists = True
    except Exception:
        exists = False

    if exists:
        # 分页拉取已有成员做去重，只追加缺失的（断点续传）
        existing = set()
        page = 1
        while True:
            r = ds_client.get_data_collection_members(name=coll_name, page=page, size=1000).to_dict()
            data = r.get('data') or {}
            items = data.get('data') or []
            existing.update(it['member_id'] for it in items if it.get('member_id'))
            if page >= (data.get('totalPages') or page) or not items:
                break
            page += 1
        todo = [m for m in member_ids if m not in existing]
        print(f"  集合已存在(现有成员 {len(existing)})，本次需追加 {len(todo)}")
        if not todo:
            print("  成员已全部就位，跳过")
            return
    else:
        first = member_ids[:chunk_size]
        resp = ds_client.create_data_collection(
            name=coll_name, table=table_name, description=coll_name, member_ids=first)
        print(f"  数据集创建(首批 {len(first)}): {resp}")
        todo = member_ids[chunk_size:]

    # 2. 分批追加剩余成员（失败即抛异常，重跑时会自动续传）
    for i in tqdm(range(0, len(todo), chunk_size), desc="追加成员", unit="批"):
        batch = todo[i:i + chunk_size]
        ds_client.add_data_collection_members(member_ids=batch, name=coll_name)
    print(f"  成员追加完成: {len(todo)}")


def write_results_to_collection(results: List[dict], task_id: str, tag_version: str,
                                 access_token: str, time_unit: str, env: str, workers: int,
                                 chunk_size: int):
    from dm_sdk import DatasetClient, ProdDataClient

    factor = UNIT_TO_NS[time_unit]
    bag_ids = sorted({r['origin_bag_id'] for r in results})
    print(f"\n开始倒查 {len(bag_ids)} 个 em bin 的车型 (并发={workers}) ...")
    origin_map = resolve_origin_tables(bag_ids, access_token, env, workers)

    for r in results:
        r['origin_table'] = origin_map.get(r['origin_bag_id'])
    results = [r for r in results if r['origin_table'] in TABLE_NAME_MAPPING]
    records = aggregate_records(results)

    # 按车型表分组：一个车型一个数据集（与 dlf_to_collection.py 惯例一致）
    grouped = defaultdict(list)
    for r in records:
        grouped[TABLE_NAME_MAPPING[r['origin_table']]].append(r)

    summary = []
    for table_name, recs in grouped.items():
        print(f"\n>>> 写入 {table_name}: {len(recs)} 条(合并后)")
        client = ProdDataClient(access_token=access_token, table=table_name, env=env)

        # 防重复：先查出本任务已写入的记录，命中的直接复用 data_id
        existing = load_existing_data_ids(client, task_id)
        if existing:
            print(f"  发现本任务已写记录 {len(existing)} 条，命中的将跳过重写")

        tag_clip_data_ids = []
        reused = 0
        for rec in tqdm(recs, desc=f"写入 {table_name}", unit="bag"):
            bag_id = rec['origin_bag_id']
            start_time = int(rec['start_ts']) * factor
            end_time = int(rec['end_ts']) * factor

            data_id = existing.get((bag_id, start_time))
            if data_id:
                tag_clip_data_ids.append(data_id)
                reused += 1
                continue

            try:
                add_bag_resp = client.create_bag_metadata(
                    origins=[(bag_id, rec['origin_table'])],
                    parents=[],
                    start_timestamp=start_time,
                    end_timestamp=end_time,
                    duration=end_time - start_time,
                    metadata={"task_id": [task_id]},
                    return_exist=True,
                )
                bag_data_id = add_bag_resp.to_dict()['data']['data_id']
                client.upsert_bag_tags(
                    data_id=bag_data_id,
                    tag_source="sqlite_search_mining",
                    tags=rec['tags'],
                    tags_replace=True,
                    version=tag_version,
                )
                tag_clip_data_ids.append(bag_data_id)
            except Exception as e:
                tqdm.write(f"  FAIL bag={bag_id}: {e}")
        print(f"  完成 {len(tag_clip_data_ids)}/{len(recs)}（复用已写 {reused}，新写 {len(tag_clip_data_ids) - reused}）")

        if tag_clip_data_ids:
            ds_client = DatasetClient(access_token=access_token, table=table_name, env=env)
            coll_name = task_id + '_' + table_name
            create_or_extend_collection(ds_client, coll_name, table_name,
                                         tag_clip_data_ids, chunk_size)
            summary.append((coll_name, len(tag_clip_data_ids), len(recs)))

        # 原始数据表也建一个数据集（与 dlf_to_collection_v2.py 一致），
        # 成员为原始 bag_id（去重，同一袋多个时间窗只算一次）
        origin_table = recs[0]['origin_table']
        origin_members = list(dict.fromkeys(r['origin_bag_id'] for r in recs))
        if origin_members:
            print(f"\n>>> 写入原始表集合 {origin_table}: {len(origin_members)} 个 bag")
            origin_ds_client = DatasetClient(access_token=access_token,
                                             table=origin_table, env=env)
            origin_coll_name = task_id + '_' + origin_table
            create_or_extend_collection(origin_ds_client, origin_coll_name,
                                         origin_table, origin_members, chunk_size)
            summary.append((origin_coll_name, len(origin_members), len(origin_members)))
    return summary


def main():
    parser = argparse.ArgumentParser(description="湖仓搜索结果转数据集（自动倒查车型）")
    parser.add_argument("--sql_id", required=True, help="Spark 检索作业的 sql_id(uuid)")
    parser.add_argument("--tag_name", action="append", help="标签名，可多次指定；不传则取该 sql_id 全部")
    parser.add_argument("--limit", type=int, default=None, help="结果条数上限，调试用")
    parser.add_argument("--task_id", default=None, help="默认 dev_<时间戳>")
    parser.add_argument("--tag_version", default="v_1_0")
    parser.add_argument("--access_token", default=DEFAULT_TOKEN)
    parser.add_argument("--env", default="prod")
    parser.add_argument("--workers", type=int, default=16, help="倒查并发数")
    parser.add_argument("--chunk_size", type=int, default=5000, help="创建/追加数据集成员的分批大小")
    parser.add_argument("--preview_bags", type=int, default=20, help="预览模式抽样倒查的 bag 数")
    parser.add_argument("--write", action="store_true", help="加上才真正写入，否则只预览查验")
    args = parser.parse_args()

    results = get_query_results(args.sql_id, args.tag_name, args.limit)
    unit = preview(results, args)
    if not results:
        sys.exit(1)

    if not args.write:
        print("\n[预览模式] 未写入任何数据。确认无误后加 --write 再跑一次。")
        return

    task_id = args.task_id or ('dev_' + datetime.datetime.now().strftime('%Y%m%d%H%M'))
    summary = write_results_to_collection(results, task_id, args.tag_version,
                                 args.access_token, unit, args.env, args.workers,
                                 args.chunk_size)

    print("\n" + "=" * 60)
    print("[已完成] 全部流程跑完，数据集如下:")
    for coll_name, members, total in summary:
        print(f"  {coll_name}: 成员 {members}/{total}")
    print("=" * 60)


if __name__ == '__main__':
    main()
