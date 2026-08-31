# Y型路口：地理信息与 SQL DB 关联链路

> 版本：v4（2026-08-17）
> 部署机：DSW（`ssh DSW`，8.130.209.216:1025），工作目录 `/root/yj_r2/`，
> 代码镜像 `/root/data/gac_huangzijian/test_code/y_junction/`

## 1. 总体链路

```
OceanBase (infra_map_data)                    bag sqlite DBs (15,460 个)
  ├─ road_info   道路几何/属性/拓扑              └─ ego 表: utm_x, utm_y, ts
  ├─ lane_info   车道类型(laneTypes)                     │
  └─ obj_info    type=1 路口 / type=4 信号灯             ▼
        │                                        pipeline.py 打标
        │ ① 一次性全量预取(单连接串行)                    │
        ▼                                        ③ 自车轨迹 → UTM→WGS84
  junctions.csv.gz   124K 路口                           → 0.05° 网格匹配分叉点
  lights.csv.gz      363K 信号灯                         → 滑窗多数投票
  forks_v4.csv.gz    1,822 个已定案 Y 型分叉点            ▼
        └──────────② 纯本地查表，零 OB 依赖──▶   labels_v4.csv (bag, ts窗, label, fork_id)
                                                         │
                                                         ▼ ④ writeback_range_tag.py
                                                  各 bag DB 的 range_tag 表
                                                  (marker: ego_track_fork_match)
                                                         │
                                                         ▼ ⑤ SceneSQL 策略检索
                                                  Y型路口.yaml + recipe
                                                  tag_name = topology_intersection_y_junction_v4
                                                         │
                                                         ▼
                                                  线上 API (8.130.209.216:30001)
```

核心思想：**地理信息只从 OB 查一次**，落成三张本地预取表；之后所有打标、
回写、检索全部在本地/SQLite 内完成，流水线零 OB 依赖。

## 2. 数据源：OceanBase

实例 `t7txo1a472krk.aliyun-cn-wulanchabu.oceanbase.cloud`，库 `infra_map_data`。

| 表 | 用途 | 关键字段 |
|---|---|---|
| `road_info` | 道路 link | `roadId, geometries(WKT), direction, formWay, roadClass, fromRoadIds, toRoadIds, reverseToRoadIds, length, maxSpeedLimits` |
| `lane_info` | 车道 | `roadId, laneTypes`（位掩码；33554432/536870912 = 掉头类） |
| `obj_info` type=1 | 路口对象 | `relatedLinkIds, geometries` → 落在路口内的分叉不算 Y 型 |
| `obj_info` type=4 | 信号灯 | `relatedLinkIds, geometries` → v4_light 规则用 |

**重要约束：该 OB 实例不支持并发查询**。2 个连接同时查就会整体挂死
（卡在认证/读包阶段），单连接串行永远正常。因此所有全量操作都是单连接串行。
另注意 `roadId` 无索引，按 IN 分块查会每次全表扫描，必须流式全表扫后本地过滤。

## 3. 预取层（OB → 本地表，只做一次）

| 脚本 | 输出 | 内容 |
|---|---|---|
| `dump_junctions.py` | `junctions.csv.gz` | 124K 路口：`lon,lat,"id;id"` |
| `dump_lights.py` | `lights.csv.gz` | 363K 信号灯：`lon,lat,related_link_ids` |
| `dump_forks.py` | `forks_v4.csv.gz` | **全国所有已通过 v4 规则判定的 Y 型分叉点（1,822 个）**：`lon,lat,fork_road_id,branch_road_ids,dir,branch_angle` |

`dump_forks.py` 流程（约 150s，单连接）：
1. 拉全部「一分二」候选 link（toRoadIds/reverseToRoadIds 恰含 2 个 ID）→ 373,606 行；
2. 流式全表扫 `road_info` 取分支几何/属性、`lane_info` 取车道类型；
3. 复用 `compute_forks()` 纯函数做与在线打标**完全相同**的规则判定
   （cls_le2 / main_side / fw15_slow_long / spd_asym / parallel_pair / v4 三子规则），
   直接输出已定案的分叉点。

## 4. 打标流水线（pipeline.py，零 OB）

```bash
python3 pipeline.py --workers 8 \
    --forks-csv /root/yj_r2/forks_v4.csv.gz \
    --out labels_v4.csv
```

1. 遍历 15,460 个 bag DB：`/mnt/ubm_code_nas/gac_huangzijian/common_data/sqlite_dbs/20260702_T68_2471_c5afa57_100w/{bag}.db`
2. 读 `ego` 表（utm_x, utm_y, ts）→ 转 WGS84 经纬度；
3. 按 0.05° 网格在 forks_v4 表里查表（不再现查 OB）；自车轨迹命中分叉点
   bbox 且分支夹角朝向符合 → 该时间窗记为 Y 型路口；
4. 输出 `labels_v4.csv`（bag, start_ts, end_ts, label, fork 信息）。

全量重刷约 1 分钟（旧在线版 4 小时+ 且会卡死）。

## 5. 写回（writeback_range_tag.py）

把 label=1 的行写回各 bag DB 的 `range_tag` 表：

```bash
# 清理旧标签行（全量 bag）
python3 writeback_range_tag.py --labels labels_v4.csv --cleanup-only
# 写入新标签行
python3 writeback_range_tag.py --labels labels_v4.csv
```

- 行来源标记（ego_track_fork_match）用于精确删除历史行，不伤其他标签；
- 清理必须对**全部 15,460 个 bag** 跑（含由正转负的 bag），再插入新行。

## 6. 检索消费（SceneSQL）

仓库 `/root/data/text2sql`（分支 feature/gen-sql-two-round）：

- 策略：`agent/backend/app/core/user_strategies/Y型路口.yaml`
- Recipe：`agent/backend/app/core/user_strategies/recipes/intersection_y_junction.yaml`
- 两处的 `tag_name` 均为 `topology_intersection_y_junction_v4`（v3 已下线）。

策略 SQL 对 bag DB 的 `range_tag` 表按 tag_name + 时间窗过滤，
经线上 API（http://8.130.209.216:30001，账号 gac/gac_data）对外提供检索。

## 7. v4 判定规则速查

| 规则 | 作用 |
|---|---|
| cls_le2 / main_side / fw15_slow_long / spd_asym / parallel_pair | v3 基础规则，保留真 Y 型 |
| v4_stub | 分支长度 <100m 的短 stub 剔除（公交车站内凹等） |
| v4_light | 掉头车道型 {536870912, 33554432} 且 60m 内信号灯指向分叉/分支 → 剔除 |
| v4_parallel | 50/100/150m 采样处两分支间距 ≤7m → 平行分流，剔除 |

## 8. 相关文件索引（DSW）

| 文件 | 说明 |
|---|---|
| `/root/yj_r2/y_junction_extract.py` | extract_bbox + compute_forks 纯函数（在线/离线共用） |
| `/root/yj_r2/dump_forks.py` | 全国分叉点一次性预取 |
| `/root/yj_r2/pipeline.py` | 离线打标流水线 |
| `/root/yj_r2/labels_v4.csv` | 当前权威标签（15,460 行 / 1,588 正） |
| `/root/yj_r2/forks_v4.csv.gz` | 预取分叉点表 |
| `/root/yj_r2/e2e_check_v4.py` | 线上 API 端到端校验 |
| `test_code/y_junction/writeback_range_tag.py` | range_tag 写回/清理 |
| `text2sql/changelog/2026-08-17_Y型路口策略切换v4标签-*.md` | v4 上线报告 |
