# 文生SQL 链路性能与效果优化方案 V1

> 日期: 2026-08-11
> 范围: SceneSQL 旁车（generate-sql 契约 §3.1）+ DataMining 本地降级链路
> 状态: 方案稿，配套试点分支 `feature/gen-sql-two-round`

## 1. 现状链路

### 1.1 DataMining 单轮主链路（已上线灰度）

```
用户问题
  └─ Text2SqlGraph.generateNode
       ├─ [灰度] scene-sql.enabled && 单轮 && dataVersion 非空
       │    └─ SceneSQL POST /api/agent/generate-sql   ← 本方案优化重点
       │         keyword 路由 → schema 裁剪 → 1 次 LLM (temp=0.1) → 规则校验
       │    ├─ 成功 → 本地 SqlValidator 预检 → 执行 BatchSearchSqlite
       │    └─ 失败/未命中 → 降级 ↓
       └─ [本地] 场景推理(1次fast LLM解析) → 模板组装或再1次LLM生成
            └─ validate → execute → 失败纠错循环(每轮1次LLM, 最多3轮)
```

### 1.2 SceneSQL 侧两条已有路径

| 路径 | 端点 | 路由 | LLM 调用 | 纠错 |
|---|---|---|---|---|
| 轻量路径 | `/api/agent/generate-sql` | 仅 TagRouter 关键词 | 固定 1 次 | 无（仅规则校验） |
| 两轮路径 | `/api/agent/query(-stream)` | ConceptRouter 5 阶段（精确→子串→NL→向量→模糊）+ Recipe 体系 | recipe 命中 0 次，最多 5 次 | Round3 EXPLAIN dry-run ≤3 轮 |

**核心矛盾：DataMining 走的是轻量路径，而 SceneSQL 效果最好的 recipe/概念路由 + EXPLAIN 纠错能力只在 query 路径里，generate-sql 完全没用上。**

## 2. 问题清单（代码定位）

### 效果类

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| E1 | generate-sql 只做关键词路由，recipe/概念体系闲置 | `visualizer/backend/app/api/agent.py:473-511` | 命中率低，全部 SQL 靠 LLM 现场生成，正确率受限于单次生成 |
| E2 | generate-sql 无 EXPLAIN dry-run 纠错 | 同上 | 语法/列名错误直接返回 validation_error，DataMining 只能降级 |
| E3 | DataMining few-shot 全量拼接、与 query 无关；schema 全文注入不裁剪 | `SqlStrategyService.buildFewShotExamples`、`SchemaLoader` | prompt 冗长、干扰生成质量 |

### 性能类

| # | 问题 | 位置 | 影响 |
|---|------|------|------|
| P1 | ConceptRouter()/BlockAssembler() 每次请求新建，重载全部 recipe/blocks YAML；现成单例 `get_concept_router()` 未被使用 | `agent_engine.py:804, 833, 849` | 路由阶段每次数百 ms 级重复 IO/解析 |
| P2 | 向量模型（BGE-M3 2.2GB）懒加载 | `vector_router.py:30-76` | 首次向量检索阻塞数秒至数十秒 |
| P3 | 无路由结果缓存、无 LLM 响应缓存 | 全局 | 相同问题重复花 LLM 时间与 token |
| P4 | DataMining：few-shot 每次查 DB；SQLiteParallelQuery 每请求新建 20 线程池；RestTemplate/OpenAiChatModel 无显式超时 | `BatchSearchSqlite:181`、`BailianApiClient` | 尾部延迟不可控，重复开销 |

## 3. 优化方案（按优先级）

### P0 — 效果：generate-sql 升级为「两轮路由 + EXPLAIN 纠错」（SceneSQL 侧，本分支实现）

改造 `/api/agent/generate-sql` 内部实现，**响应契约不变**（契约 §3.1 字段不增删，`route_method` 仅扩展取值）：

```
question
 ├─ ① ConceptRouter 单例匹配（复用 get_concept_router()）
 │    ├─ recipe 命中 → BlockAssembler 组装 SQL → EXPLAIN dry-run 校验 → 返回（0 次 LLM）
 │    └─ 未命中 ↓
 ├─ ② 关键词 TagRouter（现有逻辑保留）→ schema 裁剪 → 1 次 LLM
 └─ ③ EXPLAIN dry-run 失败 → ≤1 轮 LLM 纠错 → 返回
```

