# SceneSQL v3.0 功能规划 — 架构设计文档

> 日期: 2026-07-08
> 状态: 规划评审
> 前置: v2.0 阶段1(Schema接入Prompt)已完成, v2.0 阶段2(基准测试)进行中

---

## 概览

6个功能按依赖关系分三层：

```
Layer 1 (基础能力)     Layer 2 (交互增强)      Layer 3 (智能闭环)
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│ 流式聊天输出  │────→│ 策略保存      │────→│ Loop链路          │
│ 本地向量库    │     │ 视频多视图    │     │ 本地多模态模型     │
└──────────────┘     └──────────────┘     └──────────────────┘
     可独立开发           依赖Layer1            依赖Layer1+2
```

**实施顺序**：流式聊天 → 策略保存 → 视频多视图 → 本地向量库 → Loop链路 → 本地多模态

---

## 1. 流式聊天及输出

### 1.1 现状

| 组件 | 状态 | 问题 |
|------|------|------|
| 后端 `LLMClient.chat_stream()` | ✅ 已实现 | 未被调用 |
| 后端 `/api/agent/query-stream` SSE | ✅ 已实现 | 但 `engine.query()` 内部调用 `llm.chat()` 是一次性返回，SSE 只有阶段事件，没有 token 级流式 |
| 前端 `streamingSql` 状态 | ✅ 已存在 | 只在 `stage: 'sql_generated'` 时一次性赋值，无逐字渲染 |

**用户感知**：点击查询后，"正在理解..."→ 黑盒等待 2-5s → SQL 突然出现。体验差。

### 1.2 方案

```
用户输入 NL
    │
    ▼ SSE 事件流
    ├─ stage: understanding      ← 解析意图（瞬间）
    ├─ stage: token, content: xx  ← LLM 逐 token 输出 SQL（核心改进）
    │  (每 50ms 一个 chunk)
    ├─ stage: sql_generated       ← SQL 完成，触发 dry-run
    ├─ stage: executing           ← 执行查询
    └─ stage: completed           ← 返回结果
```

### 1.3 改动清单

**后端 `agent_engine.py`**：

```python
# 现有（一次性）:
raw_sql = await self.llm.chat(system_prompt, user_prompt, temperature=0.1)

# 改为（流式 + 回调）:
async def query_stream(self, question, ..., on_token=None):
    sql_chunks = []
    async for token in self.llm.chat_stream(system_prompt, user_prompt):
        sql_chunks.append(token)
        if on_token:
            await on_token(token)   # 回调给 SSE generator
    raw_sql = "".join(sql_chunks)
    sql = self._clean_sql(raw_sql)
    # 后续 dry_run / correction 循环不变
```

**后端 `agent.py`**（SSE endpoint）：

```python
async def event_generator():
    yield sse('understanding', '正在理解您的问题...')
    # Round 1: Recipe 匹配（不变）
    # Round 2: LLM 生成 — 改为流式
    async def on_token(token):
        yield sse('token', token)
    result = await engine.query_stream(question, ..., on_token=on_token)
    # 后续 dry-run / 执行 / 返回 不变
```

**前端 `AgentPanel.jsx`**：

```jsx
// 现有：stage === 'sql_generated' 时一次性 setStreamingSql(data.sql)
// 改为：
case 'token':
  setStreamingSql(prev => prev + data.content);  // 逐字追加
  break;
case 'sql_generated':
  setStreamingSql(data.sql);  // 最终完整版覆盖（确保一致性）
  break;
```

### 1.4 工作量

0.5 天。改动仅涉及 3 个函数的调用方式变更，无新增模块。

---

## 2. 本地向量库

### 2.1 现状

| 组件 | 状态 |
|------|------|
| `TagRouter.route()` | 关键词子串匹配（`kw in query`），626 条手写同义词 |
| `DESIGN_bge_m3_routing.md` | ✅ 已有详细设计，计划用 BGE-M3 替代关键词 |
| BGE-M3 模型 | ❌ 未部署到 DSW |

