---
category: project
tags: SceneSQL,SQL,debug,API
---

SceneSQL execute-sql API 诊断方法：不要用 COUNT(*) 跨DB统计标签数！COUNT(*) 返回每个DB的聚合值，被 result_limit 截断后严重失真（曾把748条查成8条）。正确做法：`SELECT * FROM range_tag WHERE tag_name = '...'` 然后看响应的 `total_rows` 字段。
