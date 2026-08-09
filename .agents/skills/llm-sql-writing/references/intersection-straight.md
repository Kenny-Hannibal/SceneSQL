# 直行通过带红绿灯路口 SQL

## ⚠ successor_count=1 已废弃

`successor_count=1` 衡量的是"道路段后接几条路"而非"路口几个出口方向"，逻辑上错误。解释详见下方"successor_count 为什么错"章节。

## 当前最优方案：纯 tag 过滤

**核心发现**: `INTERSECTION_STRAIGHT` 与 `INTERSECTION_LEFTTURN/RIGHTTURN` 在同一 bag 中绝不共现。每个路口要么是 STRAIGHT，要么是 LEFTTURN/RIGHTTURN，要么无标记。因此直接用 tag 时间重叠过滤即可。

### SQL（带红绿灯）

```sql
WITH ego_first_ts AS (
    SELECT MIN(ts) AS package_start_ts, MAX(ts) AS package_end_ts FROM ego
),
straight_isect AS (
    SELECT rt.start_ts, rt.end_ts
    FROM range_tag rt
    WHERE rt.tag_name = 'topology_intersection'
      AND NOT EXISTS (
          SELECT 1 FROM range_tag rt2
          WHERE rt2.tag_name IN ('INTERSECTION_LEFTTURN', 'INTERSECTION_RIGHTTURN')
            AND rt2.start_ts <= rt.end_ts AND rt2.end_ts >= rt.start_ts
      )
      AND EXISTS (
          SELECT 1 FROM range_tag rt3
          WHERE rt3.tag_name = 'INTERSECTION_STRAIGHT'
            AND rt3.start_ts <= rt.end_ts AND rt3.end_ts >= rt.start_ts
      )
),
with_traffic_light AS (
    SELECT si.start_ts, si.end_ts
    FROM straight_isect si
    WHERE (
        SELECT SUM(CASE WHEN ego.latest_traffic_light_status != '未知' THEN 1 ELSE 0 END)
        FROM ego WHERE ego.ts BETWEEN si.start_ts AND si.end_ts
    ) > 0
)
SELECT 'straight_intersection_with_trafficlight' AS tag_name,
    CASE WHEN e.package_start_ts > wt.start_ts - 10 THEN e.package_start_ts ELSE wt.start_ts - 10 END AS start_ts,
    CASE WHEN e.package_end_ts < wt.end_ts + 10 THEN e.package_end_ts ELSE wt.end_ts + 10 END AS end_ts
FROM with_traffic_light wt, ego_first_ts e ORDER BY start_ts;
```

### SQL（不带红绿灯，高 recall）

去掉 `with_traffic_light` CTE，直接从 `straight_isect` 做时间扩展：

```sql
WITH ego_first_ts AS (
    SELECT MIN(ts) AS package_start_ts, MAX(ts) AS package_end_ts FROM ego
),
straight_isect AS (
    SELECT rt.start_ts, rt.end_ts
    FROM range_tag rt
    WHERE rt.tag_name = 'topology_intersection'
      AND NOT EXISTS (
          SELECT 1 FROM range_tag rt2
          WHERE rt2.tag_name IN ('INTERSECTION_LEFTTURN', 'INTERSECTION_RIGHTTURN')
            AND rt2.start_ts <= rt.end_ts AND rt2.end_ts >= rt.start_ts
      )
      AND EXISTS (
          SELECT 1 FROM range_tag rt3
          WHERE rt3.tag_name = 'INTERSECTION_STRAIGHT'
            AND rt3.start_ts <= rt.end_ts AND rt3.end_ts >= rt.start_ts
      )
)
SELECT 'straight_intersection_with_trafficlight' AS tag_name,
    CASE WHEN e.package_start_ts > si.start_ts - 10 THEN e.package_start_ts ELSE si.start_ts - 10 END AS start_ts,
    CASE WHEN e.package_end_ts < si.end_ts + 10 THEN e.package_end_ts ELSE si.end_ts + 10 END AS end_ts
FROM straight_isect si, ego_first_ts e ORDER BY start_ts;
```

### 结构说明

- `straight_isect` CTE: topology_intersection + 排除LEFTTURN/RIGHTTURN时间重叠 + 要求STRAIGHT重叠 → P=1.0
- `with_traffic_light` CTE: 路口时段内有红绿灯感知（会砍掉2/3结果，因为红绿灯覆盖率低）
- 最终SELECT: ±10秒扩展 + `straight_intersection_with_trafficlight` tag命名
- 如果不需要红绿灯过滤，去掉 `with_traffic_light` CTE，直接从 `straight_isect` 做时间扩展

