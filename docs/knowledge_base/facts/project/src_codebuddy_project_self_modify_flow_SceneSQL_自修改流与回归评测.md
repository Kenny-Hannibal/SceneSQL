---
category: project
tags: codebuddy,project
---

[src=codebuddy:project_self_modify_flow] SceneSQL 自修改流与回归评测
SceneSQL 已建自修改流（2026-08-25 五层齐全）：
- ① 评测 `tools/eval_nl2sql_regression.py`（golden_queries.jsonl，route_method+语义+known_limitation 断言）
- ② 归因 `tools/classify_regression_failures.py`（失败分类 route_masked/sql_validation/semantic_deviation/forbidden_feature/infra + 修复建议）
- ③ 修复建议 `tools/suggest_synonyms.py "查询" recipe`（LLM 出同义词 diff，默认只打印人审，--apply 才并入）
- ④ 验证 = 重跑回归
- ⑤ 沉淀 `tools/add_golden_case.py --id rg-XX --question ... --must-contain ...`（badcase 转正，id 唯一校验）
另有 `tools/eval_vector_recall.py`（向量召回 hit@1/3）。

**Why:** TableInfo 崩溃被兜底路径掩盖的事故证明「输出正确 ≠ 路径正确」；
改路由/prompt/同义词表/阈值前没有回归网就是裸奔。

**How to apply:**
- **改 NL2SQL 链路任何环节（concept_router/agent_engine/recipe/prompt/vector_synonyms/阈值）后，
  必须在 DSW 跑回归**：`.venv/bin/python tools/eval_nl2sql_regression.py --report /tmp/regression_$(date +%F).json`
  （25 条约 10 分钟，DeepSeek 依赖；断言失败先看 route_method 是否被兜底掩盖）
- 向量索引改动后跑 `tools/eval_vector_recall.py`
- 失败 case 修复后要转正为 golden set 新用例（自修改流第⑤步沉淀）
- **rg-17 已修复**：聚合查询（GROUP BY）走「非可视化结果类型」——`_is_aggregate_sql` 判定后跳过
  start_ts/end_ts 锚定校验，`visualizable=false` 前端隐藏可视化按钮。若以后聚合查询再报
  start_ts 缺失，检查是不是 _is_aggregate_sql 漏判了新写法
- 策略启停：user_strategies yaml `status: active|disabled`，路由/索引只收 active
- Phase 4a 阈值/margin env 可调：SCENESQL_VEC_THRESHOLD(0.35)/SCENESQL_VEC_MARGIN(0.03)
- **DSW 部署注意**：DSW 仓库在 `feature/gen-sql-two-round` 分支（本地全为 master 合并提交，
  无独有内容），同步用 `git fetch && git checkout origin/master -- <变动目录>` 最稳；
  `git merge` 会被暂存区挡住。user_strategies/*.yaml、eval_cases/、data/users.json、
  data/history/ 是 DSW 本地未跟踪的生产数据，任何 reset/clean 前必须确认
