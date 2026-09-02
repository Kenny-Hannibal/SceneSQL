---
category: project
tags: SceneSQL,E2E,mage-vl,DSW,ambiguity,verified
---

> [交接注] 本条为前任原环境(2026-08-31)快照：服务地址/凭证/绝对路径均为历史值，操作时以你自己的 DSW 部署和 .env 为准（映射见交接手册附录A）。

SceneSQL E2E测试验证结果 (2026-08-11):

【完整链路验证】
1. 认证: POST /api/auth/login {username:gac, password:gac_data} → JWT ✅
2. 写SQL: SELECT start_ts, end_ts FROM range_tag WHERE tag_name = 'Cutin' LIMIT 3 ✅
3. 执行SQL: POST /api/agent/execute-sql → 3条结果 (bag_id, start_ts秒, end_ts秒) ✅
4. 送入Mage-VL: POST /api/mage-vl/evaluate → 自然语言评测 ✅

【发现的关键歧义】
★ 歧义1: range_tag.start_ts/end_ts单位是秒(10位), mage-vl/evaluate API期望纳秒(19位)。必须×10^9转换。
★ 歧义2: 默认评测topic应为前视宽120° (/gac/cam/orig_fw120_encoded), 而非前视30° (/gac/cam/ft30_encoded)。已修正代码中4处DEFAULT_CAMERA_TOPIC。
★ 歧义3: fact_store中batch_id不一致。fact_id 807说"正确批次: 20260702_T68_2471_c5afa57_100w", 但DSW .env的ETL_BATCH_ID是20260603_T68_1361_6ec7db_1.5w。
★ 歧义4: fact_store中DSW地址已修正。fact_id 283从8.130.175.37改为8.130.209.216。
★ 歧义5: topic转换字典(fact_id 810)记录了Pattern A(/gac/cam/{cam}_encoded)和Pattern B(/gac/cam/orig_{cam}_encoded)的映射关系, 评测时应优先使用前视宽120°的fw120 topic。

【验证环境】
- DSW: 8.130.209.216:1025 (大写, 唯一部署目标)
- 后端: http://localhost:30001 (DSW内部)
- Mage-VL: http://localhost:31000 (DSW内部, PPU)
- 批次: 20260702_T68_2471_c5afa57_100w (15460个sqlite DBs)
- 认证: username=gac, password=gac_data
- bag_id: 100EIej6BIa9ZbZBtVAg8w202606 (Cutin场景)
- topic: /gac/cam/orig_fw120_encoded (前视宽120°, 默认评测topic)
