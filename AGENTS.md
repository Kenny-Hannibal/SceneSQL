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
│   │   │   ├── api/          # Route modules (bag, video, agent)
│   │   │   │   └── agent.py         # Path auto-detection, batch list, query endpoints
│   │   │   ├── models/       # Pydantic schemas
│   │   │   └── services/     # Business logic (bag_parser, video_extractor)
│   │   └── requirements.txt
│   ├── frontend/             # React SPA
│   │   ├── src/
│   │   │   ├── App.js        # Main UI shell
│   │   │   └── components/   # Reusable components
│   │   │       └── AgentPanel.jsx   # NL2SQL panel with video modals
│   │   └── package.json
│   ├── run_backend.sh        # Backend launcher (handles env vars)
│   ├── run_visualizer.sh     # Legacy Gradio launcher
│   └── deploy.sh             # One-click build frontend + start backend
├── agent/                    # NL2SQL Agent 核心服务
│   └── backend/
│       ├── app/
│       │   ├── core/         # Schema reader, LLM client, Tag router
│       │   └── services/
│       │       ├── agent_engine.py   # SQLite batch / Parquet / Single DB query engine
│       │       └── etl/              # ETL manifest & Parquet converter
│       │           ├── etl_manifest.py      # EtlManifestManager + path migration
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
| `SQLITE_DB_PATH` | `/mnt/gacrnd-oss/...` | `.env` |
| `ETL_BASE_PATH` | `/mnt/gacrnd-oss/.../parquet` | `.env` |
| `ETL_BATCH_ID` | — | `.env` |
| `QUERY_MODE` | `sqlite` | `.env` (sqlite / parquet) |

**Path Resolution Priority (Parquet mode):**
When resolving batch paths via `batch_id` (frontend dropdown selection), the system uses this priority:
1. `SQLITE_DB_PATH/parquet` — if `SQLITE_DB_PATH` is set
2. `ETL_BASE_PATH` — fallback if `SQLITE_DB_PATH` is not set

**Recommendation**: Set only `SQLITE_DB_PATH` to your local data root (e.g. `/root/data/text2sql/test_data`), and place parquet batches under `test_data/parquet/`. Remove or comment out `ETL_BASE_PATH` in `.env` to avoid accidentally pointing to an unreachable OSS path.
| `OSS_MOUNT_MAP` | — | `.env` |
| `ROSBAG_MOUNT_BASE` | — | `.env` |
| `DM_ACCESS_TOKEN` | — | `.env` |
| `AGENT_MAIN_MODEL` | `gpt-4o` | `.env` |
| `OPENAI_API_KEY` | — | `.env` |
| `OPENAI_BASE_URL` | — | `.env` |

**`.env` 文件位置**：项目根目录 `/root/data/text2sql/.env`（已加入 `.gitignore`）。
`pydantic_settings` 在 `config.py` 中通过 `env_file = ".env"` 加载，uvicorn 必须从项目根目录启动才能正确定位。

**⚠️ 重要：`pydantic_settings` 不会自动将读取到的变量写入 `os.environ`**。
ETL 相关模块（如 `etl_manifest.py`）直接读取 `os.environ.get("ETL_BASE_PATH")`，因此启动脚本必须通过 `source .env` 或代码显式注入，否则 `EtlManifestManager` 会回退到 `/tmp/etl` 导致 "批次不存在" 错误。

---

## 7. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Root message |
| GET | `/health` | Health check |
| GET | `/api/agent/batches` | 列出所有可用的 ETL batch（SQLite/Parquet 状态） |
| POST | `/api/agent/query` | Agent NL2SQL 查询（body: `question`, `batch_id`, `query_mode`, `db_path` 等） |
| POST | `/api/agent/query-stream` | Agent NL2SQL SSE 流式查询 |
| POST | `/api/agent/generate-sql` | 仅生成 SQL 不执行（返回 SQL + 路由信息，供用户审查） |
| POST | `/api/agent/execute-sql` | 直接执行用户提供的 SQL（不经 LLM） |
| GET | `/api/agent/resolve-bag-path?bag_id=xxx` | 根据 bag_id 解析 bag 本地路径 |
| POST | `/api/bag/info?bag_path={path}` | Load bag metadata + camera topics + start/end timestamps |
| POST | `/api/video/extract` | Start background video extraction (body: `bag_path`, `topic`, optional `start_ts`/`end_ts` in nanoseconds) |
| GET | `/api/video/status/{task_id}` | Poll extraction progress |
| GET | `/api/video/file/{task_id}` | Download completed MP4 |

