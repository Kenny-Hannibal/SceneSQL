#!/bin/bash
# One-click deployment script for Rosbag Visualizer
# Builds frontend and starts backend (with embedded static serving)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="${PROJECT_ROOT}/.venv/bin/python"

# ============================================
# 0. Preflight checks
# ============================================
if [ ! -f "$PYTHON" ]; then
    echo "[ERROR] Virtual environment not found at ${SCRIPT_DIR}/.venv"
    echo "        Please create it first (Python 3.10 recommended)."
    exit 1
fi

if ! command -v npm &> /dev/null; then
    echo "[ERROR] npm is not installed or not in PATH"
    exit 1
fi

if ! command -v ffmpeg &> /dev/null; then
    echo "[WARNING] ffmpeg not found in PATH. Video extraction will fail."
    echo "          Install it first, e.g.: apt-get install ffmpeg"
fi

# ============================================
# 1. Setup gsbag SDK environment
# ============================================
export GSBAG_SDK="${PROJECT_ROOT}/three_party/gsbag_x86_Release_4.2.18_20260227_Linux"
export PYTHON_LIBDIR=$($PYTHON -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
export LD_LIBRARY_PATH=${GSBAG_SDK}/lib:${PYTHON_LIBDIR}:${LD_LIBRARY_PATH}
export HOBOT_COM_SDK=${GSBAG_SDK}/external/platform_sdk
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH}:${HOBOT_COM_SDK}/lib/gacrnd:${HOBOT_COM_SDK}/lib/third_party

# ============================================
# 2. Install backend dependencies if needed
# ============================================
echo "[INFO] Checking backend dependencies..."
$PYTHON -c "import pydantic_settings, fastapi, uvicorn, yaml" 2>/dev/null || {
    echo "[INFO] Installing backend dependencies..."
    if command -v uv &> /dev/null; then
        uv pip install -r backend/requirements.txt --python "$PYTHON"
    else
        "$PYTHON" -m pip install -r backend/requirements.txt
    fi
}

# ============================================
# 3. Build frontend
# ============================================
echo "[INFO] Building frontend..."
cd "${SCRIPT_DIR}/frontend"
npm run build
cd "$SCRIPT_DIR"

# ============================================
# 4. Stop existing backend if any
# ============================================
EXISTING_PID=$(pgrep -f "uvicorn backend.app.main:app" || true)
if [ -n "$EXISTING_PID" ]; then
    echo "[INFO] Stopping existing backend (PID: $EXISTING_PID)..."
    kill "$EXISTING_PID" 2>/dev/null || true
    sleep 2
fi

# ============================================
# 5. Start backend
# ============================================
echo "[INFO] Starting backend server..."

# 加载 .env 到当前 shell 环境，确保子进程能读取所有环境变量
if [ -f "${PROJECT_ROOT}/.env" ]; then
    set -a
    source "${PROJECT_ROOT}/.env"
    set +a
    echo "[INFO] Loaded .env"
fi

nohup $PYTHON -m uvicorn backend.app.main:app --host 0.0.0.0 --port 30001 > /tmp/rosbag_visualizer.log 2>&1 &
NEW_PID=$!
sleep 3

# ============================================
# 6. Health check
# ============================================
if curl -s http://localhost:30001/health | grep -q '"status":"ok"'; then
    echo ""
    echo "=========================================="
    echo "  ✅ Deployment Successful"
    echo "=========================================="
    echo ""
    echo "  🌐 Visualizer UI:  http://localhost:30001"
    echo "  📚 API Docs:       http://localhost:30001/docs"
    echo "  💓 Health Check:   http://localhost:30001/health"
    echo ""
    echo "  Backend PID:      $NEW_PID"
    echo "  Log file:         /tmp/rosbag_visualizer.log"
    echo ""
    echo "  To stop:          kill $NEW_PID"
    echo "  To view logs:     tail -f /tmp/rosbag_visualizer.log"
    echo "=========================================="
else
    echo ""
    echo "=========================================="
    echo "  ❌ Deployment Failed"
    echo "=========================================="
    echo ""
    echo "  Check logs: tail -n 50 /tmp/rosbag_visualizer.log"
    echo ""
    exit 1
fi
