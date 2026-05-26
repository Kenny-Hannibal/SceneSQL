#!/usr/bin/env bash
set -euo pipefail

# SceneSQL Dev Sync Script
# Usage: ./sync-dev.sh ["commit message"]
# Flow: local edit -> git commit & push -> DSW git pull & restart -> health check

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DSW_HOST="dsw"
DSW_PROJECT_DIR="/root/data/text2sql"
COMMIT_MSG="${1:-update: $(date '+%m-%d %H:%M')}"

cd "$REPO_DIR"

# 1. Pull remote changes first (avoid conflicts)
echo ">>> Pulling latest from remote..."
git pull origin master 2>/dev/null || true

# 2. Check for local changes
if git diff --quiet && git diff --cached --quiet && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    echo ">>> No local changes to commit"
else
    echo ">>> Committing local changes..."
    git add -A
    git commit -m "$COMMIT_MSG"
    echo ">>> Pushing to remote..."
    git push origin master
fi

# 3. Sync DSW
echo ">>> Syncing DSW..."
ssh "$DSW_HOST" "cd ${DSW_PROJECT_DIR} && git fetch origin master && git reset --hard origin/master"

# 4. Restart DSW backend
echo ">>> Restarting DSW backend..."
ssh "$DSW_HOST" "bash ${DSW_PROJECT_DIR}/_restart_backend_v2.sh"

# 5. Health check via local port forward
sleep 2
if curl -s http://localhost:30001/health 2>/dev/null | grep -q '"status":"ok"'; then
    echo ">>> ✅ DSW backend healthy (localhost:30001)"
else
    echo ">>> ⚠️  Health check failed — check DSW logs: ssh dsw 'tail -30 /tmp/rosbag_visualizer.log'"
fi
