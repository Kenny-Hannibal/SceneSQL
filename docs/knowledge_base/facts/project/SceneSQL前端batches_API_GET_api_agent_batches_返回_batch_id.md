---
category: project
tags: batches,dataset,testing,api
---

SceneSQL前端batches API: GET /api/agent/batches 返回 [{batch_id, sqlite_count}]。20260616_T68_2434_c5afa57_1.5w (11878 DB) 是最新的含完整schema的数据集。测试时用 batch_id + query_mode=sqlite 参数，不用 db_path。0603数据集部分DB缺少predecessors/obs_dr_trajectory等列是schema版本不一致，不是SQL错误。
