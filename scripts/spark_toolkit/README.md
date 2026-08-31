# spark_toolkit — 链路 B 独立脚本包（Spark 批量打标 → 转数据集）

不依赖任何常驻服务的百万级 SQLite DB 批量打标通道。拷到你机器的任意目录即可用。
完整背景与上下游流程见交接手册 `docs/gac/LLM标签开发交接手册.md` §3。

> ⚠️ 内含团队共用的云凭证与 token（AK/SK、StarRocks、数据平台 token），
> 仅限公司内网仓库分发，勿外发。

## 文件

| 文件 | 作用 |
|------|------|
| `run_debug.sh` | 环境变量封装（GSBAG_SDK / LD_LIBRARY_PATH / JAVA_HOME），用 `bash run_debug.sh <py脚本> ...` 跑一切 |
| `task_submit_sqlitedb_query.py` | 提交脚本：上传作业到 OSS + 调 EMR Serverless Spark OpenAPI 起作业 |
| `spark_sqlitedb_query_job.py` | Spark 作业本体（在 EMR 上执行）：读 OSS DB → 逐 DB 执行规则 → 写湖仓结果表 |
| `query_result_to_dataset.py` | 按 `sql_id` 捞结果 → `dm_sdk` 倒查车型 → 写湖仓集合表/数据平台集合 |
| `run_to_dataset.sh` | 上一条的一键封装：`preview` / `test` / `full [bg]` |
| `vendor/` | gsbag / dm_sdk 的 venv 原样副本 + 一键安装脚本（见下） |

## 依赖

```bash
pip install oss2 pymysql tqdm requests \
  alibabacloud_emr_serverless_spark20230808 alibabacloud_tea_openapi alibabacloud_tea_util
```

**gsbag 和 dm_sdk（内部包，原安装包已丢失）用本目录 `vendor/` 的副本直接装**：
这两个包原本只能从内部安装包获得，但安装包已删；实测可行的替代做法是把原虚拟环境
（text2sql/.venv，Python 3.10）site-packages 里的文件直接复制——`vendor/` 就是那份
原样导出：

```bash
cd vendor
bash install_vendor.sh                        # 装到默认 python3（建议用 Python 3.10 的 venv）
bash install_vendor.sh /path/to/.venv/bin/python
```

脚本会把 `gsbag/`、`dm_sdk/`（含 dist-info）、`gsbag_reader/writer_wrapper.so`、
`libgacbag_*.so*` 全部复制进目标环境的 site-packages 并验证 `import`。
dm_sdk 还需几个 pip 依赖：`pip install kafka-python cachetools alibabacloud-oss-v2`。

注意：
- gsbag 的原生封装来自 Python 3.10 环境，**请用 3.10 解释器**（否则导入可能失败；
  dm_sdk 不受影响，>=3.8 即可）；
- gsbag 依赖的一整套原生库（libgacbag/libgsbag/libsqlcipher/libfmt 等，共 30+ 个
  `.so`）也一并放在 `vendor/` 里，安装时同样进 site-packages；导入 gsbag 需
  site-packages 在 `LD_LIBRARY_PATH` 里——`run_debug.sh` 已自动处理；
- 已在干净的 Python 3.10 venv 中端到端验证：安装后 `dm_sdk` / `gsbag.gsbag_reader` /
  `gsbag.gsbag_writer` 导入全部通过；
- `run_debug.sh` 里残留的 `/mnt/data/...` 路径是原机器的，没有可删，不影响。

## 使用（最小闭环）

```bash
# 1) 换批次/换规则：改 spark_sqlitedb_query_job.py 里的常量
#    DB 前缀（要打标的 SQLite DB 目录）、SQL 前缀（.sql/.py 规则目录）
#    规则要求：每条 SQL 输出 start_ts、end_ts、tag_name 三列

# 2) 提交（首次会上传作业脚本；建议先把 JOB_SCRIPT_OSS_KEY 改成你自己名下目录）
bash run_debug.sh task_submit_sqlitedb_query.py --job-name my_tag_0901 --wait
#    → 作业日志末尾打印 sql_id（UUID）

# 3) 转数据集：改 run_to_dataset.sh 顶部【任务配置】(SQL_ID / TAG_NAMES / TASK_ID)
./run_to_dataset.sh preview     # 先看条数/车型分布（不写数据）
./run_to_dataset.sh test        # 20 条试写
./run_to_dataset.sh full bg     # 全量，后台 + 日志 /root/data/logs/db_to_dataset/
```

## 要点

- 结果表：`gac_dlf.default.sqlite_query_result_table`（**无 `_v2`**；服务版 spark-search
  写的是 `sqlite_query_result_table_v2`，两表不互通）。
- 湖仓集合表不支持 DELETE：写入不可回滚，`test`/`full` 前必须 `preview`。
- 转数据集脚本支持去重/断点续写，同一 `--task_id` 重跑是安全的。
- 测试/合成 bag 在 UBM 里查不到来源表，会被跳过不写入，属预期。
