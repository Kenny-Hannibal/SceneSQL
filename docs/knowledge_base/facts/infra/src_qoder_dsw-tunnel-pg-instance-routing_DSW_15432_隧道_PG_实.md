---
category: infra
tags: qoder,DSW,PostgreSQL,隧道,strategy_db,spark_run_record
---

[src=qoder:dsw-tunnel-pg-instance-routing] DSW 15432 隧道 PG 实例归属与 spark_run_record 路由
- DSW 上 `127.0.0.1:15432`（用户笔记本 autossh 反向隧道）指向 RDS 实例 **pgm-0jle221pauf42i45**，库 ods（public schema），用户 infra_pg_test/Infra!0711。该实例含 `sql_strategy` 与 `spark_run_record` 表，是 data-mining 服务 strategy_db 的正确目标。
- **pgm-0jls2m702d32y179 是另一个实例**：可从 DSW 直连（postgres/6yhn&8ik），但没有 `sql_strategy` 表，不能作为 strategy_db 使用。两个实例曾混淆过一次。
- pgm-0jle221pauf42i45 常被其他租户打满连接（"remaining connection slots are reserved for roles with the SUPERUSER ATTRIBUTE"）；DSW 无法直连该实例（VPC 隔离），只能走隧道。饱和时 Hikari 懒重试，等槽位释放即可恢复；无法用 postgres 超管绕过（隧道实例 postgres 密码未知）。
- data-mining 服务（/root/data/restart_dm.sh）strategy_db 覆盖参数应固定为 `jdbc:postgresql://127.0.0.1:15432/ods?currentSchema=public`，不要覆盖用户名密码。
- 服务 HTTP 端口 8089，context-path `/datamining`，接口形如 `http://127.0.0.1:8089/datamining/api/spark-search/*`。
