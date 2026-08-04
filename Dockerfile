# syntax=docker/dockerfile:1
# SceneSQL 容器化镜像 — NL2SQL API + 前端
#
# 能力范围：
# - 完整：NL2SQL（generate-sql / query / query-stream / execute-sql）、
#         策略 CRUD、前端 UI、Parquet(DuckDB)/SQLite 查询
# - 降级：rosbag 视频解析/播放（镜像不含 gsbag C++ SDK，bag_parser 自动降级）
# - 可选：向量路由（chromadb/BGE-M3 懒加载，缺省时回退关键词路由）
#
# 构建：docker build --network=host -t scenesql:<tag> .（容器默认网络无外网）
# 运行：docker run -p 30001:30001 \
#          -e OPENAI_API_KEY=... -e OPENAI_BASE_URL=... -e AGENT_MAIN_MODEL=... \
#          -e AUTH_USERNAME=admin -e AUTH_PASSWORD=... \
#          -e SQLITE_DB_PATH=/data/sqlite_dbs -v <宿主机DB目录>:/data:ro \
#          scenesql:<tag>

# ── Stage 1: 前端构建 ──
# 用 slim(glibc) 而非 alpine：npm 在 alpine/musl 上偶发 "Exit handler never called" 崩溃
FROM node:20-slim AS frontend
WORKDIR /build/frontend
COPY visualizer/frontend/package.json visualizer/frontend/package-lock.json ./
# 与宿主机一致使用 npmmirror 源（npmjs.org 在本机网络下不稳定）
RUN npm config set registry https://registry.npmmirror.com/
# npm ci 偶发 "Exit handler never called" 崩溃（npm bug），且可能以 0 码假成功；
# 先忽略退出码跑一遍，再校验 react-scripts，残缺则补跑 npm install，最后强校验
RUN (npm ci --legacy-peer-deps || true) && \
    (test -x node_modules/.bin/react-scripts || npm install --legacy-peer-deps) && \
    test -x node_modules/.bin/react-scripts
COPY visualizer/frontend/ ./
ENV CI=false
RUN npm run build

# ── Stage 2: 后端运行时 ──
FROM python:3.10-slim AS runtime

# ffmpeg：视频转码 fallback 用；curl：健康检查
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 依赖层独立缓存（阿里云源：构建走 --network=host 时无代理环境变量，直连 pypi 不稳）
COPY requirements-docker.txt ./
RUN pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ -r requirements-docker.txt

# 应用代码（WORKDIR=/app 即 PROJECT_ROOT，main.py 的静态文件与
# agent.backend.* 导入均以此为基准）
COPY agent ./agent
COPY visualizer/backend ./visualizer/backend
COPY tools ./tools
COPY --from=frontend /build/frontend/build ./visualizer/frontend/build

ENV PYTHONUNBUFFERED=1 \
    PORT=30001 \
    ENV=production

EXPOSE 30001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:30001/health || exit 1

CMD ["python", "-m", "uvicorn", "visualizer.backend.app.main:app", "--host", "0.0.0.0", "--port", "30001"]
