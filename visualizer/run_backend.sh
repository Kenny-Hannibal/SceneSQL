#!/bin/bash
# Backend startup script for Rosbag Visualizer API
# Handles gsbag SDK environment variables automatically

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

# Verify virtualenv exists
if [ ! -f "$PYTHON" ]; then
    echo "[ERROR] Virtual environment not found at ${SCRIPT_DIR}/.venv"
    exit 1
fi

# Setup gsbag SDK environment
export GSBAG_SDK="${PROJECT_ROOT}/three_party/gsbag_x86_Release_4.2.18_20260227_Linux"
export PYTHON_LIBDIR=$($PYTHON -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
export LD_LIBRARY_PATH=${GSBAG_SDK}/lib:${PYTHON_LIBDIR}:${LD_LIBRARY_PATH}
export HOBOT_COM_SDK=${GSBAG_SDK}/external/platform_sdk
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH}:${HOBOT_COM_SDK}/lib/gacrnd:${HOBOT_COM_SDK}/lib/third_party

# Verify gsbag import
echo "Verifying gsbag import..."
$PYTHON -c "from gsbag import gsbag_reader; print('gsbag OK')"

echo "=========================================="
echo "  Starting Rosbag Visualizer API"
echo "=========================================="
echo "PYTHON:          $PYTHON"
echo "GSBAG_SDK:       $GSBAG_SDK"
echo "API Docs:        http://localhost:30001/docs"
echo ""

cd "$PROJECT_ROOT"
exec $PYTHON -m uvicorn visualizer.backend.app.main:app --host 0.0.0.0 --port 30001 --reload
