#!/bin/bash
# Rosbag 图片可视化工具启动脚本
# 支持多视角视频预览、视频导出

set -e

# ============================================
# 1. 设置项目路径和 Python 解释器
# ============================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"

# 获取 uv 安装 Python 的 lib 目录（包含 libpython3.10.so）
PYTHON_LIBDIR=$($PYTHON -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")

# ============================================
# 2. 设置 gsbag SDK 环境变量（使用项目内新 SDK）
# ============================================
export GSBAG_SDK="${SCRIPT_DIR}/three_party/gsbag_x86_Release_4.2.18_20260227_Linux"
export LD_LIBRARY_PATH=${GSBAG_SDK}/lib:${PYTHON_LIBDIR}:${LD_LIBRARY_PATH}

export HOBOT_COM_SDK=${GSBAG_SDK}/external/platform_sdk
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH}:${HOBOT_COM_SDK}/lib/gacrnd:${HOBOT_COM_SDK}/lib/third_party

echo "=========================================="
echo "  Rosbag Image Visualizer"
echo "=========================================="
echo "PYTHON:          $PYTHON"
echo "GSBAG_SDK:       $GSBAG_SDK"
echo "LD_LIBRARY_PATH: $LD_LIBRARY_PATH"
echo ""

# ============================================
# 3. 验证 gsbag 是否能正常导入
# ============================================
$PYTHON -c "from gsbag import gsbag_reader; print('gsbag 导入成功')" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "[ERROR] gsbag 导入失败，请检查 SDK 路径是否正确"
    exit 1
fi

# ============================================
# 4. 启动可视化服务
# ============================================
cd "$SCRIPT_DIR"

echo "正在启动 Gradio 服务..."
echo "服务地址: http://0.0.0.0:30001"
echo "本地访问: http://localhost:30001"
echo ""

$PYTHON tools/rosbag_image_visualizer.py