- `route_method` 扩展取值：`recipe` / `concept` / `keyword` / `llm` / `fallback`
- recipe 命中的 SQL 已在产线验证过（开发规范 §4.3），免 LLM，正确率与延迟双赢
- 纠错上限 1 轮（控制延迟），区别于 query 路径的 3 轮
- 新增开关 `GENSQL_TWO_ROUND=true/false`（默认 true，可回退旧逻辑）

预期收益：
- 金标集 recipe/概念命中率从 0 → 目标 ≥30%（命中即免 LLM）
- SQL 可执行率提升（EXPLAIN 纠错兜底）
- 命中路径延迟从秒级 LLM 降到百毫秒级

### P1 — 性能：单例化 + 预热 + 缓存（同分支顺带实现）

1. generate-sql 路径使用 `get_concept_router()` 单例与 BlockAssembler 单例化（模块级 + 锁）
2. 启动预热：`main.py` startup 钩子预载 recipe/blocks 索引（向量模型仍懒加载，避免启动 OOM，可选环境变量开启）
3. LRU 缓存：`(question, batch_id) → RouteResult`（容量 512，TTL 10min）；LLM 响应缓存可选开关，默认关

### P2 — DataMining 侧（单独排期，本分支不动）

1. `buildFewShotExamples` 加进程内缓存（TTL 与 schema 一致）
2. `SQLiteParallelQuery` 线程池复用（静态共享池 + 信号量限流）
3. RestTemplate / OpenAiChatModel 配置显式超时（连接 3s / 读 60s）
4. 远期：few-shot 向量化检索替代全量拼接

## 4. 度量指标与验收

| 指标 | 基线（v2.0 报告 / 本次实测） | 目标 |
|---|---|---|
| generate-sql 可执行率 | 92.3%（26 题） | ≥95% |
| 结果有行数 | 61.5% | ≥70% |
| 逻辑正确率 | ~69% | ≥78% |
| recipe/概念命中率 | 0%（轻量路径不走） | ≥30% |
| generate-sql p50 延迟 | （见基线实测节） | 命中路径 ≤0.5s，LLM 路径不劣化 |
| DataMining 降级率 | （需灰度日志统计） | 下降 |

测试方法（遵循开发流程规范 §2 Step 5）：
- 26 条金标问题（`agent/backend/tests/baseline_queries.json`）逐条跑 `generate-sql` + `execute-sql`
- SQL 逻辑按规范 §4.2 清单逐条审查（表名/时间戳单位/start_ts end_ts/SQLite 兼容性）
- 部署 DSW（大写，`8.130.209.216:1025`，`/root/data/text2sql`）后从本机/DSW 调 API

## 5. 基线实测（2026-08-11，DSW，batch=20260511_test_xyc）

`generate-sql` 端点，26 条金标问题，逐条实测：

| 指标 | 数值 |
|---|---|
| 生成成功率（sql 非空） | 26/26 = 100% |
| 规则校验失败（validation_error） | 0 |
| route_method 分布 | keyword × 26（**recipe/概念命中 0**） |
| 延迟 p50 | **6.06s** |
| 延迟 p90 | **15.81s** |
| 延迟 max | 27.57s（L4-02 国道上先急刹再变道） |

结论：
1. 每条查询固定 1 次 LLM 生成，无一走免 LLM 的 recipe 路径 → P0 的主要动机
2. 复合场景（时序/跨表/数值条件）延迟显著放大（最长 27.6s），DataMining 侧 read-timeout 60s 虽能兜住，但用户体验差
3. 无 EXPLAIN dry-run 兜底，本次恰好 0 条校验失败，但历史上 LLM SQL 语法错误只能靠 DataMining 纠错循环兜底


## 6. 试点分支执行计划

| 步骤 | 内容 |
|---|---|
| 1 | SceneSQL 建分支 `feature/gen-sql-two-round`（本地仓库） |
| 2 | 实现 P0 + P1，py_compile 通过 |
| 3 | git push → DSW `git pull` + `deploy.sh -f` |
| 4 | E2E：26 金标 generate-sql/execute-sql，记录延迟与命中分布 |
| 5 | SQL 逻辑审查 + 对比基线，写测试报告 `test_reports/` |
| 6 | 更新 CHANGELOG，等用户确认后合回 master |

风险与回退：`GENSQL_TWO_ROUND=false` 一键回退旧逻辑；契约字段不变，DataMining 无需改动。
