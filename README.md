# SceneSQL — 自动驾驶场景挖掘 NL2SQL Agent

> 通过自然语言查询 ROS Bag 标签数据库，自动生成 SQL、执行查询、可视化播放。

## 项目简介

SceneSQL 是一个面向自动驾驶场景挖掘的业务级 Text2SQL 系统。用户用自然语言描述场景需求（如"查变道切入的 bag"），系统自动路由到对应的查询 Recipe、生成 SQL、批量执行查询，并在前端可视化播放对应的 rosbag 片段。

### 核心能力

| 能力 | 说明 |
|------|------|
| **NL2SQL 多层路由** | 5-Phase 概念路由（关键词 → 复合概念 → 用户策略 → BGE-M3 向量语义 → n-gram 模糊匹配） |
| **Recipe 直通** | 85/92 个查询场景是预定义的 raw_sql Recipe 直通，不依赖 LLM 生成 SQL |
| **Block 流水线** | SQL CTE Block 组装引擎，支持参数化模板和变体 |
| **批量查询** | 一次性查询 15000+ 个 SQLite DB，多线程并行 |
| **视频可视化** | HEVC/H.264 流式播放、多摄像头宫格、3D BEV 鸟瞰图、Fusion Map |
| **VLM 评测闭环** | 查询结果 → 视频片段提取 → Mage-VL 评测 → 标注反馈 |
| **策略管理** | 用户自定义 SQL 策略 CRUD，DataMining 产线同步 |

## 目录结构

```
SceneSQL/
├── agent/                          # NL2SQL Agent 引擎
│   └── backend/app/
│       ├── core/                   # 核心路由逻辑
│       │   ├── concept_router.py   # Round 1 概念识别器
│       │   ├── tag_router.py       # 关键词路由 + 标签语义注入
│       │   ├── block_assembler.py  # SQL Block 流水线组装引擎
│       │   ├── vector_router.py    # BGE-M3 向量语义路由
│       │   ├── llm_client.py       # LLM 客户端（OpenAI 兼容）
│       │   ├── schema_reader.py    # Schema 读取与格式化
│       │   ├── blocks/             # SQL CTE Block 定义（YAML）
│       │   ├── recipes/            # 查询 Recipe 定义（YAML）
│       │   └── user_strategies/    # 用户自定义策略（YAML）
│       └── services/
│           └── agent_engine.py     # Agent 主引擎（批量查询 & bag_id 反查）
│
├── visualizer/                     # 可视化前端 + 后端 API
│   ├── backend/app/
│   │   ├── api/                    # FastAPI 路由
│   │   │   ├── agent.py            # NL2SQL Agent API
│   │   │   ├── video.py            # 视频提取（H.264/HEVC）
│   │   │   ├── bag.py              # Bag 信息解析
│   │   │   ├── strategies.py       # 策略 CRUD
│   │   │   ├── eval_labels.py      # 评测标注
│   │   │   ├── mage_vl.py          # Mage-VL VLM 评测
│   │   │   └── fusion_map.py       # Fusion Map BEV 视图
│   │   ├── core/                   # 配置、认证、日志
│   │   ├── models/                 # Pydantic 数据模型
│   │   └── services/               # 业务逻辑
│   │       ├── bag_parser.py       # Bag 解析（gsbag SDK）
│   │       ├── video_extractor.py  # 视频提取服务
│   │       ├── frame_extractor.py  # 帧提取服务
│   │       ├── stream_worker.py    # HEVC 流式播放 worker
│   │       ├── multi_stream_worker.py  # 多路流式播放 worker
│   │       ├── fusion_map_parser.py    # Fusion Map 解析
│   │       └── datamining.py       # DataMining 产线同步
│   ├── frontend/                   # React 前端
│   │   └── src/components/
│   │       ├── AgentPanel.jsx      # NL 输入 + SSE 流式结果
│   │       ├── VideoPlayer.jsx     # 视频播放器
│   │       ├── BagLoader.jsx       # Bag 加载器
│   │       ├── BevViewer.jsx       # 3D BEV 鸟瞰图查看器
│   │       ├── MultiVideoGrid.jsx  # 多摄像头宫格播放器
│   │       └── SqlEditor.jsx       # SQL 编辑器（CodeMirror 6）
│   └── deploy.sh                   # 一键部署脚本
│
├── sql/                            # SQL 查询文件
│   ├── left_turn_conflict.sql
│   ├── unprotected_left_turn.sql
│   └── unprotected_left_turn_v11~v13.sql
│
├── scripts/                        # 运维脚本
│   ├── docker-build.sh             # Docker 镜像构建
│   ├── _restart_backend_v2.sh      # 后端重启脚本
│   ├── sync-dev.sh                 # 开发环境同步
│   ├── extract_bag_images.py       # Bag 图像提取
│   ├── extract_images.sh           # 图像提取脚本
│   ├── extract_images_standalone.py
│   ├── extract_recipes.py          # Recipe 提取
│   ├── fix_db_schema.py            # DB Schema 修复
│   ├── sqlite2parquet.sh           # SQLite → Parquet 转换
│   └── test_llm_temporal_sql.py    # LLM 时序 SQL 测试
│
├── tools/                          # 共享工具
│   ├── benchmark_bin_reader.py     # Benchmark bin 读取
│   ├── camera_config.py            # 摄像头配置
│   ├── image_handler.py            # 图像处理
│   ├── rosbag_image_visualizer.py  # Rosbag 图像可视化
│   ├── rosbag_path_resolver.py     # Rosbag 路径解析
│   └── ubm_debug_viewer.py         # UBM 调试查看器
│
├── tests/                          # 测试
│   ├── run_batch_test.py
│   ├── test_two_round_loop.py
│   └── test_two_round_results.json
│
├── test_reports/                   # 测试报告（gitignored）
├── changelog/                      # 变更日志条目
├── docs/                           # 项目文档
│   ├── ARCHITECTURE.md             # V1 架构设计
│   ├── ARCHITECTURE_DISCUSSION.md  # 架构讨论记录
│   ├── ARCHITECTURE_V3_FEATURES.md # V3 功能规划
│   ├── ARCHITECTURE_V4_AGENT_LOOP.md  # V4 Agent Loop 架构（当前）
│   ├── DESIGN_bge_m3_routing.md    # BGE-M3 语义路由设计
│   ├── PLAN_v2.0.md                # V2.0 规划
│   ├── SCHEMA_REFERENCE.md         # Schema 参考文档
│   ├── SCHEMA_REFERENCE_V2.md      # Schema 参考文档 V2
│   ├── API_CONTRACT_V1.md          # API 契约 V1
│   ├── NL2SQL_OPTIMIZATION_PLAN_V1.md  # NL2SQL 优化方案 V1
│   └── scene_tag_sql_dev_guide.md  # 场景标签 SQL 开发指南
│
├── vl_validation/                  # VL 验证集（runtime outputs gitignored）
├── v11_recall/                     # V11 召回率分析
├── Dockerfile                      # 容器化镜像定义
├── docker-compose.yml              # 容器编排配置
├── requirements.txt                # Python 依赖
├── requirements-docker.txt         # Docker 镜像依赖
├── .env.example                    # 环境变量模板
├── CHANGELOG.md                    # 变更日志（索引模式）
├── AGENTS.md                       # Agent 工作指令
└── README.md                       # 本文件
```

