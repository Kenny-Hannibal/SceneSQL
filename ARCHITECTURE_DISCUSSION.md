# Architecture Discussion & Design Decisions

> 本文档记录于 2026-05-16/17，汇总了项目从 Gradio 单体迁移到 FastAPI + React 过程中的关键设计讨论与决策。供后续开发者/AI Agent 参考。

---

## 1. 项目完整场景

### 1.1 数据层
- 本地挂载 OSS 路径，内部存放：
  - **SQLite 数据库**：记录 bag 元数据、标签、事件等
  - **原始 rosbag**：HEVC 编码的多路相机数据

### 1.2 AI Agent 流程
```
用户自然语言
    ↓
[AI Agent] → 生成 SQL
    ↓
[SQL Executor] → 查询本地 SQLite
    ↓
返回列表：[{bag_id, start_ts, end_ts}, ...]
    ↓
用户点击某行的"可视化"按钮
    ↓
[Video Extractor] → 按时间片段从 rosbag 抽帧 → ffmpeg → MP4
    ↓
[播放器] → 展示该片段视频
```

### 1.3 前端交互形态
- **左栏**：AI 对话面板（自然语言输入、对话历史、生成的 SQL 展示）
- **中栏**：SQL 结果表格（bag_id | start_ts | end_ts | [可视化按钮]）
- **右栏**：视频播放器（点击可视化后加载，支持时间范围播放）

---

## 2. 架构决策记录（ADR）

### 2.1 前端框架：保持 React，不切 Vue

**决策**：继续使用 React 19 + CRA（未来可平滑迁移到 Vite）。

**理由**：
- 当前前端仅 300 行有效代码，迁移到 Vue 的 ROI 极低
- 未来功能（聊天面板、表格、视频播放器）React 完全可胜任
- 真正的痛点不在前端框架，而在后端业务逻辑

**替代方案**：如构建速度慢，可将 CRA 换成 Vite + React，保留全部组件代码。

---

### 2.2 后端框架：保持 FastAPI，单体部署

**决策**：继续使用 FastAPI，逻辑模块化但物理单体部署。

**理由**：
- FastAPI + Python 生态对 AI/数据场景最友好
- 当前项目规模、团队规模、QPS 均不需要微服务
- 网络 I/O（gsbag 解码、ffmpeg 转码）已是主要瓶颈，框架切换无收益

---

### 2.3 AI Agent 部署方式：逻辑独立，物理单体

**决策**：Agent 代码在独立模块（`agent_engine/`）中实现，但通过函数调用与主服务交互，**不拆分为独立 HTTP 服务**。

**理由**：
- Agent 强依赖数据库 Schema，物理拆分会导致 Schema 同步问题
- 当前没有多系统复用 Agent 的需求
- 函数调用无网络开销，单日志流便于调试

**Provider 模式设计**：
```
backend/app/services/agent_engine/
├── provider.py          # 抽象接口 AgentProvider
├── openai_provider.py   # GPT-4 / OpenAI 实现
├── claude_provider.py   # Anthropic Claude 实现
├── local_provider.py    # 本地模型（vLLM / Ollama）实现
└── mock_provider.py     # 测试用
```

**切换方式**：通过配置 `AGENT_PROVIDER=openai` 一键切换，无需改业务代码。

**未来拆分条件**（满足任意一条即可拆为独立服务）：
1. Agent 需要被 3 个以上业务系统复用
2. Agent 团队与可视化团队的发布节奏差异巨大
3. Agent 需要独立的成本监控、限流、熔断策略
4. 存在多 Agent 协作需求（如一个生成 SQL，另一个校验结果）

---

### 2.4 端口策略：保持 30001

**决策**：后端服务继续监听 `30001`，与 legacy Gradio 服务保持一致。

**理由**：
- 用户内部文档、脚本、浏览器书签均围绕 30001
- 前端 dev server 用 3000（React 默认），通过 proxy 转发到 30001
- 生产部署时前端 build 产物由后端统一 serve

---

## 3. 已完成的技术改造

### 3.1 后端工业化重构
| 改造项 | 说明 |
|--------|------|
| 配置管理 | `pydantic-settings` 集中管理，支持环境变量覆盖 |
| 结构化日志 | `logging` 模块统一配置，减少第三方库噪音 |
| 全局异常处理 | 自定义 `AppException` + FastAPI 异常中间件 |
| 健康检查 | `/health` 端点 |
| 静态文件服务 | `/` 根路径返回 `frontend/build/index.html` |
| 启动脚本 | `run_backend.sh` 自动处理 gsbag SDK 环境变量 |
| 一键部署 | `deploy.sh` = build 前端 + 启动后端 + 健康检查 |

