# AGENTS.md — Rosbag Visualizer

> AI-friendly project guide. If you are an AI agent modifying this codebase, read this first.

---

## 1. Project Overview

This is a **Rosbag multi-camera visualizer** that extracts HEVC-encoded camera frames from `.bag` files and provides video preview/export capabilities.

**Architecture**: FastAPI backend + React frontend (separated).
**Legacy**: The original app was a Gradio monolith at `tools/rosbag_image_visualizer.py` (port 30001). The new architecture replaces it while keeping the same port.

---

## 2. Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Backend | FastAPI + Uvicorn | Python 3.10 via `.venv` |
| Frontend | React 19 + CRA | `create-react-app`, no TypeScript yet |
| Bag SDK | `gsbag` (C++ wrapped) | Requires `LD_LIBRARY_PATH` setup |
| Proto | `j6.image_encode` | Dynamic `sys.path` injection at runtime |
| Video | `ffmpeg` | HEVC -> H.264 MP4 transcoding |

---

## 3. Directory Structure

```
text2sql/
├── visualizer/               # 前后端可视化服务
│   ├── backend/              # FastAPI application
│   │   ├── app/
│   │   │   ├── main.py       # App factory, CORS, static files, routers
│   │   │   ├── core/         # Config, logging, exception handlers
│   │   │   ├── api/          # Route modules (bag, video)
│   │   │   ├── models/       # Pydantic schemas
│   │   │   └── services/     # Business logic (bag_parser, video_extractor)
│   │   └── requirements.txt
│   ├── frontend/             # React SPA
│   │   ├── src/
│   │   │   ├── App.js        # Main UI: bag loader, topic grid, video player
│   │   │   └── components/   # BagLoader.jsx, VideoPlayer.jsx
│   │   └── package.json
│   ├── run_backend.sh        # Backend launcher (handles env vars)
│   ├── run_visualizer.sh     # Legacy Gradio launcher
│   └── deploy.sh             # One-click build frontend + start backend
├── agent/                    # NL2SQL Agent 服务（待建设）
│   └── backend/              # FastAPI / gRPC Agent service
├── tools/                    # 共享工具库
│   ├── rosbag_path_resolver.py  # dm_sdk 封装：bag_id → OSS path → local path
│   ├── rosbag_image_visualizer.py
│   ├── image_handler.py
│   └── camera_config.py
├── three_party/              # gsbag SDK binaries
│   └── gsbag_x86_Release_4.2.18_20260227_Linux/
├── .env                      # 项目环境变量（数据库路径、OSS 映射、LLM Key）
├── AGENTS.md                 # This file
├── ARCHITECTURE.md           # 架构设计文档
└── ARCHITECTURE_DISCUSSION.md # 架构评审记录
```

---

## 4. Environment & Critical Setup

### 4.1 Python Virtualenv
- Location: `.venv/`
- Python: 3.10 (managed by `uv`)
- **Do NOT use system Python** (`/usr/local/bin/python`). Always use `.venv/bin/python`.

### 4.2 gsbag SDK Environment Variables
The `gsbag` native extension fails to load without these. The `run_backend.sh` script sets them automatically:

```bash
export GSBAG_SDK="${PWD}/three_party/gsbag_x86_Release_4.2.18_20260227_Linux"
export PYTHON_LIBDIR=$(python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
export LD_LIBRARY_PATH=${GSBAG_SDK}/lib:${PYTHON_LIBDIR}:${LD_LIBRARY_PATH}
export HOBOT_COM_SDK=${GSBAG_SDK}/external/platform_sdk
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH}:${HOBOT_COM_SDK}/lib/gacrnd:${HOBOT_COM_SDK}/lib/third_party
```

**Symptom of misconfiguration:**
```
ImportError: libgacbag_storage.so: cannot open shared object file
ImportError: libpython3.10.so.1.0: cannot open shared object file
```

### 4.3 Proto Path Injection
`j6.image_encode.boleidl_pb2` is loaded via dynamic `sys.path` in:
- `backend/app/services/bag_parser.py`
- `backend/app/services/video_extractor.py`

The base path is **NOT** under `text2sql/`. It lives at:
```
/root/data/data_mining/UBM_mining/ubm_data_mining/gsbag_parser/proto/v4.8.3
```

If you move proto files, update `_PROTO_BASE` in both service files.

---

## 5. How to Start

### Backend only
```bash
cd /root/data/text2sql
./visualizer/run_backend.sh
```
- URL: `http://localhost:30001`
- API Docs: `http://localhost:30001/docs`
- Health: `http://localhost:30001/health`

### Frontend dev mode
```bash
cd /root/data/text2sql/visualizer/frontend
npm start
```
- URL: `http://localhost:3000`
- Proxy to backend is configured in `package.json` (`"proxy": "http://localhost:30001"`).

### Frontend production build
```bash
cd /root/data/text2sql/visualizer/frontend
npm run build
```
After building, the backend will auto-serve `frontend/build/static/` and `frontend/build/assets/`.

---

## 6. Configuration

All backend config lives in `backend/app/core/config.py` (Pydantic Settings).