## 架构概览

### Agent Loop 数据流

```
用户 NL 输入
    │
    ▼
┌─────────────────────────────────────────────┐
│ Round 1: 概念识别 (ConceptRouter)            │
│   Phase 1: CONCEPT_RECIPE_MAP keyword命中    │
│   Phase 2: Compound concept分解              │
│   Phase 3: 用户策略覆盖                       │
│   Phase 4a: BGE-M3向量语义搜索               │
│   Phase 4: n-gram模糊匹配兜底                │
│   → 输出: {concepts, composition, recipe?}   │
└──────────────┬──────────────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
  recipe命中?      无recipe
       │               │
       ▼               ▼
  Layer1/2:        Layer3: Hybrid
  BlockAssembler   (已知block+LLM胶水CTE)
       │               │
       └───────┬───────┘
               ▼ 无SQL
        Fallback: Round2 LLM生成
               │
               ▼
        ┌──────────────┐
        │ 纠错循环       │
        │ dry-run验证    │
        │ → 失败→correction│
        │ → 重试(最多3轮) │
        └──────┬───────┘
               │ 通过
               ▼
        ┌──────────────┐
        │ 执行层         │
        │ SQLite批量查询  │
        │ bag_id注入      │
        │ 结果聚合+分页    │
        └──────────────┘
```

### 核心设计理念

**弱模型 + 强约束 + 多层路由**：85/92 个 recipe 是 raw_sql 直通（查表搬运），不是 AI 生成 SQL。LLM 只负责少数 Fallback 场景。系统设计确保：即使 LLM 完全不可用，85%+ 的查询仍能返回结果。

### 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | FastAPI + Uvicorn + Pydantic |
| 前端 | React 19 + CodeMirror 6 + Three.js |
| 数据库 | SQLite（主库）+ Parquet/DuckDB（ETL 产物）|
| LLM | OpenAI 兼容 API（vLLM / 云端），支持多模型 fallback |
| 向量路由 | BGE-M3 (1024维) + LanceDB |
| 视频处理 | ffmpeg + gsbag SDK + PyAV |
| 容器化 | Docker multi-stage build |

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 20+ (npm)
- ffmpeg
- (可选) gsbag SDK — 用于 bag 解析

