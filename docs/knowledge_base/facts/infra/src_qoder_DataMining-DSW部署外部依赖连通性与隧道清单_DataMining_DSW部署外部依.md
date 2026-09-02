---
category: infra
tags: qoder,DSW,DataMining,autossh,隧道,MongoDB,连通性
---

> [交接注] 本条为前任原环境(2026-08-31)快照：服务地址/凭证/绝对路径均为历史值，操作时以你自己的 DSW 部署和 .env 为准（映射见交接手册附录A）。

[src=qoder:DataMining-DSW部署外部依赖连通性与隧道清单] DataMining DSW部署外部依赖连通性与隧道清单
DSW（8.130.209.216:1025）不在部分 RDS 的 VPC 网段，外部依赖连通性实测（2026-08-11）：

可达（无需隧道）：MySQL db-zhijia:3306(data_manage)、Redis r-0jl9o8gv84zflzy47t:6379、PG pgm-0jls2m702d32y179:5432(ali_db/map_db)、StarRocks fe-c-ca4b4d642153fa7e-internal:9030、Kafka alikafka-pre-*:9092、OSS 内网/公网、PAI-EAS vpc、tianyan 172.31.253.74:13306、OceanBase、ALB。

不可达（需经用户公司电脑 autossh 反向隧道到 root@10.115.197.74=DSW）：
- MongoDB s-0jl3527b6e7aebf4.mongodb.rds.aliyuncs.com:3717（评测页必需；实为 sharded 集群）
- PG pgm-0jle221pauf42i45:5432（strategy_db，走 15432，注意现有 15432 隧道实际指向 pgm-0jls）
- 多模态 ES es-cn-db14n1rna0005oku2:9200、自动化引擎 10.115.129.45:11012、remote.task 172.16.172.79:11516、预控 172.16.171.100:11616

关键约定：
1. MongoDB 单点隧道必须用 directConnection=true，否则驱动会去找副本集其它节点。
2. DSW 无法主动 ssh 跳板机（防火墙挡），隧道只能由用户公司电脑侧发起（autossh -R）。
3. 后端启动需覆盖：`--spring.data.mongodb.uri='mongodb://infra_pipeline:df5_herer@127.0.0.1:3717/data_pipeline?authSource=admin&authMechanism=SCRAM-SHA-1&directConnection=true'`
4. 用户公司电脑手动跑的 autossh 是临时进程，重启会丢；持久化靠 ~/.config/systemd/user/l1-sdc-rssh-dsw.service。

后端完整启动命令（/root/data/DataMining 下）：
java -jar data-mining-starter/target/data-mining.jar --spring.profiles.active=prod --scene-sql.enabled=true --oss.sync.enabled=false --oss.sync.local-dir=/mnt/ubm_code_nas/gac_huangzijian/common_data/sqlite_dbs/ --oss.nas.sync.nas-dir=/mnt/ubm_code_nas/gac_huangzijian/common_data/sqlite_dbs/ --text2sql.schema.local-dir=/tmp/dm_schema --text2sql.tag-semantics.local-dir=/tmp/dm_schema --text2sql-agent.config.local-dir=/tmp/dm_schema '--spring.datasource.dynamic.datasource.strategy_db.url=jdbc:postgresql://127.0.0.1:15432/ods?currentSchema=public' --remote.cerberus.url=http://alb-2hjgj3j3kmcpx75nds.cn-wulanchabu.alb.aliyuncsslb.com/api/cerberus '--spring.data.mongodb.uri=mongodb://infra_pipeline:df5_herer@127.0.0.1:3717/data_pipeline?authSource=admin&authMechanism=SCRAM-SHA-1&directConnection=true'，日志 /tmp/dm_service.log。

另：主库 data_manage 原缺 strategy_deployment_record 表（Mapper 无 @DS 默认走 master），2026-08-11 已手工建表，DDL 存档 DataMining/data-mining-starter/src/main/resources/db/migration/V20260811_0__create_table_strategy_deployment_record.sql；项目 Flyway 处于禁用状态，migration 文件仅存档。
