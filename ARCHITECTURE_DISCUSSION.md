# Architecture Discussion & Design Decisions

> 本文档记录于 2026-05-16/17，汇总了项目从 Gradio 单体迁移到 FastAPI + React 过程中的关键设计讨论与决策。
> 2026-05-20 更新：新增 NL2SQL Agent 优化方案与 P0-P4 排期。

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
[Tag Router] → 关键词路由 → 匹配标签 + 涉及表
    ↓
[Schema 分层注入] → Layer 0 常驻 Card + Layer 1 按需表结构 + Layer 2 标签语义 + few-shot
    ↓
[LLM] → 生成 SQL
    ↓
[SQL Validator] → 安全校验 + 字段存在性检查
    ↓
[SQL Executor] → 查询 SQLite / Parquet (DuckDB)
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

### 2.5 NL2SQL 路由策略：关键词 + RAG，不用 LLM 路由

**决策**：使用关键词匹配作为主路由，RAG 语义检索作为兜底，不使用 LLM 做路由判断。

**理由**：
- 弱模型（7B-14B Qwen/GLM）路由准确率低，本身就需要路由才能生成 SQL，形成循环依赖
- LLM 路由增加 200-500ms 延迟和 1 次 API 调用成本
- 关键词路由对 7 表 + 60 标签的规模足够，命中率 > 80%
- RAG 作为兜底处理关键词无法覆盖的模糊表达

**替代方案**：LLM 路由（P3 阶段可视准确率数据决定是否引入强模型做轻量路由）。

---

### 2.6 Schema 注入策略：三层四类，替代全量注入

**决策**：Schema 按"三层四类"体系分层注入，替代当前全量 Schema 灌入。

| 层 | 内容 | Token 量 | 何时注入 |
|----|------|----------|----------|
| Layer 0 | Schema Card（表概览 + 表分类） | ~200 | 始终 |
| Layer 1 | 命中表的完整字段定义 | ~200-500/表 | 路由后按需 |
| Layer 2 | 标签语义描述 + few-shot 模板 | ~100-300 | 路由后按需 |

**四类表分类**：
| 类别 | 表 | 特征 |
|------|-----|------|
| horizontal_event | range_tag, intersection_info | 事件/片段型，秒级时间 |
| vertical_timeseries | ego, dynamic_obj, static_obj | 时序型，纳秒级时间，10Hz |
| dynamic_ref | dynamic_lane, dynamic_link | 动态拓扑，实时变化 |
| static_ref | static_lane, static_link | 静态参考，不随时间变化 |

**理由**：
- 当前全量 Schema ~3000+ token，弱模型上下文利用率极低
- 精准注入后 ~800-1500 token，信息密度更高
- 三层结构让 LLM 先看全局再聚焦，符合"渐进式理解"的认知规律

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

### 3.4 NL2SQL Agent P0 改造（2026-05-20）
| 改造项 | 旧代码 | 新代码 |
|--------|--------|--------|
| Schema 注入 | 全量 `format_schema_for_prompt()` | `only_tables` 参数按需注入 |
| 路由 | 无 | `TagRouter` 关键词路由 → `RouteResult` |
| Prompt 组装 | 固定 `SYSTEM_PROMPT` + 全量 schema | `build_prompt()` 分层组装 |
| 标签语义 | `schema_dictionary.yaml` 未参与 prompt | `format_tag_semantics()` Layer 2 注入 |
| 跨表 JOIN 提示 | 仅在规则中文字描述 | `format_cross_table_join_hint()` 精准规则 |
| Few-shot | 无 | 内置 10 条 + 外部 `templates.jsonl` 20 条 |
| 验证 | 仅检查 SELECT/FROM + 禁词 | 增加 `known_tables` 字段存在性校验 |

---

## 4. NL2SQL Agent 优化方案

### 4.1 核心问题诊断

| 问题 | 根因 | 影响 |
|------|------|------|
| 全量 Schema 灌入 | `format_schema_for_prompt()` 无过滤 | 3000+ token，弱模型上下文噪声大 |
| 标签字典未入 prompt | `schema_dictionary.yaml` 仅被 `schema_reader` 读取字段描述 | LLM 不知道"变道"→ LaneChange 的映射 |
| 无路由 | 每次查询都带全部表 | LLM 可能选错表、混淆时间单位 |
| 验证薄弱 | `_validate_sql()` 仅 10 行 | 字段不存在、表名拼错等低级错误无法拦截 |
| 无 few-shot | 纯 zero-shot 生成 | 弱模型缺乏格式参照，SQL 结构不规范 |

### 4.2 优化路线图（P0-P4）