### 1. 克隆仓库

```bash
git clone git@github.com:Kenny-Hannibal/SceneSQL.git
cd SceneSQL
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，配置数据库路径、LLM API 等
```

关键环境变量：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `SQLITE_DB_PATH` | SQLite DB 目录路径 | — |
| `OPENAI_API_KEY` | LLM API Key | — |
| `OPENAI_BASE_URL` | LLM API 地址 | `http://localhost:30000/v1` |
| `AGENT_MAIN_MODEL` | 主 LLM 模型名 | `qwen3.5` |
| `PORT` | 后端服务端口 | `30001` |
| `AUTH_USERNAME` | 登录用户名 | — |
| `AUTH_PASSWORD` | 登录密码 | — |
| `GSBAG_SDK` | gsbag SDK 路径 | `three_party/gsbag_x86_...` |

### 3. 安装依赖

```bash
# 后端
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 前端
cd visualizer/frontend
npm install --legacy-peer-deps
```

### 4. 启动服务

```bash
# 一键部署（构建前端 + 启动后端）
bash visualizer/deploy.sh

# 或手动启动后端
python -m uvicorn visualizer.backend.app.main:app --host 0.0.0.0 --port 30001

# 前端开发模式
cd visualizer/frontend
npm start
```

### 5. 访问

- **可视化 UI**: http://localhost:30001
- **API 文档**: http://localhost:30001/docs
- **健康检查**: http://localhost:30001/health

## Docker 部署

```bash
# 构建镜像
bash scripts/docker-build.sh
# 或手动
docker build --network=host -t scenesql:latest .

# 运行
docker run -d -p 30001:30001 \
  -e OPENAI_API_KEY=... \
  -e OPENAI_BASE_URL=... \
  -e AGENT_MAIN_MODEL=... \
  -e AUTH_USERNAME=admin \
  -e AUTH_PASSWORD=... \
  -v <宿主机DB目录>:/data:ro \
  -v <宿主机rosbag目录>:/mnt:ro \
  scenesql:latest
```

## API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 登录获取 JWT Token |
| POST | `/api/agent/query` | NL 查询（同步） |
| POST | `/api/agent/query-stream` | NL 查询（SSE 流式） |
| POST | `/api/agent/execute-sql` | 直接执行 SQL |
| POST | `/api/bag/info` | 获取 Bag 信息 |
| POST | `/api/video/extract` | 提取视频片段（MP4） |
| GET | `/api/strategies` | 列出所有策略 |
| POST | `/api/eval-labels` | 添加评测标注 |
| POST | `/api/mage-vl/evaluate` | Mage-VL VLM 评测 |
| GET | `/api/bag/fusion-map-info` | Fusion Map BEV 信息 |

详细 API 契约见 [docs/API_CONTRACT_V1.md](docs/API_CONTRACT_V1.md)。

## 开发指南

### 项目约定

- **后端**: Python，遵循 PEP 8，已有代码用单引号字符串则保持单引号
- **前端**: React 函数组件，使用单引号字符串，缩进 2 空格
- **注释**: 关键逻辑必须加中文注释，特别是 HEVC/MSE 相关代码
- **错误处理**: 前端 MSE 错误必须输出 `[HEVC诊断]` 日志；后端 ffmpeg 错误必须打印 stderr

### 修改代码前

1. 阅读 [CHANGELOG.md](CHANGELOG.md) 了解近期改动
2. 阅读 [AGENTS.md](AGENTS.md) 了解工作流约束
3. 只做用户要求的事，不要过度设计
4. 修改后必须做语法验证：`python -m py_compile`（后端）

### 测试

```bash
# 运行测试
python -m pytest tests/

# 批量测试
python tests/run_batch_test.py
```

### 部署

```bash
# 部署到大写 DSW（8.130.209.216:1025）
ssh DSW "cd /root/data/text2sql && git pull --ff-only && bash visualizer/deploy.sh -f"

# 注意：小写 dsw（8.130.175.37:1021）已废弃
```

## 相关文档

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | V1 架构设计文档 |
| [ARCHITECTURE_V4_AGENT_LOOP.md](docs/ARCHITECTURE_V4_AGENT_LOOP.md) | V4 Agent Loop 架构（当前生产版本） |
| [SCHEMA_REFERENCE.md](docs/SCHEMA_REFERENCE.md) | Schema 参考文档 |
| [API_CONTRACT_V1.md](docs/API_CONTRACT_V1.md) | API 契约 V1 |
| [CHANGELOG.md](CHANGELOG.md) | 变更日志 |
| [AGENTS.md](AGENTS.md) | Agent 工作指令 |

## License

Proprietary — Internal use only.
