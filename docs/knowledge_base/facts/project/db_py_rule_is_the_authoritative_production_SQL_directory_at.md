---
category: project
tags: sql,nl2sql,db-py-rule,reference,production
---

> [交接注] 本条为前任原环境(2026-08-31)快照：服务地址/凭证/绝对路径均为历史值，操作时以你自己的 DSW 部署和 .env 为准（映射见交接手册附录A）。

db_py_rule/ is the authoritative production SQL directory at <projects根>/data_mining/UBM_mining/ubm_data_mining/output/db_py_rule/. 60 .py files, each with tag_name + sql triple-quoted string. Zero * 1e9 bugs — all use `e.ts BETWEEN r.start_ts AND r.end_ts` directly. Contains complex multi-CTE patterns (agent_cross_conflict 7 CTEs, left_turn_conflict, close_following gaps-and-islands, ego_cut_in lead/follow classification). Same SQLite DB environment as SceneSQL — correct reference for few-shot SQL examples. templates.jsonl has 34 * 1e9 bugs because it was written referencing Python operator code (nanosecond env), not adapted for DB-level SQL (seconds env).
