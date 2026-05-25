#!/bin/bash
cd /root/data/text2sql
source .venv/bin/activate

export GSBAG_SDK="${PWD}/three_party/gsbag_x86_Release_4.2.18_20260227_Linux"
export PYTHON_LIBDIR=$(python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
export LD_LIBRARY_PATH=${GSBAG_SDK}/lib:${PYTHON_LIBDIR}:${LD_LIBRARY_PATH}
export HOBOT_COM_SDK=${GSBAG_SDK}/external/platform_sdk
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH}:${HOBOT_COM_SDK}/lib/gacrnd:${HOBOT_COM_SDK}/lib/third_party
set -a && source .env && set +a

# 杀掉旧进程
for pid in $(ps aux | grep -E "uvicorn.*backend.app.main:app" | grep -v grep | awk '{print $2}'); do
    kill "$pid" 2>/dev/null
done
sleep 2

find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -delete 2>/dev/null

cd visualizer
nohup python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 30001 > /tmp/rosbag_visualizer.log 2>&1 &
NEW_PID=$!
sleep 4

curl -s http://localhost:30001/health | grep -q '"status":"ok"' && echo "OK PID=$NEW_PID" || echo "FAIL"
