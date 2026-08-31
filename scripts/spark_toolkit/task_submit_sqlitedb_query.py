# -*- coding: utf-8 -*-
"""
通过 EMR Serverless Spark OpenAPI 提交 spark_sqlitedb_query_job.py，
使原 notebook magic 脚本可以在任意非 Spark 平台环境（本地/DSW/CI）提交。

参照 task_submit.py 的形式：SDK Client + JobDriverSparkSubmit + StartJobRunRequest。

依赖：
    pip install alibabacloud_emr_serverless_spark20230808 alibabacloud_tea_openapi alibabacloud_tea_util oss2

用法：
    python task_submit_sqlitedb_query.py                # 上传作业脚本到 OSS 并提交
    python task_submit_sqlitedb_query.py --no-upload    # 作业脚本已在 OSS，只提交
    python task_submit_sqlitedb_query.py --wait         # 提交后轮询任务状态直到结束
"""

import argparse
import os
import time
from typing import List

import oss2
from alibabacloud_emr_serverless_spark20230808.client import Client
from alibabacloud_emr_serverless_spark20230808.models import (
    StartJobRunRequest,
    GetJobRunRequest,
    Tag,
    JobDriver,
    JobDriverSparkSubmit,
)
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models

# ============ 提交侧配置 ============
ACCESS_KEY_ID = os.environ["ALIBABA_CLOUD_ACCESS_KEY_ID"]
ACCESS_KEY_SECRET = os.environ["ALIBABA_CLOUD_ACCESS_KEY_SECRET"]
REGION_ID = "cn-wulanchabu"
WORKSPACE_ID = "w-3fa048e86117d91f"
RESOURCE_QUEUE_ID = "dev_queue"
RELEASE_VERSION = "esr-4.8.0 (Spark 3.5.2, Scala 2.12, Java Runtime)"

# 作业脚本上传位置（gacrnd-oss 可被 Spark 侧访问）
CODE_BUCKET_NAME = "gacrnd-oss"
CODE_BUCKET_ENDPOINT = "oss-cn-wulanchabu-internal.aliyuncs.com"
JOB_SCRIPT_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spark_sqlitedb_query_job.py")
JOB_SCRIPT_OSS_KEY = "gac_huangzijian/test_code/spark_sqlitedb_query_job.py"
JOB_OSS_FILE = f"oss://{CODE_BUCKET_NAME}/{JOB_SCRIPT_OSS_KEY}"

# 原 %%emr_serverless_spark magic 中的 spark_conf，转成 spark-submit --conf
SPARK_SUBMIT_CONF = (
    "--conf spark.driver.cores=32"
    " --conf spark.driver.memory=64g"
    " --conf spark.executor.cores=32"
    " --conf spark.executor.memory=64g"
    " --conf spark.executor.instances=6"
    " --conf spark.mongodb.write.connection.uri=mongodb://infra_raw_data:U10uw123@s-0jl0728f412959f4.mongodb.rds.aliyuncs.com:3717"
    " --conf spark.mongodb.read.connection.uri=mongodb://infra_raw_data:U10uw123@s-0jl0728f412959f4.mongodb.rds.aliyuncs.com:3717"
    " --conf spark.emr.serverless.user.defined.jars="
    "oss://gacrnd-oss/u_zhangjiabo/jars/mongo-spark-connector_2.12-10.4.1.jar,"
    "oss://gacrnd-oss/u_zhangjiabo/jars/mongodb-driver-core-5.0.1.jar,"
    "oss://gacrnd-oss/u_zhangjiabo/jars/mongodb-driver-sync-5.0.1.jar,"
    "oss://gacrnd-oss/u_zhangjiabo/jars/bson-5.0.1.jar"
    " --conf spark.emr.serverless.environmentId=ev-7pp5qyy5u8dcgt0k"
    " --conf spark.emr.serverless.mount.oss.enabled=false"
    " --conf spark.emr.serverless.network.service.name=gac_infra"
)

TERMINAL_STATES = {"Success", "Failed", "Cancelled", "CancelFailed"}


class SqliteQueryTaskSubmitExecutor:

    @staticmethod
    def __create_client() -> Client:
        config = open_api_models.Config(
            access_key_id=ACCESS_KEY_ID,
            access_key_secret=ACCESS_KEY_SECRET,
        )
        config.endpoint = f"emr-serverless-spark.{REGION_ID}.aliyuncs.com"
        return Client(config)

    @staticmethod
    def upload_job_script():
        """把本地作业脚本上传到 OSS，供 spark-submit 引用。"""
        auth = oss2.Auth(ACCESS_KEY_ID, ACCESS_KEY_SECRET)
        bucket = oss2.Bucket(auth, CODE_BUCKET_ENDPOINT, CODE_BUCKET_NAME)
        with open(JOB_SCRIPT_LOCAL, "rb") as f:
            bucket.put_object(JOB_SCRIPT_OSS_KEY, f)
        print(f"作业脚本已上传: {JOB_OSS_FILE}")

    @staticmethod
    def submit(job_name="sqlite_query_task") -> str | None:
        print("提交 Spark 作业到 EMR Serverless...")
        client = SqliteQueryTaskSubmitExecutor.__create_client()
        tags: List[Tag] = [Tag("environment", "production"), Tag("workflow", "true")]

        job_driver_spark_submit = JobDriverSparkSubmit(
            JOB_OSS_FILE,
            [],  # 作业脚本不需要运行时参数
            SPARK_SUBMIT_CONF,
        )

        start_job_run_request = StartJobRunRequest(
            region_id=REGION_ID,
            resource_queue_id=RESOURCE_QUEUE_ID,
            code_type="PYTHON",
            name=job_name,
            release_version=RELEASE_VERSION,
            tags=tags,
            job_driver=job_driver,
            fusion=False,
        )

        runtime = util_models.RuntimeOptions()
        headers = {}
        try:
            response = client.start_job_run_with_options(
                WORKSPACE_ID, start_job_run_request, headers, runtime
            )
            print(f"response: {response.body.to_map()}")
            return response.body.job_run_id
        except Exception as error:
            print(f"提交失败，错误日志如下： {error}")
            return None

    @staticmethod
    def wait(job_run_id: str, interval: int = 30):
        """轮询任务状态直到进入终态。"""
        client = SqliteQueryTaskSubmitExecutor.__create_client()
        get_request = GetJobRunRequest(region_id=REGION_ID)
        runtime = util_models.RuntimeOptions()
        headers = {}
        while True:
            response = client.get_job_run_with_options(
                WORKSPACE_ID, job_run_id, get_request, headers, runtime
            )
            job_run = response.body.job_run
            state = job_run.state
            print(f"[{job_run_id}] state={state}")
            if state in TERMINAL_STATES:
                return state
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="提交 SQLite 批量查询 Spark 作业")
    parser.add_argument("--no-upload", action="store_true", help="跳过上传作业脚本（OSS 上已有最新版本时）")
    parser.add_argument("--wait", action="store_true", help="提交后轮询任务状态直到结束")
    parser.add_argument("--job-name", default="sqlite_query_task", help="作业名称")
    args = parser.parse_args()

    if not args.no_upload:
        SqliteQueryTaskSubmitExecutor.upload_job_script()

    job_run_id = SqliteQueryTaskSubmitExecutor.submit(args.job_name)
    if job_run_id is None:
        raise SystemExit(1)
    print(f"job_run_id: {job_run_id}")

    if args.wait:
        final_state = SqliteQueryTaskSubmitExecutor.wait(job_run_id)
        print(f"任务最终状态: {final_state}")


if __name__ == "__main__":
    main()