### 2.2 方案：ChromaDB 嵌入式

**为什么选 ChromaDB 而非 BGE-M3 + numpy**：

| | BGE-M3 + numpy | ChromaDB |
|---|---|---|
| 部署 | 需 GPU + PyTorch（DSW 无 GPU） | 纯 Python，CPU 即可 |
| 维护 | 自建索引代码 | 内置持久化、增量更新 |
| 语义模型 | BGE-M3 (568M, 需 GPU) | 内置 all-MiniLM-L6-v2 (80M, CPU) 或外挂 BGE-M3 |
| 查询速度 | <10ms (GPU) | <50ms (CPU, 222条) |

**架构**：

```
离线构建（CI/一次性）           在线路由
┌──────────────────┐         ┌──────────────────┐
│ templates.jsonl   │         │  用户 NL          │
│ schema_dict.yaml  │──→ embed ──→ ChromaDB ──→ top-k tags ──→ hint 给 LLM
│ manual_overrides  │         │  (persist/目录)   │
└──────────────────┘         └──────────────────┘
```

**索引 schema**：

```python
# ChromaDB collection: "tag_index"
# 每条文档:
{
    id: "Cutin",
    document: "Cutin 切入 加塞 挤进来 插队 变道插入 其他车辆切入自车前方",
    metadata: {"type": "range_tag", "table": "range_tag"},
    embedding: [...]  # 384-dim (MiniLM) 或 1024-dim (BGE-M3)
}

# ChromaDB collection: "template_index"
# 每条文档:
{
    id: "t02",
    document: "找出切入事件的片段",
    metadata: {"domain": "scenario", "sql": "SELECT ..."},
    embedding: [...]
}
```

**与现有代码集成**：

```python
# tag_router.py
class TagRouter:
    def __init__(self):
        self.chroma = chromadb.PersistentClient(path="./chroma_db")
        self.tag_coll = self.chroma.get_collection("tag_index")
        self.tmpl_coll = self.chroma.get_collection("template_index")

    def route(self, query: str) -> RouteResult:
        # 1. 向量匹配 tags
        results = self.tag_coll.query(query_texts=[query], n_results=5)
        matched_tags = [id for id, dist in zip(results["ids"][0], results["distances"][0]) if dist < 0.5]

        # 2. 向量匹配 templates
        tmpl_results = self.tmpl_coll.query(query_texts=[query], n_results=3)

        # 3. map 枚举仍走关键词
        map_hits = [e for e in _MAP_ENUM_KEYWORDS if e["kw"] in query]

        return self._build_route_result(matched_tags, map_hits, tmpl_results)
```

### 2.3 两阶段部署

| 阶段 | 模型 | 环境 | 时间 |
|------|------|------|------|
| Phase 1: MVP | ChromaDB 内置 MiniLM (CPU) | DSW | 0.5天 |
| Phase 2: 升级 | 外挂 BGE-M3 (需 GPU 机器) | PPU 或用户本机 | 1天 |

Phase 1 用 MiniLM 先上线，验证向量路由比关键词好多少。Phase 2 按需升级 BGE-M3。

### 2.4 工作量

**Phase 1**: 1.5 天（0.5天构建索引脚本 + 0.5天集成 tag_router + 0.5天 E2E 验证）
**Phase 2**: 额外 1 天

---

## 3. 本地多模态模型

### 3.1 目标

SQL 查询结果的**视觉验证**：抽查视频帧，判断查出来的场景是否正确。

```
SQL 结果 → 取 start_ts 附近的帧 → VLM 判断 "这是路口左转吗？" → 验证通过/失败
```

### 3.2 部署约束

| 位置 | GPU | 可行性 |
|------|-----|--------|
| DSW | ❌ CPU-only | 不可行 |
| 用户本机 (RTX 5090) | ✅ | 可行，但需网络打通 |
| API 调用 (Kimi-K2.5) | N/A | 最快上线，按量计费 |

