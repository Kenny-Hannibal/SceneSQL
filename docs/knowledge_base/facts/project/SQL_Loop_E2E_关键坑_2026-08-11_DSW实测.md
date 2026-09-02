---
category: project
tags: SSH,DSW,mage-vl,scenesql,e2e,sqlite,batch20260702,topic-dict,critical
---

> [交接注] 本条为前任原环境(2026-08-31)快照：服务地址/凭证/绝对路径均为历史值，操作时以你自己的 DSW 部署和 .env 为准（映射见交接手册附录A）。

SQL Loop E2E 关键坑 (2026-08-11 DSW实测):

★ SSH必须大写: ssh DSW (8.130.209.216:1025), 小写dsw（8.130.175.37:1021，前任旧机，与你无关）是旧机器只有8策略
★ SQL不能SELECT bag_id: range_tag表无bag_id列, API自动从db文件名提取bag_id
★ range_tag.start_ts/end_ts是秒级(10位Unix timestamp): 传给mage-vl/evaluate API时必须×10^9转纳秒(19位)
★ ★ 默认评测topic是前视宽120°: /gac/cam/orig_fw120_encoded (大多数bag) 或 /gac/cam/fw120_encoded (少数bag)。不是30°的ft30。
★ 正确批次: 20260702_T68_2471_c5afa57_100w (15460个sqlite DBs)
★ bag metadata里无topic解释字典: 只有split_topics_lists列列出camera topics

【完整E2E链路】
1. POST /api/auth/login {username:gac, password:gac_data} → JWT
2. POST /api/agent/execute-sql {sql, query_mode:sqlite, batch_id:20260702_..., result_limit:N}
   → 返回[{bag_id, start_ts(秒), end_ts(秒)}, ...]
3. POST /api/mage-vl/evaluate {bag_id, start_ts:秒×10^9, end_ts:秒×10^9, topic:/gac/cam/orig_fw120_encoded, prompt, max_tokens}
   → 返回{ok:true, bag_id, evaluation:"自然语言评测"}

【Topic命名(从metadata split_topics_lists)】
- /gac/cam/orig_fw120_encoded: 前视宽120° (★默认评测topic)
- /gac/cam/orig_ft30_encoded: 前视30°
- /gac/cam/orig_r50_encoded: 后视50°
- /gac/cam/orig_fl99_encoded: 左前99°
- /gac/cam/orig_fr99_encoded: 右前99°
- /gac/cam/orig_rl99_encoded: 左后99°
- /gac/cam/orig_rr99_encoded: 右后99°
- /gac/cam/orig_ft30_1080_encoded: 前视30° 1080p
- /gac/cam/orig_fw120_1080_encoded: 前视宽120° 1080p

【其他常见topic】
- /gac/cam/apa_encoded: APA摄像头
- /gac/camera/multi_mono3d_obstacle30_raw: 障碍物检测
- /gac/camera/bev_obstacle_raw: BEV障碍物

★ 注意: 旧版topic名可能是/gac/cam/ft30_encoded (无orig_前缀), 但实际bag中都是orig_前缀。 mage-vl/evaluate API的默认topic是/gac/cam/ft30_encoded, 需要手动指定/gac/cam/orig_fw120_encoded。
