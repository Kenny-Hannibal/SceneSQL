# NL2SQL 路由过拟合修复：recipe 命中改参考 SQL 模式 + user_strategies 目录 bug

> 日期：2026-08-19
> 范围：`agent/backend/app/services/agent_engine.py`、`agent/backend/app/core/concept_router.py`、`agent/backend/app/core/user_strategy.py`

## 问题

提问「查询高速道路上自车cut in的场景」时，系统直接返回 `ego_cut_in` 产线 recipe 的原样 SQL，
**丢失「高速道路」约束**——路由过拟合仓库里的产线代码。

## 根因（三层叠加）

1. **关键词短路**：`concept_router.parse_round1_output` Phase 1/2 命中「自车切入」→ `ego_cut_in`，
   同句的「高速」概念被静默忽略
2. **LLM 被跳过**：`agent_engine` recipe 分支 raw_sql fast path 原样返回 YAML SQL，
   Round 2 LLM 不执行，用户原始问题不再进入任何 prompt
3. **无道路约束积木**：recipe SQL 本身无道路类型 WHERE，compound 组装也未触发

附带 bug：`user_strategy.py` 的 `DEFAULT_STRATEGY_DIR` 指向 `app/user_strategies/`（空目录），
而 visualizer 保存和 block_assembler 读取都在 `app/core/user_strategies/`——
**用户保存的策略关键词从未进入路由匹配表**（实测修复前 0 个 → 修复后 13 个：
Y型路口/分流/汇出/合流/汇入/直行路口 等）。

## 修复：参考 SQL 模式（方案 A）

recipe 命中后**不再直接返回**，而是把产线 SQL 作为「参考 SQL」注入 Round 2 prompt，
由 LLM 结合原始问题按需追加约束：

- `concept_router.build_round2_messages(nl, context, reference_sql="")`：
  prompt 新增参考 SQL 段 + 复用规则（语义完全一致则原样；有额外约束则在模板基础上追加 WHERE/JOIN；
  禁删已有约束；含 static_link 高速判断示例）
- `agent_engine._query_two_round` / `generate_sql_two_round`：
  recipe 分支产出 `reference_sql` 而非 `sql`，Round 2 始终执行；
  `sql_source`/`route_method` = `recipe_guided`，按 LLM 路径走 dry-run 纠错循环
- schema 注入：参考 SQL 涉及的表全部纳入 `involved_tables`，并强制附带
  `static_link` + `ego`（道路约束依赖 `ego.ego_static_map_link_id JOIN static_link`）
- `user_strategy.py`：DEFAULT_STRATEGY_DIR 修正为 `app/core/user_strategies`

代价：recipe 命中也要多跑一轮 Round 2 LLM（原来短路免 LLM），延迟略增，换取约束不丢失。

## E2E 验证（DSW 真实链路，generate-sql）

**⚠️ 修正（2026-08-19 当天）**：本节第一次记录的 E2E 结果无效——当时所有请求其实都因
`self.schema` 是 `List[TableInfo]`（迭代得到 dataclass 对象而非表名，`re.escape(t)` 抛
`decoding to str: need a bytes-like object, TableInfo found`）而 100% 崩溃，
全部降级到旧关键词兜底路径。当时只检查了输出 SQL "看起来像对的"就宣布通过，
**没有核对 route_method 和日志路径**，被兜底路径掩盖。教训：验证必须核对执行路径，
不能只看输出内容。

bug 修复（`b456cdb`，两处 schema 扫描先构建 `{t.name}` 集合再匹配）后的真实验证：

| 问题 | route_method | 结果 |
|---|---|---|
| 查询高速道路上自车cut in的场景 | **recipe_guided**（参考模式真正跑通） | ✅ SQL 含 `ego JOIN static_link` + `sl.link_class = '高速公路'`，validation_error=None |
| 查询自车cut in的场景（对照组） | **recipe_guided** | ✅ 简单 cut-in 查询，无 static_link/高速约束混入，validation_error=None |

日志核查：修复后两次请求均无「两轮生成失败，降级到关键词路径」警告。

## 行为变化备注

参考模式下 LLM 对模板有裁量权：两次测试中 LLM 都选择了基于 `range_tag` 产线标签的简洁查询
（`CRUISE_CUTIN` 等），而非照抄 6716 字符的 ego_cut_in 轨迹分析模板。
语义均正确且通过 dry-run，但如果后续希望"无额外约束时严格照抄模板"，
需要加强 prompt 第 1 条复用规则的权重。