### 3.3 方案：API 优先 + 本机 VLM 备选

**Phase 1: Kimi-K2.5 Vision API（推荐，1天上线）**

```python
# agent/backend/app/services/vision_verifier.py
class VisionVerifier:
    """调用 Kimi-K2.5 vision API 验证 SQL 结果"""

    async def verify(self, image_base64: str, tag_name: str, question: str) -> dict:
        prompt = f"这是自动驾驶摄像头截图。用户查询了'{tag_name}'场景。这张图是否匹配该场景？请回答 yes/no 及原因。"
        resp = await kimi_vision_client.chat(prompt, image=image_base64)
        return {"match": "yes" in resp.lower(), "reason": resp}
```

调用链：前端点"验证" → 后端抽帧（复用 `video_extractor` 的单帧提取） → base64 编码 → Kimi API → 返回结果。

**Phase 2: 本机 Qwen3-VL (RTX 5090, 可选)**

本机部署 Qwen3-VL-7B，DSW 通过内网 API 调用。需要解决 DSW↔本机网络打通（SSH 反向隧道已有方案）。

### 3.4 工作量

| 阶段 | 内容 | 时间 |
|------|------|------|
| Phase 1 | Kimi API + 单帧抽取 + 前端验证按钮 | 1.5天 |
| Phase 2 | 本机 VLM 部署 + 网络打通 | 2天 |

---

## 4. Loop 链路

### 4.1 目标

NL → SQL → 执行 → 验证 → 修正，形成闭环。当前系统只有**语法级纠错**（dry-run 失败→LLM重写），缺少**语义级验证**。

### 4.2 Loop 分三层

```
Layer A: 语法纠错（已有）
  dry_run 失败 → error_msg → LLM 重写 SQL → 重试
  最多 MAX_CORRECTIONS 轮

Layer B: 规则验证（新增，1天）
  执行成功后，对结果做规则检查：
  - start_ts < end_ts ?
  - duration 在合理范围？(0.5s ~ 300s)
  - 返回行数 > 0 ?
  - tag_name 在已知列表中 ?
  失败 → 生成具体错误描述 → LLM 修正 SQL

Layer C: VLM 验证（依赖多模态模型）
  抽样结果 → 抽帧 → VLM 判断 → 失败 → 生成修正提示 → LLM 修正 SQL
```

### 4.3 Layer B 规则验证器

```python
# agent/backend/app/services/rule_verifier.py

class RuleVerifier:
    RULES = [
        {
            "name": "start_before_end",
            "check": lambda row: row.get("start_ts", 0) < row.get("end_ts", 0),
            "msg": "start_ts >= end_ts, 时间范围无效"
        },
        {
            "name": "duration_reasonable",
            "check": lambda row: 0.5 < (row.get("end_ts", 0) - row.get("start_ts", 0)) < 300,
            "msg": "duration 不在合理范围 [0.5s, 300s]"
        },
        {
            "name": "result_not_empty",
            "check": lambda rows: len(rows) > 0,
            "msg": "查询结果为空，SQL 可能条件过严或标签名错误"
        },
    ]

    def verify(self, rows: list[dict]) -> list[str]:
        """返回所有违规描述，空列表表示全部通过"""
        issues = []
        for rule in self.RULES:
            if rule["name"] == "result_not_empty":
                if not rule["check"](rows):
                    issues.append(rule["msg"])
            else:
                failed = [r for r in rows[:10] if not rule["check"](r)]
                if failed:
                    issues.append(f"{rule['msg']} ({len(failed)}/{min(len(rows),10)} 行违规)")
        return issues
```

### 4.4 Loop 流程集成

