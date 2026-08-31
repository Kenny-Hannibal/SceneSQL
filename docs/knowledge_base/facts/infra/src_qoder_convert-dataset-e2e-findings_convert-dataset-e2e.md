---
category: infra
tags: qoder
---

[src=qoder:convert-dataset-e2e-findings] convert-dataset-e2e-findings
转数据集平台化 E2E 环境结论（2026-08-14）

- 湖仓挖掘集合表（gac_dlf.default `dwd_data_mining_collection_*_i`）**不支持 DELETE**（外部 catalog 报 "Db does not exist"），写入不可回滚，真实写入必须先 dry-run/preview 确认。
- paimon 快照提交：INSERT 后约 1–2 分钟才可读到。
- 测试集 t68_test_1000 的合成 bag（如 `100TFtzXocnb89xCQuc3gf202605`）在 `dwd_collection_{t68,ay5}_thor_bag_metadata_hf` 中不存在（metadata bag_id 是 `c-UUID`、bag_name 是 VIN_日期）；与 liulian 生产挖掘行形态一致 → 转数据集必须显式指定 `model`。
- 探针行遗留：`data_id=913332d6-595b-48ec-9fbc-abc5ebc537b1`（metadata `__probe_delete__`，create_user huangzijian）在 dwd_data_mining_collection_t68_thor_bag_i，无法删除，需平台侧过滤。
- 数据集平台 ALB（alb-2hjgj3j3kmcpx75nds...）2026-08-14 上午持续 503，成员注册未联调。
- Gerrit REST 自动化：`git credential fill` 导出的环境变量是小写 `GERRIT_username`/`GERRIT_password`；脚本在 DSW /root/data/gerrit_submit.py、gerrit_detail.py。
- 转数据集开放 API：`/api/spark-search/convert/{preview,submit,status,list}`，默认 dry-run=true；说明书在 DataMining openspec/specs/spark-search/api-guide.md；DSW 脚本 scripts/db_to_dataset/（仓库内）已改为 API 薄客户端。
