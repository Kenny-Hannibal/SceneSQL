---
category: project
tags: SQL-Loop,Mage-VL,workflow,SceneSQL,strategy,evalset,auth,merged
---

SQL Loop 完整流程 (2026-08-11 修正DSW地址):

【认证】
- 登录端点: POST /api/auth/login, body: {"username":"gac","password":"gac_data"}
- 返回: {"access_token":"...","token_type":"bearer"}
- 后续请求 Header: Authorization: Bearer <token>
- 用户名/密码来源: 项目部署根目录 .env 文件 (AUTH_USERNAME=gac, AUTH_PASSWORD=gac_data)
- JWT Secret: JWT_SECRET env var (默认 sceneSQL_visualizer_secret_key_2026)

【API 端点 — 大写DSW: http://8.130.209.216:30001】
- SceneSQL 后端: http://8.130.209.216:30001 (大写DSW, 非小写dsw)
- 登录: POST /api/auth/login
- NL查询: POST /api/agent/query
- SQL执行: POST /api/agent/execute-sql
- 策略列表: GET /api/strategies
- 评测集: GET /api/eval-labels/{strategy_name}
- Mage-VL评测: POST /api/mage-vl/evaluate (代理到 localhost:31000)
- Mage-VL健康: GET /api/mage-vl/health

【Loop 12 步】
1. 金标准对齐: bag_id + ts 窗口 (不凭场景名想像)
2. SQL 草稿: SceneSQL/<tag>.sql (头注版本史)
3. 对照组回归: 14 个 bag (判错的永远进组)
4. 全量批次: db_limit=20000, max_workers=32, 15460 DBs
5. 新 seed: 随机抽 10 条
6. ★ 导出 mp4: POST /api/video/extract (bag_id + ts → mp4)
7. ★ 送入 Mage-VL: 完整 mp4 视频送进 SGLang port 31000 (利用 codec-native sparsity)
8. ★ Mage-VL 输出自然语言评价
9. ★ 喂给 GLM-5.2 改 SQL
10. 误报归因到机制 → 修改 SQL
11. 交付: 底稿 + recipe yaml (逐字节一致) + 验证集打包
12. 用户复核判错 → 回到 1

【策略+评测集工作流】
- Loop以策略形式维护, 一个或多个策略, 每个策略有正式发版版本
- 发版节奏: Agent觉得结果不错再发版, 可视化后发现结果太差则修正后覆盖或删除
- 验证过的场景收回到策略的评测集
- Loop完成后告知交付版本
- 用户调取评测集二次打标 → 下一轮loop的评测集作为金标准

【关键注意事项】
- SSH必须大写: ssh DSW (8.130.209.216:1025), 小写dsw (8.130.175.37:1021)是旧机器只有8策略
- SQL必须返回 start_ts/end_ts (秒), 前端依赖这两个标准列名定位视频片段
- 送完整视频不抽帧, 保留 codec-native sparsity 优势
- VLM judge: Mage-VL (SGLang port 31000, codec-native sparsity)
- SQL写作LLM backbone: GLM-5.2 (vLLM port 8000, served-model-name qwen3.5)
- 相关skill: vlm-scene-verification, llm-sql-writing
- 相关fact: fact_id 363 (VLM judge 部署)
