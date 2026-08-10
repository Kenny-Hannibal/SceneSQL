# 2026-08-11 generate-sql 两轮路由试点（feature/gen-sql-two-round）

**分支**: `feature/gen-sql-two-round`（commit df93c1e 起）
**测试报告**: `test_reports/gensql-two-round-test-report.md`

## 变更内容

1. **generate-sql 端点升级两轮路由** — `visualizer/backend/app/api/agent.py`
   新增 `AgentEngine.generate_sql_two_round()`：概念识别（Round1 JSON）→ recipe 组装（免 LLM）
   → hybrid block 拼装 → Round2 LLM 生成 → EXPLAIN dry-run + ≤1 轮纠错。
   `GENSQL_TWO_ROUND=false` 一键回退旧关键词路径；契约 §3.1 响应字段不变。
2. **BlockAssembler 全局单例** — `agent/backend/app/core/block_assembler.py`
   `get_block_assembler()`，避免每请求重载全部 block/recipe YAML。
3. **优化方案文档** — `docs/NL2SQL_OPTIMIZATION_PLAN_V1.md`（含基线实测数据）

## 测试验证

- 金标 26 题 generate-sql 对比：recipe 命中 15/26（57.7%，目标 ≥30% 达成），
  生成成功 25/26，2 条 validation_error（hybrid 截断、Round2 空返回）
- execute-sql E2E：recipe SQL（cutin/超速）真实数据可执行，start_ts/end_ts 齐全
- **代价**：延迟回退（p50 6.06s → 16.24s），主因 Round1 概念识别 LLM；
  下一步在 Round1 前加纯本地预匹配
- 详见 `test_reports/gensql-two-round-test-report.md`
