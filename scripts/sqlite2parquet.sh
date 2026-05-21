#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# 加载 .env（不覆盖已设置的环境变量）
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

source .venv/bin/activate

# --- 从 .env / 环境变量读取配置 ---

# 数据挖掘仓库路径（用于 repo-hash）
DATA_MINING_PROJECT_PATH="${DATA_MINING_PROJECT_PATH:-/root/data/data_mining/UBM_mining/ubm_data_mining}"
REPO_HASH=$(cd "${DATA_MINING_PROJECT_PATH}" && git rev-parse HEAD 2>/dev/null || echo "unknown")

# 批次 ID
BATCH_ID="${ETL_BATCH_ID:-20260515_T68_1131_5bb5ec_1.5w}"

# 源目录：优先 SOURCE_DIR，其次从 SQLITE_DB_PATH + BATCH_ID 推导
if [ -z "${SOURCE_DIR:-}" ]; then
    if [ -n "${SQLITE_DB_PATH:-}" ]; then
        # SQLITE_DB_PATH 可能是 common_data 级别或具体 batch 级别
        if [ -d "${SQLITE_DB_PATH}/sqlite_dbs" ]; then
            # common_data 级别：自动拼接 sqlite_dbs/BATCH_ID
            SOURCE_DIR="${SQLITE_DB_PATH}/sqlite_dbs/${BATCH_ID}"
        else
            # 直接是 batch 目录（向后兼容）
            SOURCE_DIR="${SQLITE_DB_PATH}"
        fi
    else
        SOURCE_DIR="/mnt/gacrnd-oss/gac_liulian/common_data/sqlite_dbs/${BATCH_ID}"
    fi
fi

# 输出目录：优先 OUTPUT_DIR，其次从 ETL_BASE_PATH 推导
if [ -z "${OUTPUT_DIR:-}" ]; then
    if [ -n "${ETL_BASE_PATH:-}" ]; then
        OUTPUT_DIR="${ETL_BASE_PATH}/${BATCH_ID}"
    else
        OUTPUT_DIR="/mnt/gacrnd-oss/gac_liulian/common_data/parquet/${BATCH_ID}"
    fi
fi

# 并行 / 控制
WORKERS="${ETL_WORKERS:-${WORKERS:-0}}"
MAX_DB="${ETL_MAX_DB:-${MAX_DB:-0}}"
SKIP_SYNC="${ETL_SKIP_SYNC:-${SKIP_SYNC:-0}}"
KEEP_STAGING="${ETL_KEEP_STAGING:-${KEEP_STAGING:-0}}"

# --- 构建 CLI 参数 ---
CLI_ARGS=(
    --source-dir "${SOURCE_DIR}"
    --output-dir "${OUTPUT_DIR}"
    --batch-id "${BATCH_ID}"
    --repo-hash "${REPO_HASH}"
    --workers "${WORKERS}"
)

if [ -n "${ETL_TABLES:-}" ]; then
    CLI_ARGS+=(--tables ${ETL_TABLES})
fi

[ "${MAX_DB}" != "0" ] && CLI_ARGS+=(--max-db "${MAX_DB}")
[ "${SKIP_SYNC}" = "1" ] && CLI_ARGS+=(--skip-sync)
[ "${KEEP_STAGING}" = "1" ] && CLI_ARGS+=(--keep-staging)

# --- 启动 ---
echo "[INFO] Batch ID:      ${BATCH_ID}"
echo "[INFO] Source:        ${SOURCE_DIR}"
echo "[INFO] Output:        ${OUTPUT_DIR}"
echo "[INFO] Tables:        ${ETL_TABLES:-<default 9 tables>}"
echo "[INFO] Workers:       ${WORKERS}"
echo "[INFO] Max DB:        ${MAX_DB}"
echo "[INFO] Skip Sync:     ${SKIP_SYNC}"
echo "[INFO] Keep Staging:  ${KEEP_STAGING}"
echo "[INFO] Repo Hash:     ${REPO_HASH}"

nohup python -u agent/backend/app/services/etl/etl_sqlite_to_parquet.py \
    "${CLI_ARGS[@]}" \
    > etl_v3.log 2>&1 &

echo "[INFO] ETL v3 已后台启动, PID=$!"
echo "[INFO] 日志: etl_v3.log"
echo "[INFO] 跟踪: tail -f etl_v3.log"