**Agent Path Auto-Detection (`_resolve_db_path`):**
When `db_path` is provided manually (frontend text input), the backend auto-detects the path type:
| Input pattern | Detected `query_mode` | Behavior |
|---------------|----------------------|----------|
| File ending with `.db` | `sqlite` | Single SQLite DB query |
| Directory containing `.db` files | `sqlite` | Batch SQLite folder query |
| Directory containing `manifest.yaml` or `.parquet` | `parquet` | Parquet aggregation query; `batch_id` inferred from folder name |
| User explicitly sets `query_mode` | User's choice | Overrides auto-detection |

**P2 (batch_id selection) path semantics:**
- `db_path` is always the **base_dir** (parquet parent directory), never `base_dir/batch_id`.
- `batch_id` is passed separately.
- `_get_engine` injects `db_path` into `ETL_BASE_PATH` and passes `batch_id` to `EtlManifestManager`.

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
5. **Time-range clamping** — If `start_ts` < bag start or `end_ts` > bag end, automatically clamp to bag's actual time range (read from `metadata.yaml`). Prevents "No frames found" when SQL result time range exceeds bag range. Both frontend and backend perform this clamp (dual safeguard).

If you change the extraction pipeline, keep the framerate fix and do not reintroduce temp-file I/O unless profiling shows a benefit.

### 8.4 SQL Editor & LLM Behavior Modes
Frontend `AgentPanel.jsx` supports two LLM behavior modes:
- **⚡ Auto-execute**: LLM generates SQL and immediately executes the query (default, original behavior).
- **✏️ Preview only**: LLM generates SQL and fills it into the SQL editor, letting the user review/modify before manually executing.

The SQL editor (`<textarea>`) is a shared component for both modes:
- In auto-execute mode, the generated SQL is displayed in the editor after execution (read-only reference).
- In preview mode, the generated SQL is placed in the editor for editing before execution.
- Users can also manually type SQL and click "▶ 执行 SQL" at any time, bypassing LLM entirely.

### 8.5 Parquet Mode bag_id/bag_path Injection
In Parquet query mode, `agent_engine.py._query_parquet()` injects `bag_id`, `bag_path`, and `db_file` into each result row, ensuring parity with SQLite mode. The `bag_path` is resolved via `RosbagPathResolver` (dm_sdk). If the resolver is unavailable, `bag_path` falls back to empty string and the frontend offers manual path input.

### 8.6 Parquet Path Migration (`_resolve_parquet_path`)
`etl_manifest.py` records absolute parquet paths in `manifest.yaml` (often OSS paths). When data is copied to a new location, `EtlManifestManager._resolve_parquet_path()`:
1. Tries the original path first.
2. If unavailable (or `OSError` from a dead FUSE mount), falls back to `current_base_dir/batch_id/filename.parquet`.
3. This allows moving parquet batches between machines or from OSS to local disk without regenerating `manifest.yaml`.

### 8.7 Batch List (`/api/agent/batches`)
Returns an array of batch objects:
```json
[
  {
    "batch_id": "20260515_T68_1131_5bb5ec_1.5w",
    "sqlite_count": 0,
    "has_parquet": true,
    "bag_count": 12898
  }
]
```
- `sqlite_count`: number of `.db` files in `sqlite_dbs/{batch_id}/`
- `bag_count`: read from `parquet/{batch_id}/manifest.yaml` (`bag_count` field)
- Frontend displays `sqlite_count` in SQLite mode and `bag_count` in Parquet mode.

### 8.8 Global Exception Handling
The generic 500 handler (`exceptions.py`) now returns structured debug info:
```json
{
  "detail": "Internal server error",
  "error_type": "OSError",
  "error_message": "[Errno 107] Transport endpoint is not connected",
  "traceback": ["Traceback (most recent call last):", "  File ...", "  ..."]
}
```

### 8.9 Visualization Modal Interaction
Frontend `AgentPanel.jsx` uses a two-modal flow for video playback:
1. **Topic Selection Modal**: After clicking "播包可视化", a modal opens showing:
   - Bag path and time range
   - Dropdown list of camera topics (fetched from `/api/bag/info`)
   - "确认提取" / "取消" buttons
2. **Video Player Modal**: When extraction completes (`status === 'completed' && video_url`), a fullscreen modal auto-opens with:
   - `<video autoPlay controls>`
   - Bag ID + topic name in the header
   - "✕ 关闭" button to dismiss

The old `window.prompt()` flow for topic selection has been removed.

### 8.6 Frontend API_BASE
- `frontend/src/App.js` uses `process.env.REACT_APP_API_BASE || ''`.
- Empty string means relative URLs, which works with both proxy (dev) and backend static serving (prod).

---

