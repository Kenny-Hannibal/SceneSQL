#!/bin/bash
# One-click deployment script for Rosbag Visualizer
# Builds frontend and starts backend (with embedded static serving)
#
# Usage:
#   ./deploy.sh          # 正常部署（如果服务已运行则跳过）
#   ./deploy.sh --force  # 强制重启（先 kill 再部署）

set -e

FORCE_RESTART=false
if [ "$1" = "--force" ] || [ "$1" = "-f" ]; then
    FORCE_RESTART=true
fi

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
# .venv/lib contains libgacbag_storage.so.4 etc (gsbag pip package libs)
export LD_LIBRARY_PATH=${PROJECT_ROOT}/.venv/lib:${LD_LIBRARY_PATH}
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
# 4. Check existing backend — don't restart if already running
# ============================================
EXISTING_PID=$(pgrep -f "uvicorn backend.app.main:app" 2>/dev/null || true)
if [ -n "$EXISTING_PID" ]; then
    # 验证进程是否真的存活
    if kill -0 "$EXISTING_PID" 2>/dev/null; then
        if [ "$FORCE_RESTART" = true ]; then
            # --force 模式：直接 kill 现有进程，继续部署
            echo "[INFO] --force flag set. Stopping existing backend (PID $EXISTING_PID)..."
            kill "$EXISTING_PID" 2>/dev/null || true
            sleep 2
            if kill -0 "$EXISTING_PID" 2>/dev/null; then
                echo "[WARNING] Process $EXISTING_PID still alive after SIGTERM, sending SIGKILL..."
                kill -9 "$EXISTING_PID" 2>/dev/null || true
                sleep 1
            fi
            echo "[INFO] Existing backend stopped. Proceeding with redeployment..."
        else
            # 正常模式：检查健康状态，健康则跳过
            if curl -s --max-time 3 http://localhost:30001/health 2>/dev/null | grep -q '"status":"ok"'; then
                echo ""
                echo "=========================================="
                echo "  ⚠️  Backend Already Running"
                echo "=========================================="
                echo ""
                echo "  Backend PID:      $EXISTING_PID"
                echo "  Visualizer UI:    http://localhost:30001"
                echo "  Health Check:     http://localhost:30001/health"
                echo "  Log file:         /tmp/rosbag_visualizer.log"
                echo ""
                echo "  To stop:          kill $EXISTING_PID"
                echo "  To force restart: $0 --force"
                echo ""
                echo "  Skipping deployment — service is already healthy."
                echo "=========================================="
                exit 0
            else
                echo "[WARNING] Process $EXISTING_PID exists but health check failed. Killing and restarting..."
                kill "$EXISTING_PID" 2>/dev/null || true
                sleep 2
                # 确保进程已退出
                if kill -0 "$EXISTING_PID" 2>/dev/null; then
                    echo "[WARNING] Process $EXISTING_PID still alive, sending SIGKILL..."
                    kill -9 "$EXISTING_PID" 2>/dev/null || true
                    sleep 1
                fi
            fi
        fi
    else
        echo "[INFO] Stale PID $EXISTING_PID found (process dead). Cleaning up..."
    fi
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

# protobuf 4.x 对 duplicate json_name 更严格，需要用纯 Python 实现避免 C++ 描述符池冲突
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENT=python

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
