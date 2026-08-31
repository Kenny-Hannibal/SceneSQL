#!/bin/bash
# =============================================================
# 湖仓搜索结果 -> 数据集 一键启动脚本
#
# 用法（在本目录下执行）:
#   ./run_to_dataset.sh preview        # 只预览查验，不写任何数据（安全，随便跑）
#   ./run_to_dataset.sh test           # 小批量 20 条 + 真实写入（全量前必做的验证）
#   ./run_to_dataset.sh full           # 全量写入（前台跑，SSH 断连会中断）
#   ./run_to_dataset.sh full bg        # 全量写入，后台运行，日志写到 /root/data/logs/
#
# 换一批数据时，只需改下面【任务配置】里的 SQL_ID 和 TAG_NAMES。
# =============================================================
cd "$(dirname "$0")"

# ------------------ 任务配置（换任务改这里） ------------------
SQL_ID="96ee8aa0-1bf8-4d33-96da-abbb444cd7dd"          # Spark 检索作业的 sql_id
TAG_NAMES=("长闪黄灯_v5")  # 标签，可加多个，用空格分隔
TASK_ID="diverg_converg_straight_20260828"        # 数据集名前缀，如 "div_converg_straight_20260812"；留空则自动用 dev_<时间戳>
TEST_LIMIT=20     # test 模式的小批量条数
WORKERS=16        # 倒查车型的并发数
PY=python3        # 如换环境可改成 /root/data/text2sql/.venv/bin/python
# --------------------------------------------------------------

MODE=${1:-preview}
BG=${2:-}

# 拼 --tag_name 参数
TAG_ARGS=""
for t in "${TAG_NAMES[@]}"; do
    TAG_ARGS="$TAG_ARGS --tag_name $t"
done

# 拼 --task_id 参数
TASK_ARGS=""
if [ -n "$TASK_ID" ]; then
    TASK_ARGS="--task_id $TASK_ID"
fi

case "$MODE" in
    preview)
        CMD="$PY query_result_to_dataset.py --sql_id $SQL_ID $TAG_ARGS"
        ;;
    test)
        CMD="$PY query_result_to_dataset.py --sql_id $SQL_ID $TAG_ARGS --limit $TEST_LIMIT --write $TASK_ARGS"
        ;;
    full)
        CMD="$PY query_result_to_dataset.py --sql_id $SQL_ID $TAG_ARGS --write --workers $WORKERS $TASK_ARGS"
        ;;
    *)
        echo "未知模式: $MODE（可选: preview / test / full）"
        exit 1
        ;;
esac

echo "模式: $MODE"
echo "命令: $CMD"
echo "----------------------------------------"

if [ "$BG" = "bg" ]; then
    # 本地盘,避免写 ossfs2(FUSE) 挂载上 tail 会报 EINVAL
    LOG_DIR="${LOG_DIR:-/root/data/logs/db_to_dataset}"
    mkdir -p "$LOG_DIR"
    LOG="$LOG_DIR/$(date +%Y%m%d_%H%M%S)_${MODE}.log"
    nohup $CMD > "$LOG" 2>&1 &
    echo "已后台运行, PID=$!"
    echo "日志: $LOG"
    echo "查看进度: tail -f $LOG"
else
    $CMD
fi
