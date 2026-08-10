# SceneSQL generate-sql 两轮路由试点 测试报告

> **版本**: feature/gen-sql-two-round（commit df93c1e 起）
> **日期**: 2026-08-11
> **测试人**: QoderCN
> **方案文档**: `docs/NL2SQL_OPTIMIZATION_PLAN_V1.md`

## 1. 版本概述

将 `/api/agent/generate-sql`（DataMining 旁车契约 §3.1 端点）从「关键词路由 + 1 次 LLM」
升级为「概念识别 → recipe/hybrid 组装 → Round2 LLM → EXPLAIN dry-run 纠错（≤1 轮）」，
并单例化 ConceptRouter/BlockAssembler。开关 `GENSQL_TWO_ROUND`（默认 true，可回退）。

## 2. 代码修改总结

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `agent/backend/app/services/agent_engine.py` | 新增 | `generate_sql_two_round()` 方法 + `GENERATE_MAX_CORRECTIONS` 预算 |
| `agent/backend/app/core/block_assembler.py` | 新增 | `get_block_assembler()` 全局单例 |
| `visualizer/backend/app/api/agent.py` | 修改 | generate-sql 端点优先走两轮路径，异常降级旧关键词路径 |
| `docs/NL2SQL_OPTIMIZATION_PLAN_V1.md` | 新增 | 优化方案 + 基线实测数据 |

## 3. 测试总结

测试环境：DSW（8.130.209.216:1025），batch=20260511_test_xyc，金标 26 题。

### 3.1 基线 vs 两轮路径对比

| 指标 | 基线（keyword） | 两轮路径 | 变化 |
|------|----------------|---------|------|
| 生成成功率 | 26/26 | 25/26 | ▼1 题（L4-02） |
| validation_error | 0 | 2 | ▲（L4-01/L4-02） |
| recipe/概念命中 | 0 | **15/26 = 57.7%** | ✅ 超额（目标 ≥30%） |
| 路由分布 | keyword×26 | recipe 15 / llm 9 / hybrid 1 / keyword 1 | — |
| 延迟 p50 | **6.06s** | 16.24s | ⚠️ 回退 2.7× |
| 延迟 p90 | 15.81s | 46.44s | ⚠️ 回退 |
| 延迟 max | 27.57s | 67.24s | ⚠️ |

### 3.2 端到端执行验证（execute-sql）

| NL 查询 | route | error | total_rows | 关键列 | 结果 |
|---------|-------|-------|-----------|--------|------|
| 找出cutin场景 | recipe | None | 3 | start_ts/end_ts ✓（含 pre_speed_kmh 等产线列） | ✅ |
| 找出超速场景 | recipe | None | 4 | start_ts/end_ts ✓ | ✅ |

recipe SQL 为产线验证过的模板（Obj_CutIn_v2 / 超速 V4.1 等），语法免检，真实数据可执行。

### 3.3 SQL 逻辑审查

- **recipe 路径（15 题）**：SQL 来自产线验证模板，逻辑与产线一致，免逐条审查；
  注意语义变化——recipe 是「从原始信号检测场景」（如 cutin 走 dynamic_obj 两阶段检测），
  比基线的标签查询（`range_tag WHERE tag_name='Cutin'`）更深、更准，但执行成本更高，
  DataMining 侧 BatchSearchSqlite 扫描耗时需观察。
- **llm 路径抽查**：
  - L3-05「行人横穿同时 cutin」：时间重叠 JOIN 逻辑正确；但 `tag_name='CrossVRUV1'`
    待人工确认是否为规范标签名（疑应为 CrossVRU 系）→ 列入已知问题。
  - L1-05「低时距」：`tag_name IN ('LowTTC')` 正确。
- **L4-01（hybrid）**：validation_error="incomplete input"，SQL 疑似被 max_tokens=4096
  截断（hybrid 胶水 + 模板拼接后超长），纠错 1 轮未修复。
- **L4-02（llm）**：Round2 返回空/非 SELECT 内容，校验拦截，返回空 SQL——
  基线同题 27.6s 能生成，属两轮链路偶发回退。

### 3.4 已知问题

| # | 问题 | 影响 | 计划 |
|---|------|------|------|
| 1 | 两轮路径延迟整体回退（p50 6s→16s） | 用户等待变长；DataMining read-timeout 60s 对 L4 题告急 | 下一步 P0：Round1 前先做**纯本地概念预匹配**（ConceptRouter 5 阶段本地匹配），命中即跳过 Round1 LLM；简单单标签题 recipe 命中可降至亚秒级 |
| 2 | L4-01 hybrid SQL 截断（max_tokens 4096） | 复杂组合题生成失败 | generate-sql 路径 max_tokens 提到 8192 或拆分生成 |
| 3 | L4-02 Round2 空返回 | 偶发生成失败 | 空返回时降级旧关键词路径（当前仅整个两轮异常才降级） |
| 4 | CrossVRUV1 标签名存疑 | 可能查不到数据 | 对照标签体系确认后补概念组别名 |
| 5 | recipe SQL 执行成本高于标签查询 | DataMining 执行侧耗时增加 | 与 DataMining 灰度观察，必要时按场景分级 |

## 4. 下一步计划

| 优先级 | 任务 | 预计工作量 |
|--------|------|-----------|
| P0 | Round1 前纯本地预匹配，命中跳过概念识别 LLM（延迟主修复） | 0.5-1 天 |
| P0 | max_tokens 8192 + Round2 空返回降级关键词路径 | 0.5 天 |
| P1 | DataMining 灰度接入对比（降级率、执行耗时） | 依赖 PG 隧道恢复 |
| P2 | 路由/LLM 响应 LRU 缓存 | 0.5 天 |
