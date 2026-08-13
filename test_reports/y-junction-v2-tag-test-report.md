# SceneSQL Y型路口 v2 标签切换 测试报告

> **版本**: feature/gen-sql-two-round @ c517889
> **日期**: 2026-08-13
> **测试人**: Coder Agent

## 1. 版本概述

「Y型路口」策略与同名 recipe 的 raw_sql 由查询 UBM 上游旧标签
`topology_intersection_y_junction`（质量差，用户明确要求替换）切换为
新标签 `topology_intersection_y_junction_v2`。新标签由独立链路产出：
OceanBase 地图 `road_info` 提取「一分二」link 分叉点（分支夹角≤60°、
剔除路口内 link）+ 各 bag ego 经纬度轨迹匹配（30m 半径、≥2 点命中），
批次 20260702 全量 15,460 bags 中 10,847 个正样本、42,045 条区间行已
回写至各 bag `range_tag` 表，param 带 `source=ego_track_fork_match`，
与上游旧标签完全隔离（旧行未动）。

## 2. 代码修改总结

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `agent/backend/app/core/user_strategies/Y型路口.yaml` | 修改 | raw_sql 中 tag_name 过滤改为 `_v2`，碎片合并逻辑不变 |
| `agent/backend/app/core/recipes/intersection_y_junction.yaml` | 修改 | 同上，保持策略与 recipe 一致 |
| `CHANGELOG.md` + `changelog/2026-08-13_*.md` | 新增 | 变更记录 |

## 3. 测试总结

### 3.1 端到端测试结果（本机 curl → http://8.130.209.216:30001）

| # | 查询 | 命中 | sql_source | 行数 | start_ts | end_ts | SQL逻辑 | 结果 |
|---|------|------|-----------|------|----------|--------|---------|------|
| 1 | NL「找出Y型路口场景」batch=20260702_T68_2471_c5afa57_100w | Y型路口 recipe | recipe | 20 (result_limit) | ✓ | ✓ | SQL 仅含 v2 tag，无旧 tag | ✅ |
| 2 | execute-sql `SELECT COUNT(*) FROM range_tag WHERE tag_name='topology_intersection_y_junction_v2'` db_limit=50 | — | 直接SQL | 100 | — | — | — | ✅ |
| 3 | /api/strategies 列表 | — | — | — | — | — | Y型路口策略 sql 含 v2、无旧 tag | ✅ |

### 3.2 SQL 逻辑审查

- 策略 SQL（recipe 直通）：碎片合并（间隔≤3s 归组）+ 边界裁剪
  （-10s/+5s，不超出 ego 首末 ts）+ 输出 start_ts/end_ts/duration，与产线一致 ✓
- 无 `*1e9`、无 `->>`、无编造表名；tag_name 为实际存在且已全量回写的 v2 ✓

### 3.3 数据层验证（回写侧，DSW 上执行）

| 检查项 | 结果 |
|--------|------|
| v2 行数 / bags | 42,045 行 / 10,847 bags，0 错误 |
| 幂等性 | 重复回写无重复行（主键 + INSERT OR IGNORE） |
| 旧 tag 残留 | 全批扫描 0 条我方残留行 |
| UBM 上游行 | 712 行 / 576 bags 原样保留 |
| 正样本抽查 | 100/100 bag 策略 SQL 查出场景区间 |
| 负样本抽查 | 10/10 bag 查询为空 |
| 正样本率一致性 | API 抽 50 bag 34 个有 v2 行 ≈ 全量 70.2% |
| 同区间多 fork | 合并为一行，forks 列表保留全部明细 |

### 3.4 已知问题

| # | 问题 | 影响 | 计划 |
|---|------|------|------|
| 1 | 旧批次（非 20260702）尚无 v2 数据，策略查询返回空 | 仅新批次可用 | 需要时对其他批次重跑匹配+回写脚本 |
| 2 | 70.2% 正样本率偏高，部分为普通道路分叉（formWay 7） | 标签含一定宽松匹配 | 可后续加 formWay/道路等级过滤收紧 |

## 4. 产出物

- 回写脚本: `DSW:/root/data/gac_huangzijian/test_code/y_junction/writeback_range_tag.py`
  （支持 --tag-name / --cleanup / --bags / --limit，幂等）
- 匹配管线与结果: `DSW:/root/data/gac_huangzijian/test_code/y_junction/`
  （pipeline.py / y_junction_labels_20260702.csv / README.md）
