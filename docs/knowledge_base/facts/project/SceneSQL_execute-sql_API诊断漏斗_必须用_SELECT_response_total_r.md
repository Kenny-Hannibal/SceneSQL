---
category: project
tags: SceneSQL,SQL,diagnostic,pitfall
---

SceneSQL execute-sql API诊断漏斗：必须用 SELECT * + response.total_rows，禁止用 COUNT(*)。COUNT(*) 每个DB返回一行聚合值，result_limit截断后严重低估（实测40 vs 11996）。
