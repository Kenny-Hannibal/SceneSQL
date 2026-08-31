# -*- coding: utf-8 -*-
"""
SQLite 批量查询 Spark 作业脚本（自包含版本）。

原脚本依赖阿里云 EMR Serverless Spark notebook 的 %%emr_serverless_spark
magic 与预置的 spark/sc 变量，只能在平台 notebook 中运行。
本版本改为标准 pyspark 入口：自建 SparkSession，spark 配置由
task_submit 类脚本通过 spark-submit --conf 注入，可在任意环境提交。
"""

import os
import re
import sqlite3
import uuid

import oss2
from pyspark.sql import SparkSession, Row

# ============ DB 文件配置 ============
DB_BUCKET_NAME = "gacrnd-infra-datamining"
DB_BUCKET_ENDPOINT = "oss-cn-wulanchabu-internal.aliyuncs.com"
DB_OSS_PATH = "sqlite_dbs/ubm_production_260709"

# ============ SQL 文件配置 ============
SQL_BUCKET_NAME = "gacrnd-oss"
SQL_BUCKET_ENDPOINT = "oss-cn-wulanchabu-internal.aliyuncs.com"
SQL_OSS_PATH = "gac_huangzijian/sql_production/dataworks_0803"

# ============ 通用配置 ============
OSS_ACCESS_KEY_ID = os.environ["OSS_ACCESS_KEY_ID"]
OSS_ACCESS_KEY_SECRET = os.environ["OSS_ACCESS_KEY_SECRET"]
TARGET_TABLE = "gac_dlf.default.sqlite_query_result_table"
VERSION_TABLE = "gac_dlf.default.sql_version_table"
NUM_PARTITIONS = 500


def get_oss_bucket(bucket_name, endpoint):
    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    return oss2.Bucket(auth, endpoint, bucket_name)


def get_oss_db_files(bucket, prefix):
    files = []
    for obj in oss2.ObjectIteratorV2(bucket, prefix=prefix):
        if obj.key.endswith('.db'):
            files.append(f"oss://{bucket.bucket_name}/{obj.key}")
    return files


def parse_py_sql(content_str):
    sqls = []
    for m in re.finditer(r'sql\s*=\s*"""(.*?)"""', content_str, re.DOTALL):
        sqls.append(m.group(1).strip())
    for m in re.finditer(r"""sql\s*=\s*["']([^"']+)["']""", content_str):
        s = m.group(1).strip()
        if s not in sqls:
            sqls.append(s)
    return sqls


def fetch_sql_queries_from_oss(bucket, prefix):
    queries = []
    for obj in oss2.ObjectIteratorV2(bucket, prefix=prefix):
        key = obj.key
        filename = key.rsplit('/', 1)[-1] if '/' in key else key

        if key.endswith('.sql'):
            content = bucket.get_object(key).read().decode('utf-8')
            queries.append({"sql": content.strip(), "source": filename})

        elif key.endswith('.py'):
            content = bucket.get_object(key).read().decode('utf-8')
            for sql_text in parse_py_sql(content):
                queries.append({"sql": sql_text, "source": filename})

    return queries


def process_sqlite_in_memory(iterator, sql_list, uuid_str):
    db_bucket = get_oss_bucket(DB_BUCKET_NAME, DB_BUCKET_ENDPOINT)

    results = []
    for oss_path in iterator:
        try:
            oss_key = oss_path.replace(f"oss://{DB_BUCKET_NAME}/", "")
            db_bytes = db_bucket.get_object(oss_key).read()
            bag_id = oss_key.split("/")[-1].replace(".db", "")

            conn = sqlite3.connect(':memory:')
            conn.deserialize(db_bytes)

            for item in sql_list:
                sql = item["sql"]
                source = item["source"]
                try:
                    cursor = conn.cursor()
                    cursor.execute(sql)
                    rows = cursor.fetchall()
                    col_names = [desc[0] for desc in cursor.description]
                    col_index = {name: i for i, name in enumerate(col_names)}

                    for row in rows:
                        results.append(Row(
                            bag_id=bag_id,
                            start_ts=row[col_index["start_ts"]] if "start_ts" in col_index else None,
                            end_ts=row[col_index["end_ts"]] if "end_ts" in col_index else None,
                            tag_name=row[col_index["tag_name"]] if "tag_name" in col_index else None,
                            sql_id=uuid_str,
                            param=source
                        ))
                    cursor.close()
                except Exception as e:
                    print(f"[SQL执行失败] db={bag_id} source={source}: {e}")

            conn.close()
        except Exception as e:
            print(f"[DB读取失败] {oss_path}: {e}")

    return iter(results)


def main():
    spark = SparkSession.builder.appName("sqlite_query_job").getOrCreate()
    sc = spark.sparkContext

    db_bucket = get_oss_bucket(DB_BUCKET_NAME, DB_BUCKET_ENDPOINT)
    sql_bucket = get_oss_bucket(SQL_BUCKET_NAME, SQL_BUCKET_ENDPOINT)

    # 1. 从 SQL bucket 读取所有 SQL 查询（不落盘）
    sql_queries = fetch_sql_queries_from_oss(sql_bucket, SQL_OSS_PATH)
    print(f"从 OSS 读取到 {len(sql_queries)} 条 SQL 查询:")
    for q in sql_queries:
        print(f"  [{q['source']}] {q['sql'][:80]}...")

    sql_uuid = uuid.uuid4()
    uuid_str = str(sql_uuid)

    # 2. 写入 sql_version_table
    version_rows = [(uuid_str, q["sql"]) for q in sql_queries]
    version_df = spark.createDataFrame(version_rows, ["id", "sql"])
    version_df.write.mode("append").saveAsTable(VERSION_TABLE)

    # 3. Broadcast SQL 列表到所有 Executor
    sql_bc = sc.broadcast(sql_queries)

    # 4. 从 DB bucket 获取 db 文件列表并分区
    sqlite_files = get_oss_db_files(db_bucket, DB_OSS_PATH)
    print(f"共发现 {len(sqlite_files)} 个 SQLite 文件")
    rdd_paths = sc.parallelize(sqlite_files, numSlices=NUM_PARTITIONS)

    # 5. 分布式执行
    result_rdd = rdd_paths.mapPartitions(
        lambda it: process_sqlite_in_memory(it, sql_bc.value, uuid_str)
    )

    # 6. 结果写入湖仓
    result_rdd.count()
    for row in result_rdd.take(5):
        print(row)

    schema = ["bag_id", "start_ts", "end_ts", "tag_name", "sql_id", "param"]
    result_df = spark.createDataFrame(result_rdd, schema)
    result_df.write.mode("append").saveAsTable(TARGET_TABLE)
    print(f"sql_id: {uuid_str}")
    print("湖仓表写入完成")

    sql_bc.destroy()
    spark.stop()


if __name__ == "__main__":
    main()
