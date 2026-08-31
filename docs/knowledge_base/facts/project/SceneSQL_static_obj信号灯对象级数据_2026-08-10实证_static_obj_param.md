---
category: project
tags: SceneSQL,static_obj,signal_vector,api,e2e
---

SceneSQL static_obj信号灯对象级数据(2026-08-10实证): static_obj.param JSON含 {id(灯杆ID跨帧不变), lane_ids, is_ego_light, signal_vector:[{type,status}]}。status与ego整数码一致(1绿2黄3红)。type=灯类型(2-7,14,待proto确认)。数据稀疏:多数bag仅几十~几百帧有signal_vector。坑1: CTE只在EXISTS子查询里引用时SELECT列不能引用它(no such column),必须JOIN w。坑2: 批量execute-sql API静默吞掉单DB错误(0行无报错)。execute-sql不经LLM(旧fact_store'LLM改写'已失效)。E2E客户端模板: SceneSQL/v11_recall/api_client.py, POST /api/agent/execute-sql + batch_id + query_mode=sqlite, 结果行自带bag_id。