```python
# agent_engine.py — query() 尾部追加

# 现有：dry_run 通过后直接执行
# 改为：dry_run 通过 → 执行 → 规则验证 → 失败则修正

result = await self._execute_sql(sql, ...)
issues = self.rule_verifier.verify(result.rows)
if issues and correction_rounds < MAX_CORRECTIONS:
    # 构建修正 prompt：把验证失败信息 + SQL + 结果样本告诉 LLM
    correction_prompt = self._build_rule_correction_prompt(sql, issues, result.rows[:3])
    raw_sql = await self.llm.chat(**correction_prompt)
    sql = self._clean_sql(raw_sql)
    # 重新执行...
```

### 4.5 工作量

| 层 | 内容 | 时间 | 前置 |
|----|------|------|------|
| Layer B | RuleVerifier + 集成到 query() | 1天 | 无 |
| Layer C | VLM 验证 + 修正闭环 | 2天 | 多模态模型就绪 |

---

## 5. 策略保存

### 5.1 现状

| 组件 | 状态 |
|------|------|
| `templates.jsonl` | 78 条手写模板，硬编码在项目仓库 |
| 用户手动改 SQL | ✅ 前端 SQL 编辑器可编辑，但**刷新丢失** |
| Recipe 机制 | `tag_router.py` 里硬编码，用户不可增删 |

### 5.2 方案：用户策略库

```
┌─────────────────────────────────────────────┐
│              策略存储层                       │
│  ┌─────────────┐    ┌──────────────────┐    │
│  │ 系统模板     │    │ 用户策略          │    │
│  │ templates.   │    │ user_strategies/ │    │
│  │ jsonl (只读) │    │ {user_id}.jsonl  │    │
│  └─────────────┘    └──────────────────┘    │
│          ↓ 合并                      ↓       │
│        TagRouter.route() 检索时同时命中两者    │
└─────────────────────────────────────────────┘
```

**存储格式**：每条策略与 `templates.jsonl` 格式一致：

```json
{
  "id": "u_001",
  "domain": "scenario",
  "nl": "找出Y型路口直行",
  "sql": "WITH ... SELECT 'y_junction_straight' AS tag_name ...",
  "author": "user_ou_12d601f6",
  "created_at": "2026-07-08T22:00:00Z",
  "hit_count": 3
}
```

### 5.3 API 设计

```
POST   /api/agent/strategies          # 保存当前 SQL 为策略
GET    /api/agent/strategies          # 列出用户所有策略
DELETE /api/agent/strategies/{id}     # 删除策略
POST   /api/agent/strategies/{id}/hit # 命中计数 +1（自动触发）
```

### 5.4 前端交互

```
SQL 编辑器区域
┌─────────────────────────────────┐
│ SELECT 'y_junction' AS tag_name │  [💾 保存为策略]
│ FROM ...                        │
└─────────────────────────────────┘

策略管理弹窗
┌─────────────────────────────────┐
│ 我的策略 (5)                     │
│ ┌─────────────────────────────┐ │
│ │ Y型路口直行  命中3次  [删除] │ │
│ │ 路口左转    命中7次  [删除] │ │
│ └─────────────────────────────┘ │
└─────────────────────────────────┘
```

### 5.5 与现有 recipe 机制的关系

```
用户输入 NL
    │
    ▼ TagRouter.route()
    1. 先查 user_strategies（向量/关键词匹配）
    2. 再查 templates.jsonl（系统模板）
    3. 合并去重，返回 top-k
    │
    ▼ build_prompt()
    用户策略和系统模板统一作为 few-shot 注入
```

用户策略**优先级高于**系统模板——因为是用户亲手验证过的 SQL。

### 5.6 工作量

1.5 天。后端 CRUD API (0.5天) + 前端保存/管理 UI (0.5天) + 集成路由 (0.5天)

---

## 6. 视频输出多视图

### 6.1 现状

| 组件 | 状态 |
|------|------|
| `video_config.yaml` | 定义多 topic 的 fps 覆盖 |
| 前端 `VideoPlayer` | 只支持单个 topic 播放 |
| `topicModalData.cameraTopics` | ✅ 前端已有 camera topic 列表展示 |
| 浏览器并发限制 | Chrome 同域 6 个 HTTP 连接（SSE/stream 各占1个） |

