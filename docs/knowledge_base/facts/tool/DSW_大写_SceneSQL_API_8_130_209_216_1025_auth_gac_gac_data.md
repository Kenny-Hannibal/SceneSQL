---
category: tool
tags: DSW,SceneSQL,API,auth
---

> [交接注] 本条为前任原环境(2026-08-31)快照：服务地址/凭证/绝对路径均为历史值，操作时以你自己的 DSW 部署和 .env 为准（映射见交接手册附录A）。

DSW (大写) SceneSQL API: 8.130.209.216:1025, auth=gac/gac_data（原环境值，你的见 .env）, 内部curl http://127.0.0.1:30001. 批次20260702_T68_2471_c5afa57_100w=15460 DBs. execute-sql诊断必须用SELECT *+total_rows，禁止COUNT(*)。直行路口最优方案=topology_intersection+successor_count=1(815条)
