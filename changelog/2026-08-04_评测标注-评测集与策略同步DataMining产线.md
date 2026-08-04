# 2026-08-04 — 评测标注 + 评测集/策略同步 DataMining 产线

## 背景

避免重复劳动：在 SceneSQL 前端验证 SQL、播放视频确认 case 后，
无需到 DataMining 平台重做评测集与策略录入。

## 功能

### 1. 播放器通过/不通过标注
- 可视化播放器头部新增 `✅ 通过` / `❌ 不通过` 按钮 + 已标注 badge
- 当前 SQL 已保存为策略（按 SQL 文本精确匹配）→ 直接绑定到该策略
- 未保存 → 先弹保存策略窗口，保存成功后自动补提交标注
- 结果表 Action 列显示标注小圆点（绿=通过，红=不通过）
- 标注按 (策略, bag_id, start_ts, end_ts) upsert，可翻转

### 2. 评测集同步到产线
- 策略卡片「同步评测集」按钮 → 弹窗（benchmark 名可编辑，默认
  `scenesql_<策略名>`，显示 tag 映射预览与 case 统计）
- 后端组装 `label_res_list`：`bin_id=row.bag_id`（SceneSQL 查询行 bag_id
  即 ubm_vehicle_module_bin 的 data_id，实测确认，无需 dm_sdk 反查）、
  `mining_table=ubm_vehicle_module_bin`、tag=`<策略名>_positive/negative`、
  秒→纳秒、version=v1
- POST `{DATAMINING_BASE_URL}/evalset/benchmark/upload`（批量 JSON；
  产线无流式接口，benchmark 自动创建，按 hash 幂等去重，可重复同步）

### 3. 策略同步到产线
- 策略卡片「同步策略」按钮 → POST `{base}/api/text2sql/strategy/save`，
  重名（code=409）→ 按名 search 取 id → POST `strategy/update/{id}`

## 改动文件

| 文件 | 说明 |
|------|------|
| `visualizer/backend/app/core/config.py` | +DATAMINING_BASE_URL / EVAL_SYNC_TOKEN |
| `visualizer/backend/app/core/eval_case_store.py` | 新增，JSONL 标注存储（agent/backend/app/core/eval_cases/） |
| `visualizer/backend/app/services/datamining.py` | 新增，httpx 调产线 upload / strategy save-or-update |
| `visualizer/backend/app/api/eval_labels.py` | 新增，/api/eval-labels CRUD + sync-evalset |
| `visualizer/backend/app/api/strategies.py` | +POST /{name}/sync-dm；删除策略级联清标注 |
| `visualizer/backend/app/main.py` | 注册 eval_labels router |
| `visualizer/frontend/src/components/AgentPanel.jsx` | 标注按钮/badge/小圆点 + 同步弹窗 |

## 端点实测（DSW curl）

- upload：`scenesql_smoke_test` 提交 1 条 → code 200 successCount 1；重复提交幂等
- strategy/save：新建 200；重名 body code=409
- 网关前缀：evalset = `{host}/api/datamining/evalset/...`；
  text2sql = `{host}/api/datamining/api/text2sql/...`（controller 自带 /api）

## 部署注意

- 容器/DSW 环境如有 HTTP(S)_PROXY 环境变量且产线 ALB 走代理超时，
  需在启动环境加 `NO_PROXY`/`no_proxy` 覆盖 ALB 域名
- Docker 镜像无需 dm_sdk（同步只用 httpx）

---

## 更新 2026-08-04（对齐产线后端更新）：mining_table 修正 + 评测详情同步

产线 DataMining 后端更新后复核链路，两处调整：

1. **sync-evalset 去掉 `mining_table`**。SceneSQL 的 `bag_id` 即 em_bin（ubm_vehicle_module_bin 的 data_id）。实测：不传 mining_table 时产线按 em_bin 反查 UBM 推断真实 collection 表（如 `collection_t68_thor_bag` + `..._metadata` viz 表）；此前强传 `ubm_vehicle_module_bin` 反而指错表。现 payload 只传 `bin_id/tag_name/start_ts/end_ts/version`。
2. **sync-dm 新增评测详情同步**。DataMining 策略的"评测详情"（哪些 case 通过/不通过）即策略评测记录，与 SceneSQL 策略标注同源。`sync-dm` 在 save-or-update 拿到策略 id 后，把全部标注 case 经 `POST /api/text2sql/strategy/review` 推送（`reviewResult` pass→1 / fail→2，bag_id 同作 bagId+dataId，秒级 ts），产线按 (strategyId,bagId,dataVersion,startTs) 幂等 upsert。

### 改动文件
| 文件 | 说明 |
|------|------|
| `app/api/eval_labels.py` | sync-evalset 去掉 mining_table；移除 settings 依赖 |
| `app/services/datamining.py` | +`sync_strategy_reviews()` 推送评测详情 |
| `app/api/strategies.py` | sync-dm 追加评测详情推送，响应含 `reviews` 计数 |
| `AgentPanel.jsx` | 同步策略确认/结果文案含评测详情计数 |

### 实测（产线 ALB）
- benchmark upload 无 mining_table → 推断 `collection_t68_thor_bag`，successCount 1（测试 benchmark 已硬删）
- strategy/review：pass+fail 各 1 → review-stats `pass_count=1 fail_count=1 total_count=2`（测试策略已删）