| Variable | Default | Override via |
|----------|---------|--------------|
| `PROJECT_NAME` | Rosbag Visualizer API | env |
| `VERSION` | 1.0.0 | env |
| `ENV` | development | `ENV` env |
| `PORT` | 30001 | `PORT` env |
| `HOST` | 0.0.0.0 | env |
| `CORS_ORIGINS` | `["*"]` | env |
| `VIDEO_OUTPUT_DIR` | `/tmp/rosbag_videos` | `VIDEO_OUTPUT_DIR` env |
| `LOG_LEVEL` | INFO | `LOG_LEVEL` env |

No `.env` file is committed; you can create one at `backend/.env` if needed.

---

## 7. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Root message |
| GET | `/health` | Health check |
| POST | `/api/bag/info?bag_path={path}` | Load bag metadata + camera topics |
| POST | `/api/video/extract` | Start background video extraction (body: `bag_path`, `topic`, optional `start_ts`/`end_ts` in nanoseconds) |
| GET | `/api/video/status/{task_id}` | Poll extraction progress |
| GET | `/api/video/file/{task_id}` | Download completed MP4 |

---

## 8. Key Implementation Notes

### 8.1 Video Extraction Task Model
- Uses FastAPI `BackgroundTasks` (in-memory registry `_tasks`).
- **Not persistent**: server restart loses tasks.
- For production-scale workloads, migrate to **Celery + Redis**.

### 8.2 ffmpeg Dependency & Framerate
- `ffmpeg` must be on `$PATH`.
- **Critical**: Raw HEVC streams have **no timestamps**. ffmpeg defaults to **25 fps** for raw HEVC input.
- The extraction command **must** specify `-r 10` **before** the input (`-i -`) so ffmpeg treats the raw frames as 10 fps:
  ```bash
  ffmpeg -y -r 10 -f hevc -i - -c:v libx264 -preset fast -crf 23 -pix_fmt yuv420p -r 10 output.mp4
  ```
- The backend pipes HEVC data directly into ffmpeg via `stdin` to avoid temp-file I/O.

### 8.3 Video Extraction Optimizations
`backend/app/services/video_extractor.py` implements these optimizations:
1. **`set_topic_filter`** — `GsBagReader.set_topic_filter([topic])` skips all non-target topics (avoids scanning lidar/CAN/etc).
2. **Single-pass read** — Frames are buffered in memory as HEVC payloads (typically tens to hundreds of MB).
3. **Pipe to ffmpeg** — No temporary `.265` file is written; frames stream directly into ffmpeg `stdin`.
4. **Time-range slicing** — Optional `start_ts`/`end_ts` (nanoseconds) allow extracting only a segment instead of the full topic.

If you change the extraction pipeline, keep the framerate fix and do not reintroduce temp-file I/O unless profiling shows a benefit.

### 8.4 Frontend API_BASE
- `frontend/src/App.js` uses `process.env.REACT_APP_API_BASE || ''`.
- Empty string means relative URLs, which works with both proxy (dev) and backend static serving (prod).

---

## 9. Common Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `No module named 'j6'` | Proto path wrong | Check `_PROTO_BASE` points to `/root/data/data_mining/...` |
| `libgacbag_storage.so: cannot open` | Missing `LD_LIBRARY_PATH` | Run via `./run_backend.sh` |
| `ModuleNotFoundError: app` | Missing `__init__.py` | Ensure every Python package dir has one |
| `No module named 'pydantic_settings'` | Dependency missing | `uv pip install pydantic-settings` |
| `ffmpeg failed` | ffmpeg not installed or HEVC stream corrupt | Check `ffmpeg` on PATH; inspect log stderr from `video_extractor.py` |
| Video plays too fast (e.g. 1182 frames in 47s) | Missing input framerate `-r 10` for raw HEVC | Ensure ffmpeg command sets `-r 10` before `-i -` |

---

## 10. Agent Modification Guidelines

0. **Skill Discovery Rule — 最高优先级**: 在处理任何用户请求之前，先检查 `.agents/skills/` 目录下是否存在匹配的 skill。读取每个 `SKILL.md` 的 YAML frontmatter `description`，如果用户请求命中描述中的触发条件，必须**优先加载该 skill 的 body 并按其工作流执行**，禁止自己从零重新设计流程或编写重复脚本。

1. **Port policy**: Keep the backend on **30001** to match the legacy Gradio service. Do not change without explicit user approval.
2. **Path policy**: `gsbag` SDK and proto paths are absolute and shared with sibling `data_mining/` repo. Do not assume they live under `text2sql/`.
3. **Environment policy**: Never launch backend with bare `python` or system Python. Always use `.venv/bin/python` with `run_backend.sh` or replicate its env exports.
4. **Adding new endpoints**: Place routes in `backend/app/api/`, schemas in `backend/app/models/`, business logic in `backend/app/services/`.
5. **Frontend changes**: Update `frontend/src/App.js` or add components under `frontend/src/components/`. Run `npm run build` before testing static serving.
6. **One-click deploy**: Prefer `./visualizer/deploy.sh` for full-stack deployment (builds frontend + starts backend).
7. **Before committing changes**: Verify `./run_backend.sh` starts without import errors and `curl http://localhost:30001/health` returns `{"status":"ok"}`.

---

## 11. Future Improvements (Pending User Decision)

- Replace in-memory `_tasks` with **Celery + Redis** for durable video jobs.
- Add **Docker / docker-compose** for reproducible deployment.
- Frontend: multi-camera sync playback (original Gradio feature).
- Add API authentication if exposed externally.
