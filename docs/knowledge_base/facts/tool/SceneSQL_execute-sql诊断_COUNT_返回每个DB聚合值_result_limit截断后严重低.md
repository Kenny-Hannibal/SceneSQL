---
category: tool
tags: SceneSQL,pitfall,diagnostic
---

SceneSQL execute-sql诊断: COUNT(*)返回每个DB聚合值，result_limit截断后严重低估(实测40 vs 11996)。正确做法=SELECT * FROM range_tag WHERE...看response.total_rows