#### P0：规则基座（已完成 ✓ 2026-05-20）

**目标**：关键词路由 + Schema 分层注入，零 LLM 成本，解决 80% 确定性 case。

| 组件 | 文件 | 状态 |
|------|------|------|
| TagRouter | `agent/backend/app/core/tag_router.py` | ✅ 新建 |
| Schema 按需过滤 | `agent/backend/app/core/schema_reader.py` | ✅ 改造 `only_tables` 参数 |
| Prompt 分层组装 | `agent/backend/app/core/tag_router.py` → `build_prompt()` | ✅ 新建 |
| Agent Engine 集成 | `agent/backend/app/services/agent_engine.py` | ✅ 改造 |
| Few-shot 模板库 | `agent/backend/app/core/templates.jsonl` | ✅ 新建 |

**关键设计**：
- 关键词索引从 `schema_dictionary.yaml` 自动构建 + 手工同义词补充
- 路由完全无 LLM 调用，延迟 < 1ms
- Fallback 机制：关键词未命中 → 默认 range_tag 表
- Layer 0 Schema Card 始终注入（~200 token），Layer 1/2 按路由结果按需注入

**预期效果**：
- Prompt token 从 ~3000 降至 ~800-1500
- 标签命中率：关键词匹配覆盖 80%+ 常见查询
- SQL 生成准确率：从 ~30% 提升到 ~50-60%（主要靠 few-shot 和标签精准注入）

---

#### P1：Few-shot 模板库扩充（预计 1 周）

**目标**：手工构建 20-30 条高质量 SQL 模板，覆盖 7 个表 + 常见跨表 JOIN。

| 任务 | 交付物 | 预估工时 |
|------|--------|----------|
| 标注 20-30 条真实 NL→SQL 对 | `templates.jsonl` 扩充 | 4h |
| 覆盖 7 表 + 5 种 JOIN 模式 | 模板分类标注 | 2h |
| 验证所有模板在 DuckDB 上可执行 | 测试脚本 | 2h |

**JOIN 模式清单**：
1. range_tag × ego（秒→纳秒时间桥接）
2. range_tag × dynamic_obj（秒→纳秒时间桥接）
3. ego × dynamic_obj（纳秒对齐）
4. range_tag × intersection_info（时间 + intersection_id）
5. ego × dynamic_lane（时间对齐 + lane_id）

---

#### P2：RAG 语义检索（预计 2 周）

**目标**：关键词路由的兜底 + 语义模糊查询的支持。

| 组件 | 说明 | 预估工时 |
|------|------|----------|
| 三索引构建 | 标签索引、表索引、SQL 模板索引，各自独立 embed | 4h |
| Embedding 模型 | 选用 bge-large-zh-v1.5 或 m3e-base（中文优化） | 2h |
| Bi-Encoder 检索 | 向量库（FAISS/ChromaDB），余弦相似度 top-k | 6h |
| HyDE 查询改写 | 用 LLM 生成假设性 SQL → embed → 检索模板 | 4h |
| 路由融合 | 关键词 + RAG 双路召回 → 去重合并 | 4h |
| 评估 | 50 条测试集 + 准确率/召回率统计 | 4h |

**三索引设计**：
```
标签索引:  tag_name + description + sub_tags → embed → 768d vector
表索引:    table_name + description + key_columns → embed → 768d vector
模板索引:  nl + sql + domain → embed → 768d vector
```

**检索流程**：
```
NL query
  ├─ 关键词路由 → matched_tags + involved_tables
  ├─ RAG 检索 → top-k 标签 + top-k 模板
  └─ 融合去重 → 最终 route_result
```

---

#### P3：Cross-Encoder 重排序 + 查询改写（预计 1.5 周）

**目标**：RAG 召回结果精准排序 + 复杂 NL 分解。

| 组件 | 说明 | 预估工时 |
|------|------|----------|
| Cross-Encoder | bge-reranker-v2-m3 对 top-k 结果重排序 | 4h |
| 查询改写 | 多意图分解："变道时有什么目标且速度>60" → 两个子查询 | 6h |
| 双向检索 | 正向(NL→SQL) + 反向(SQL→NL) 双路召回 | 4h |
| 强模型 fallback | 路由失败 → 调用强模型(Kimi/GLM-4) 生成 | 2h |

---

#### P4：数据飞轮（持续迭代）

**目标**：自动入库 + 负样本反馈 + 定期去重，形成自学习闭环。