## GT（Ground Truth）局限性

验证用的 GT = `INTERSECTION_STRAIGHT` 标签，是另一个算法打的标签，不是人工标注。Precision=1.0 只意味着"SQL输出和这个GT完全一致"，不代表绝对正确。

200DB 采样关键数据：
- 97 个 topology_intersection 不与任何 INTERSECTION_* 重叠（无法判断正负）
- 46 个 INTERSECTION_STRAIGHT 完全不在任何 topology_intersection 内（时间范围不对齐）
- 没有绝对 ground truth，真正验证需人工抽检或前端端到端测试

## 验证结果（20260702 批次 100w 数据）

| 版本 | P | R | F1 |
|---|---|---|---|
| 无红绿灯 | 1.000 | 0.843 | 0.915 |
| 加红绿灯(路口时段) | 1.000 | 0.264 | 0.419 |
| 加红绿灯(±10秒) | 1.000 | 0.405 | 0.576 |

## successor_count 为什么错

ego 所在 link 的 successor 全是普通道路（主干道/辅路），没有任何 `link_type='路口'` 的 link。`link_successor` 描述的是道路拓扑中"下一段路"，而不是"路口的出口方向"。

一条主干道 link 的 successor 可能指向下一段主干道和一条辅路——这不代表路口有分岔，只代表这条路后面分成了两条路。

**successor_count 衡量的是"当前道路段后面连接了几条路"，不是"路口有几个出口方向"。**

`successor_count=1` 的真实含义：这条路后面只有一条路（比如直路），而不是"路口只能直行"。

这也解释了为什么 `topology_intersection_straight_intersection` + `successor_count=1` 效果好——不是因为它正确判断了路口类型，而是因为 `successor_count=1` 顺带筛掉了大多数普通路口（因为普通路口前的道路段大多有2+个successor），只剩下了少数特殊路段。

## 全方案对比（150DB 验证）

| 方案 | 逻辑 | P | R | F1 |
|---|---|---|---|---|
| **纯tag过滤+STRAIGHT** | topology_intersection - LEFTTURN/RIGHTTURN + STRAIGHT重叠 | **1.000** | 0.822 | **0.902** |
| 纯tag过滤 | topology_intersection - LEFTTURN/RIGHTTURN重叠 | 0.795 | 0.822 | 0.808 |
| ego从未在转弯lane | ego从未在左转/右转/掉头lane上 | 0.684 | 0.762 | 0.721 |
| ego lane=直行 | ego所在lane turn_type=直行 | 0.853 | 0.734 | 0.789 |
| ego lane=直行或路口 | ego所在lane turn_type IN (直行,路口) | 0.770 | 0.282 | 0.412 |
| link级 | 整个link所有lane只有直行/路口 | 0.750 | 0.114 | 0.198 |

## 关键表结构速查

| 表 | link_id 格式 | 说明 |
|---|---|---|
| static_link | 大数字字符串 (513259752...) | 静态拓扑 |
| dynamic_link | 小整数 (1, 2, 3...) | 动态拓扑(时序) |
| dynamic_lane | ref_link_id = 小整数 | 车道(时序) |
| intersection_info | intersection_id=大数字, lane_info=JSON | 路口截面(turn_type编码) |
| ego | ego_link_id=小整数, ego_static_map_link_id=大数字 | 两套ID都有 |

映射: `ego.ego_link_id` → `dynamic_link.link_id` / `dynamic_lane.ref_link_id`; `ego.ego_static_map_link_id` → `static_link.link_id`

## 交叉验证数据

`INTERSECTION_STRAIGHT` 和 `INTERSECTION_LEFTTURN/RIGHTTURN` 从不共现（同一bag中完全互斥）。

`topology_intersection` 的 `start_ts/end_ts` 与 `INTERSECTION_*` 的时间范围可能不完全对齐。用 `rt2.start_ts <= rt.end_ts AND rt2.end_ts >= rt.start_ts` 做重叠匹配（而非精确匹配）是正确做法。

47个FN是因为 `topology_intersection` 的时间范围与 `INTERSECTION_STRAIGHT` 完全不重叠（时间粒度差异或标记遗漏）。