## 9. Common Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `No module named 'j6'` | Proto path wrong | Check `_PROTO_BASE` points to `/root/data/data_mining/...` |
| `libgacbag_storage.so: cannot open` | Missing `LD_LIBRARY_PATH` | Run via `./run_backend.sh` or `./visualizer/deploy.sh` |
| `libpython3.10.so.1.0: cannot open` | Missing `LD_LIBRARY_PATH` (Python lib) | Same as above |
| `ValueError: 批次 xxx 不存在，请先执行 ETL` | `ETL_BASE_PATH` 未正确注入 `os.environ` | 检查 `deploy.sh` 是否 `source .env`；或确认 `config.py` 已添加 `ETL_BASE_PATH` 字段 |
| P2 选择 batch 后 parquet 查询报 "批次不存在" | `.env` 中 `ETL_BASE_PATH` 指向了不可用的 OSS 路径 | 注释掉 `ETL_BASE_PATH`，只保留 `SQLITE_DB_PATH` 指向本地根目录，代码会自动推断 `SQLITE_DB_PATH/parquet` |
| Parquet 查询报 `Catalog Error: Table with name range_tag does not exist` | 该 parquet batch 的 `manifest.yaml` 中确实没有这个表 | 检查 `manifest.yaml` 的 `tables` 字段；ETL 阶段可能只转存了部分表 |
| `Transport endpoint is not connected` in `list_batches()` | OSS FUSE mount died | `list_batches()` now catches `OSError` and skips the dead directory; restart the FUSE mount from the host side |
| Video extraction modal doesn't open | `bag_path` is empty and `RosbagPathResolver` failed | Frontend falls back to manual path input; ensure `bag_id` is present in SQL result rows |
| `ModuleNotFoundError: app` | Missing `__init__.py` | Ensure every Python package dir has one |
| `No module named 'pydantic_settings'` | Dependency missing | `uv pip install pydantic-settings` |
| `ffmpeg failed` | ffmpeg not installed or HEVC stream corrupt | Check `ffmpeg` on PATH; inspect log stderr from `video_extractor.py` |
| Video plays too fast (e.g. 1182 frames in 47s) | Missing input framerate `-r 10` for raw HEVC | Ensure ffmpeg command sets `-r 10` before `-i -` |
| `No frames found` during video extraction | `start_ts`/`end_ts` outside bag's actual time range | Frontend/backend auto-clamp should handle this; if still occurring, check `metadata.yaml` `start_time` format (dict vs number) |
| Parquet query result missing `bag_id`/`bag_path` | `_query_parquet` injection depends on SQL including `bag_id` column | LLM must generate SQL with `SELECT bag_id`; otherwise injection cannot work |
| `bag_path` empty in visualization | `RosbagPathResolver` failed (dm_sdk unavailable or OSS not mounted) | Frontend offers manual bag_path input as fallback |

---

## 10. Agent Modification Guidelines

0. **Skill Discovery Rule — 最高优先级**: 在处理任何用户请求之前，先检查 `.agents/skills/` 目录下是否存在匹配的 skill。读取每个 `SKILL.md` 的 YAML frontmatter `description`，如果用户请求命中描述中的触发条件，必须**优先加载该 skill 的 body 并按其工作流执行**，禁止自己从零重新设计流程或编写重复脚本。

1. **Port policy**: Keep the backend on **30001** to match the legacy Gradio service. Do not change without explicit user approval.
2. **Path policy**: `gsbag` SDK and proto paths are absolute and shared with sibling `data_mining/` repo. Do not assume they live under `text2sql/`.
3. **Environment policy**: Never launch backend with bare `python` or system Python. Always use `.venv/bin/python` with `run_backend.sh` or replicate its env exports.
4. **Adding new endpoints**: Place routes in `visualizer/backend/app/api/`, schemas in `visualizer/backend/app/models/`, business logic in `visualizer/backend/app/services/`.
5. **Agent changes**: Agent 逻辑位于 `agent/backend/app/services/`（`agent_engine.py`, `etl/` 等）。注意 `sys.path` 注入在 `visualizer/backend/app/api/agent.py` 中完成。
6. **Frontend changes**: Update `visualizer/frontend/src/App.js` or add components under `visualizer/frontend/src/components/`. Run `npm run build` before testing static serving.
7. **One-click deploy**: Prefer `./visualizer/deploy.sh` for full-stack deployment (builds frontend + starts backend). **必须从项目根目录启动**，确保 `.env` 被正确加载。
8. **Before committing changes**: Verify `./run_backend.sh` or `./visualizer/deploy.sh` starts without import errors and `curl http://localhost:30001/health` returns `{"status":"ok"}`.
9. **ETL 环境变量陷阱**：修改 `config.py` 新增字段后，若 ETL 模块仍读 `os.environ`，需在 `_get_engine` 或启动脚本中显式注入。

---

## 11. Future Improvements (Pending User Decision)

- Replace in-memory `_tasks` with **Celery + Redis** for durable video jobs.
- Add **Docker / docker-compose** for reproducible deployment.
- Frontend: multi-camera sync playback (original Gradio feature).
- Add API authentication if exposed externally.