### 3.2 视频提取优化
| 优化项 | 旧代码 | 新代码 |
|--------|--------|--------|
| Bag 读取范围 | 遍历全部 10万+ 消息 | `set_topic_filter([topic])` 只读目标 topic |
| 读取次数 | 隐式多遍 | 单遍读取，内存缓冲 HEVC payload |
| 中间文件 | 先写 `.265` 临时文件 | Pipe 直传 ffmpeg `stdin`，零临时文件 |
| 帧率修复 | 仅输出端 `-r 10` | 输入端 + 输出端双 `-r 10` |
| 时间范围 | 不支持 | `start_ts` / `end_ts`（纳秒）可选参数 |

### 3.3 前端重写
- `App.js` 集成 Bag 加载、Camera Topic 选择、视频提取与播放
- `package.json` 配置 `proxy: http://localhost:30001`

---

## 4. 待办事项与演进路线

### 第一阶段（2 周内）：Text2SQL + 可视化闭环跑通
- [ ] 后端：`/api/agent/generate-sql` 接口
- [ ] 后端：`agent_engine` Provider 模块（至少实现 OpenAI）
- [ ] 后端：`/api/query/execute` 接口 + SQL 沙箱（只读、超时）
- [ ] 前端：三栏布局（聊天 / 表格 / 播放器）
- [ ] 前端：点击"可视化"按钮调用视频提取 API（传入 start_ts/end_ts）

### 第二阶段（1 个月内）：产品化加固
- [ ] **Celery + Redis** 替换 `BackgroundTasks`
  - 视频提取任务持久化
  - 支持并发提取多个片段
  - 任务进度不丢失
- [ ] **SQL Sandbox**
  - 白名单：仅允许 `SELECT`
  - 超时：单条 SQL 最多 10 秒
  - 路径校验：bag_path 必须在 OSS 挂载目录白名单内
- [ ] **视频提取结果缓存**
  - 同一片段（bag + topic + start_ts + end_ts）避免重复提取
  - 缓存键 hash 化，存于本地磁盘

### 第三阶段（按需）：深度优化
- [ ] 视频预提取：常用 bag 提前后台转码
- [ ] 前端播放器帧级跳转：配合 start_ts 精确到帧
- [ ] 多路相机同步播放：恢复 legacy Gradio 的 3×3 网格能力

---

## 5. 关键技术约束（不可违反）

### 5.1 gsbag 环境变量
- 必须使用 `.venv/bin/python`（Python 3.10）
- 必须设置 `LD_LIBRARY_PATH` 包含 gsbag SDK lib 目录
- 必须设置 `HOBOT_COM_SDK` 平台 SDK 路径
- **症状**：`ImportError: libgacbag_storage.so` 或 `libpython3.10.so`

### 5.2 Proto 路径
- `j6.image_encode.boleidl_pb2` 路径在 `/root/data/data_mining/...`
- **不是**在 `/root/data/text2sql/data_mining/...`
- 修改路径时同步更新 `bag_parser.py` 和 `video_extractor.py`

### 5.3 ffmpeg 帧率
- 裸 HEVC 流**无时间戳**，ffmpeg 默认按 **25fps** 处理
- 命令必须在**输入前**指定 `-r 10`：
  ```bash
  ffmpeg -y -r 10 -f hevc -i - ... -r 10 output.mp4
  ```

### 5.4 端口冻结
- 后端：**30001**（冻结，未经用户批准不得修改）
- 前端 dev：`3000`（React 默认，可接受）

---

## 6. 常见误区提醒

| 误区 | 正确做法 |
|------|---------|
| "前端切 Vue 能解决性能问题" | 前端不是瓶颈，换框架无收益 |
| "Agent 必须拆成独立微服务" | 当前规模单体足够，逻辑独立即可 |
| "BackgroundTasks 够用了" | 视频提取是长时间任务，必须上 Celery |
| "AI 生成的 SQL 直接执行" | 必须 sandbox（只读、超时、路径校验） |
| "把帧率只写在 ffmpeg 输出端" | 裸 HEVC 输入端也必须写 `-r 10` |

---

## 7. 相关文件索引

| 文件 | 作用 |
|------|------|
| `AGENTS.md` | AI 维护指南（环境、启动、常见错误） |
| `ARCHITECTURE_DISCUSSION.md` | 本文档（设计决策、演进路线） |
| `backend/app/core/config.py` | 全局配置 |
| `backend/app/services/video_extractor.py` | 视频提取核心逻辑 |
| `backend/app/services/agent_engine/` | （待创建）Agent Provider 模块 |
| `deploy.sh` | 一键部署脚本 |
| `run_backend.sh` | 后端开发启动脚本 |

---

*最后更新：2026-05-17*
*下次修改代码前，务必同步更新本文档及 `AGENTS.md`*