| 组件 | 说明 | 预估工时 |
|------|------|----------|
| 自动入库 | 执行成功的 SQL→NL 对自动加入模板库 | 4h |
| 负样本反馈 | 执行失败的 SQL → 标注原因 → 更新路由/模板 | 6h |
| 去重机制 | 相似度 > 0.95 的模板合并 | 3h |
| 定期评估 | 每周自动跑 50 条测试集，输出准确率报告 | 4h |
| 标签枚举同步 | `schema_structure.yaml` → `tag_router` 索引自动更新 | 2h |

---

### 4.3 预期效果时间线

| 阶段 | 准确率 | 主要提升来源 |
|------|--------|-------------|
| 当前 | ~30% | 基线 |
| P0 后 | ~50-60% | Schema 精准注入 + few-shot + 标签语义 |
| P1 后 | ~65-70% | 丰富 few-shot 覆盖 |
| P2 后 | ~75-80% | RAG 语义检索兜底 |
| P3 后 | ~85%+ | 重排序 + 查询改写 + 强模型 fallback |
| P4 后 | 持续提升 | 数据飞轮自学习 |

---

### 4.4 工业化方案调研参考

| 方案 | 核心思想 | 本项目借鉴点 |
|------|---------|-------------|
| C3SQL | Candidate pruning: 先生成候选 Schema 再剪枝 | Schema 分层注入本质就是预剪枝 |
| DIN-SQL | Decomposition: 复杂查询分解为子问题 | P3 查询改写参考此思路 |
| CHESS | Cross-Encoder 重排序 Schema-linking | P3 重排序直接采用 |
| Vanna | RAG + DDL/SQL training data | P2 三索引设计参考 |
| FK Graph Pruning | 确定性图剪枝（外键关系） | 7 表规模小，关键词已足够，暂不需要图 |

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

### 5.5 时间单位（NL2SQL 专项）
- `range_tag.start_ts / end_ts`：**秒**（BIGINT）
- `ego.ts / dynamic_obj.ts`：**纳秒**（BIGINT）
- 跨表 JOIN 条件：`ego.ts BETWEEN range_tag.start_ts * 1e9 AND range_tag.end_ts * 1e9`
- `ts_ms`（毫秒）仅存在于 ego/dynamic_obj，可用于辅助桥接

### 5.6 range_tag.param JSON 字段
- 子标签提取：`json_extract(param, '$.sub_tag')`
- 目标类型提取：`json_extract(param, '$.object_type')`
- 常见 key：sub_tag, object_id, state_ratio, frame_count, duration, en

---

## 6. 常见误区提醒

| 误区 | 正确做法 |
|------|---------|
| "前端切 Vue 能解决性能问题" | 前端不是瓶颈，换框架无收益 |
| "Agent 必须拆成独立微服务" | 当前规模单体足够，逻辑独立即可 |
| "BackgroundTasks 够用了" | 视频提取是长时间任务，必须上 Celery |
| "AI 生成的 SQL 直接执行" | 必须 sandbox（只读、超时、路径校验） |
| "把帧率只写在 ffmpeg 输出端" | 裸 HEVC 输入端也必须写 `-r 10` |
| "全量 Schema 让 LLM 自己选表" | 弱模型上下文利用率低，必须精准注入 |
| "LLM 路由更智能" | 弱模型路由准确率低，关键词 + RAG 更可靠 |
| "RAG 能替代关键词路由" | 关键词路由零成本零延迟，RAG 是兜底不是替代 |

---

## 7. 相关文件索引

| 文件 | 作用 |
|------|------|
| `AGENTS.md` | AI 维护指南（环境、启动、常见错误） |
| `ARCHITECTURE_DISCUSSION.md` | 本文档（设计决策、演进路线） |
| `ARCHITECTURE.md` | 详细架构设计文档 |
| `backend/app/core/config.py` | 全局配置 |
| `backend/app/services/video_extractor.py` | 视频提取核心逻辑 |
| `backend/app/services/agent_engine.py` | NL2SQL 核心引擎（P0 已改造） |
| `backend/app/core/tag_router.py` | 关键词路由器（P0 新建） |
| `backend/app/core/schema_reader.py` | Schema 读取（P0 已改造，支持 only_tables） |
| `backend/app/core/templates.jsonl` | Few-shot SQL 模板库（P0 新建） |
| `backend/app/core/schema_dictionary.yaml` | 标签字典（路由索引的数据源） |
| `backend/app/core/schema_structure.yaml` | 数据库表结构定义 |
| `deploy.sh` | 一键部署脚本 |
| `run_backend.sh` | 后端开发启动脚本 |

---

*最后更新：2026-05-20*
*下次修改代码前，务必同步更新本文档及 `AGENTS.md`*