### 6.2 方案：Tab 切换（推荐） + 可选同屏双画面

**方案 A: Tab 切换（默认，2天）**

```
┌─────────────────────────────────────────┐
│  [前视] [左前] [右前] [后视] [左后] [右后] │  ← Tab 栏
│─────────────────────────────────────────│
│                                         │
│          当前选中 topic 的视频播放         │
│                                         │
└─────────────────────────────────────────┘
```

切换逻辑：
1. 断开当前 stream（abort controller）
2. 等 500ms（让浏览器释放 TCP 连接 + 后端 kill ffmpeg）
3. 以新 topic 参数请求 stream

**方案 B: 同屏双画面（可选扩展，+1天）**

```
┌──────────────────┬──────────────────┐
│   前视 (主)       │   后视 (辅)       │
│   /cam_front/hevc │   /cam_rear/hevc  │
└──────────────────┴──────────────────┘
```

限制：最多 2 路同屏（受浏览器 6 并发连接限制：1 SSE查询 + 1 bag-info + 2 video stream = 4，留 2 个给其他请求）。3 路以上需域名分片或 HTTP/2。

### 6.3 前端改动

```jsx
// AgentPanel.jsx — 新增状态
const [activeCamera, setActiveCamera] = useState(null);  // 当前播放的 camera topic

// 打开视频弹窗时，传入 topic 列表 + 默认选中
const openVideoPlayer = (row, mode) => {
  const cameras = topicModalData.cameraTopics;
  setActiveCamera(cameras[0]);  // 默认前视
  setPlayerModalOpen(true);
};

// VideoPlayer.jsx — 接收 topic 列表，Tab 切换
<VideoPlayer
  bagPath={row.bag_path}
  topic={activeCamera}
  startTs={row.start_ts}
  endTs={row.end_ts}
  mode={mode}
  availableTopics={topicModalData.cameraTopics}
  onTopicChange={setActiveCamera}
/>
```

### 6.4 工作量

| 方案 | 时间 |
|------|------|
| A: Tab 切换 | 1.5天（前端 Tab UI 0.5天 + 切换逻辑 0.5天 + 测试 0.5天） |
| B: 同屏双画面 | 额外 1天 |

---

## 实施路线图

```
Week 1
├── Day 1-2: 流式聊天输出 (0.5d) + 策略保存 (1.5d)
│
Week 2
├── Day 3-4: 视频多视图 Tab 版 (1.5d) + 本地向量库 Phase 1 (0.5d)
├── Day 5:   本地向量库 Phase 1 续 (1d) + Loop Layer B 规则验证 (0.5d)
│
Week 3
├── Day 6-7: Loop Layer B 续 (0.5d) + 本地多模态 Phase 1 Kimi API (1.5d)
├── Day 8:   Loop Layer C VLM 验证闭环 (2d, 可选)
│
Optional
├── 本地向量库 Phase 2 (BGE-M3 升级): +1d
├── 视频同屏双画面: +1d
├── 本机 VLM 部署: +2d
```

**总计核心**: 8天 | **含可选扩展**: 12天

---

## 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| ChromaDB MiniLM 中文能力弱 | 中 | 路由准确率不及 BGE-M3 | Phase 1 先测，不行就提前切 BGE-M3 |
| Kimi API 调用延迟高 (>3s) | 低 | 验证体验差 | 异步验证，前端不阻塞 |
| 浏览器并发连接限制 | 高 | 多视图>2路时卡顿 | Tab 切换方案天然规避 |
| 用户策略与系统模板冲突 | 低 | 相同 NL 匹配到两个不同 SQL | 用户策略优先，覆盖系统模板 |
| Loop Layer C 假阳性（VLM 判断错） | 中 | 正确 SQL 被误修正 | 保留人工确认环节，Loop 不自动提交修正 |
