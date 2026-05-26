#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════
#  extract_images.sh — 从 SQL 查询结果批量提取 Rosbag 图片帧
#
#  用法:
#    # 单条提取
#    bash scripts/extract_images.sh --bag_id 13qCIWDN --start_ts 1773270382 --end_ts 1773270389
#
#    # CSV 批量提取
#    bash scripts/extract_images.sh --csv tasks.csv
#
#    # CSV + 指定输出根目录
#    bash scripts/extract_images.sh --csv tasks.csv --output_dir /data/output
#
#  CSV 格式 (逗号分隔):
#    bag_id,start_ts,end_ts
#    13qCIWDNxsqGSksD56q2si202605,1773270382,1773270389
#    another_id,1773270400,1773270410
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

# ──── 验证 venv ────
if [ ! -f "$PYTHON" ]; then
    echo "[ERROR] Virtualenv 未找到: ${PYTHON}"
    echo "        请确认项目路径: ${PROJECT_ROOT}"
    exit 1
fi

# ──── gsbag SDK 环境变量 ────
export GSBAG_SDK="${PROJECT_ROOT}/three_party/gsbag_x86_Release_4.2.18_20260227_Linux"
export PYTHON_LIBDIR=$($PYTHON -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
export HOBOT_COM_SDK="${GSBAG_SDK}/external/platform_sdk"
export LD_LIBRARY_PATH="${GSBAG_SDK}/lib:${PYTHON_LIBDIR}:${LD_LIBRARY_PATH:-}"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:${HOBOT_COM_SDK}/lib/gacrnd:${HOBOT_COM_SDK}/lib/third_party"

# uv 管理的 Python 可能 libpython3.x.so 不在 sysconfig.LIBDIR 指向的系统路径
# 从 sys.executable 推断实际的 lib 目录
UV_PYTHON_LIB="$(dirname "$(dirname "$(readlink -f "$PYTHON")")")/lib"
if [ -d "$UV_PYTHON_LIB" ] && ls "$UV_PYTHON_LIB"/libpython*.so >/dev/null 2>&1; then
    export LD_LIBRARY_PATH="${UV_PYTHON_LIB}:${LD_LIBRARY_PATH}"
    echo "[INFO] uv Python lib: ${UV_PYTHON_LIB}"
fi

# ──── Proto 路径 ────
PROTO_BASE="${PROJECT_ROOT}/../data_mining/UBM_mining/ubm_data_mining/gsbag_parser/proto/v4.8.3"
if [ -d "$PROTO_BASE" ]; then
    export PROTO_BASE
fi

# ──── 加载 .env ────
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
fi

# ──── 验证 gsbag ────
echo "[CHECK] 验证 gsbag import ..."
if ! $PYTHON -c "from gsbag import gsbag_reader; print('gsbag OK')" 2>/dev/null; then
    echo "[WARN] gsbag import 失败，检查 LD_LIBRARY_PATH 和 GSBAG_SDK"
    echo "       LD_LIBRARY_PATH=$LD_LIBRARY_PATH"
    echo "       GSBAG_SDK=$GSBAG_SDK"
    echo "       UV_PYTHON_LIB=$UV_PYTHON_LIB"
    # 不退出，让 Python 脚本自己报更详细的错误
fi

echo "=========================================="
echo "  Rosbag Image Extractor"
echo "=========================================="
echo "  PYTHON:    $PYTHON"
echo "  GSBAG_SDK: $GSBAG_SDK"
echo "  PROTO:     ${PROTO_BASE:-未找到}"
echo "=========================================="

# ──── 执行 Python 脚本 ────
cd "$PROJECT_ROOT"
exec $PYTHON "${SCRIPT_DIR}/extract_bag_images.py" "$@"
