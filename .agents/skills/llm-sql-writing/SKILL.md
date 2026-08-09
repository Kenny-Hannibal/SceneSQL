---
name: llm-sql-writing
description: >
  LLM 辅助场景搜索 SQL 写作。当用户要求"写SQL"、"场景搜索SQL"、
  "标量场景SQL"、"查XXX场景"、"帮我写个加塞的SQL"、"搜闯红灯的bag"等
  任何涉及从 SQLite DB 中查询自动驾驶场景数据的请求时，自动加载此 skill。
  包含完整的 schema 知识（8表结构、100+标签体系、JOIN 模式），
  使 Agent 无需反复查源文件即可直接写出正确的场景搜索 SQL。
tags: [sql, scene-search, schema, nl2sql, scene-sql]
version: 1.0
---

# LLM 辅助场景搜索 SQL 写作

> 本 skill 是 Agent 写场景搜索 SQL 的完整知识库。加载此 skill 后，
> Agent 应能：(1) 将用户自然语言映射到正确的 tag_name；(2) 选择正确的表和 JOIN 方式；
> (3) 输出可直接执行的 SQLite SQL。

---

## 1. Schema 定义文件

| 文件 | 用途 | 路径 |
|------|------|------|
| schema_structure.yaml | 纯结构（表名/列名/类型/主键/枚举值），注入 LLM prompt | `agent/backend/app/core/schema_structure.yaml` |
| schema_dictionary.yaml | 标签字典（tag_name → 语义/子标签/局限/关联表） | `agent/backend/app/core/schema_dictionary.yaml` |
| schema_master_raw.yaml | 原始汇总母表 | `agent/backend/app/core/schema_master_raw.yaml` |

> 以上文件均由 `derive_schemas.py` 自动派生，**勿手动编辑**。

---

## 2. 八张表速查

| 表名 | 类型 | 主键 | 说明 | 核心列 |
|------|------|------|------|--------|
| **range_tag** | horizontal_event | (start_ts, end_ts, tag_name) | 时间区间标签表 | tag_name(枚举100+值), start_ts, end_ts, param(JSON) |
| **dynamic_obj** | vertical_timeseries | (ts, obj_id) | 动态障碍物 | x,y,z,l,w,h,heading,type(car/truck/bus/pedestrian/cyclist/suv/motorcycle/motorcyclist/stroller/wheelchair/animal),absolute_velocity_x/y,relative_velocity_x/y,obs_dr_trajectory,is_static,obs_lane_id,obs_static_map_link_id |
| **static_obj** | vertical_timeseries | (ts, obj_id) | 静态障碍物 | x,y,z,l,w,h,heading,type(traffic_warning_object),param |
| **ego** | vertical_timeseries | (ts) | 自车状态 | speed,steering_angle,traffic_light_status(-1无/1绿/2黄/3红),acc_magnitude,ego_lane_id,ego_link_id,ego_static_map_link_id,ego_hq_lane_id,ego_lane_curvature,ego_to_centerline_dist,ego_to_left/right_boundary_dist,latest_traffic_light_status(绿/黄/红/未知),latest_stop_line_direction,navigation_status,wiper_status,cumulative_distance,utm_x,utm_y,utm_yaw,ego_dr_trajectory,ego_lane_width,ego_hq_lane_ids_on_cross_section,ego_lane_index_on_hq_cross_section,ego_lane_successor_count,ego_lane_predecessor_count |
| **intersection_info** | horizontal_event | — | 路口信息 | intersection_id,lane_count,ego_lane_index,lane_info |
| **static_link** | static | (link_id) | 静态道路拓扑 | link_type(主路/辅路/入口匝道/出口匝道/路口/环岛/人行道/平行路/收费站/主干道/右转A/右转B/左转A/左转B/左右掉头/互通立交/服务区/匝道/其他),link_turn_type(直行/左转/右转/掉头/左前直行/右前直行等),link_class(高速/国道/省道/县道/乡道/主要大街/主要道路/次要道路/普通道路/非导航道路等),link_attribute(隧道/桥梁/高架桥/收费站/收费站入口/出口/断头路/其他),link_speed_limit,link_exp_speed_limit,link_predecessor,link_successor,is_intersection_out |
| **dynamic_link** | vertical_timeseries | (ts, link_id) | 动态道路拓扑 | 同static_link列 + include_lane_ids |
| **dynamic_lane** | vertical_timeseries | (ts, lane_id) | 动态车道 | ref_link_id,left_boundary_id,right_boundary_id,lane_type(机动车道/非机动车道/人行道/公交专用道/应急车道/可变车道/潮汐车道/匝道/虚拟车道/其他),lane_turn_type(直行/左转/右转/掉头等),lane_trans_type(普通车道/入口过渡/出口过渡/合流/分流),predecessors,successors,lane_relate_obs_ids |
| **static_lane** | static | (lane_id) | 静态车道 | 同dynamic_lane列(无lane_relate_obs_ids，有lane_relate_obs_id) + link_id |

### 表类型语义

- **horizontal_event**: 时间区间事件（有 start_ts, end_ts）
- **vertical_timeseries**: 时间序列（每行一个时间步，主键含 ts）
- **static**: 无时间维度，单行描述一个实体

---

## 3. 标签体系 (range_tag.tag_name)

### 3.1 标签来源

| 来源 | 说明 | 命名风格 | 示例 |
|------|------|----------|------|
| **车端标签** (source=vehicle) | bag文件 beh_tag 消息 | 大写+下划线 | CRUISE_CUTIN, INTERSECTION_LEFTTURN |
| **云端算子** (source=cloud_operator) | op_*.py 算子输出 | PascalCase/混合 | Cutin, Jerk, LowTTC |

### 3.2 常用云端标签速查（33 个基础标签）

| tag_name | 中文 | sub_tags | 关联表 |
|----------|------|----------|--------|
| Cutin | 加塞/切入 | CarCutin,CarCloseCutin,TruckBusCutin,TruckBusCloseCutin,VruCutin,VruCloseCutin | range_tag+dynamic_obj |
| CrossVehicle | 侧方横穿 | Cross_From_Left,Cross_From_Right | range_tag+dynamic_obj |
| CrossVRUV1 | VRU横穿 | Cross_From_Left,Cross_From_Right | range_tag+dynamic_obj |
| CloseFollow | 近距跟车 | CloseFollow | range_tag+dynamic_obj |
| CongestedFollow | 拥堵跟车 | — | range_tag |
| CrawlingFollow | 蠕行跟车 | CrawlingFollow | range_tag |
| LowTTC | 碰撞时间低 | TTC_Risk | range_tag+dynamic_obj |
| Jerk | 急加减速 | brake, speed_up | range_tag |
| LaneChange | 变道 | LeftLaneChange, RightLaneChange | range_tag |
| SolidLaneChange | 实线变道 | SolidLeftLaneChange, SolidRightLaneChange | range_tag |
| Intersection | 路口 | IsIntersection,LeftIntersection,RightIntersection,LuturnIntersection,UnstableIntersection | range_tag |
| TrafficIntersection | 交通灯路口 | TrafficIntersection | range_tag |
| RunRedLight | 闯红灯 | RunRedLight | range_tag |
| CrossStopLineOnYellowLight | 黄灯越线 | CrossStopLineOnYellowLight | range_tag |
| GreenLightNotProceeding | 绿灯未起步 | GreenLightNotProceeding | range_tag |
| OnRamp | 上匝道 | OnRamp | range_tag |
| OffRamp | 下匝道 | OffRamp | range_tag |
| OtherRamp | 其他匝道 | OtherRamp | range_tag |
| Roundabout | 环岛 | Roundabout | range_tag |
| Slope | 坡道 | — (sub_tag在param中) | range_tag |
| RainLevel / over_rain_level | 降雨 | heavy_rain, light_rain, moderate_rain | range_tag |
| ActiveWiperState | 雨刮激活 | ActiveWiperState | range_tag |
| ObstacleCollision | 碰撞 | ObstacleCollision | range_tag+dynamic_obj |
| ObstacleNearMiss | 近碰 | ObstacleNearMiss | range_tag+dynamic_obj |
| ObstacleAvoidance | 避障 | — | range_tag |
| Turning | 转向 | turn_left, turn_right, turn_back | range_tag |
| AvoidanceBorrowLane | 借道避让 | AvoidanceBorrowLaneLeft, AvoidanceBorrowLaneRight | range_tag |
| Avoidance_InLane | 车道内避让 | AvoidanceInLaneLeft | range_tag |
| SteeringWheelSlam | 猛打方向 | SteeringWheelSlam | range_tag |
| SteeringSmallSwing | 方向盘小摆 | LeftRightAlternateSwing | range_tag |
| HighSteeringWheelTorque | 方向盘力矩过大 | HighSteeringWheelTorque | range_tag |
| AbnormalLaneChange | 异常变道 | — | range_tag |
| LaneKeep | 保持车道 | LaneKeep | range_tag |

### 3.3 Feature 标签（动态 label_id，直接作 tag_name 存入 DB）

这些标签**父名不会出现在 DB 中**，子标签名直接作为 tag_name：

| 父算子 | 子标签（互斥） | 语义 |
|--------|----------------|------|
| SpeedState | stationary, creeping, cruising, decelerating, accelerating, hard_braking, reversing | 自车速度状态 |
| Steering | steering_slightly, steering_left_15_60, steering_left_60_120, steering_left_120_185, steering_left_above_185, steering_right_15_60, steering_right_60_120, steering_right_120_185, steering_right_above_185 | 方向盘转角区间 |
| NaviCommand | navi_keep, navi_enter, navi_exit, navi_turn_left, navi_turn_right, navi_u_turn, navi_other | 导航指令 |
| — | topology_*(topology_intersection, topology_ramp, topology_merge, topology_split, topology_tunnel, topology_toll_station等) | 道路拓扑类型 |
| — | invalid_*(invalid_navi_*, invalid_topology_*) | 无效/异常标签 |

### 3.4 车端行为标签分类（source=vehicle）

| 类别 | 前缀 | 示例 |
|------|------|------|
| 巡航 | CRUISE_* | CRUISE_CUTIN, CRUISE_FOLLOW, CRUISE_STRAIGHT |
| 停走 | STOPANDGO_* | STOPANDGO_FIRSTCARSTOPATREDLIGHT, STOPANDGO_STOPBEHINDOBSTACLE |
| 变道 | LANECHANGE_* | LANECHANGE_NAVIGATION, LANECHANGE_OVERTAKE, LANECHANGE_ABANDON |
| 路口 | INTERSECTION_* | INTERSECTION_LEFTTURN, INTERSECTION_RIGHTTURN, INTERSECTION_UTURNLANE |
| 避让 | AVOIDANCE_* | AVOIDANCE_INLANE, AVOIDANCE_LANEYIELDCARORTRUCK |

---

## 4. 写 SQL 标准流程

### Step 1: 语义 → 标签路由

| 用户说法 | tag_name | 备注 |
|----------|----------|------|
| 加塞/切入 | Cutin | sub_tag区分车辆类型 |
| 横穿/侧方横穿 | CrossVehicle | 车辆横穿 |
| 行人横穿 | CrossVRUV1 | VRU横穿 |
| 闯红灯 | RunRedLight | — |
| 黄灯越线 | CrossStopLineOnYellowLight | — |
| 绿灯未起步 | GreenLightNotProceeding | — |
| 急刹/急加速 | Jerk | sub_tag: brake / speed_up |
| 近距跟车 | CloseFollow | — |
| 拥堵跟车 | CongestedFollow | — |
| 变道 | LaneChange | sub_tag: Left/Right |
| 实线变道 | SolidLaneChange | sub_tag: SolidLeft/SolidRight |
| 路口直行/左转/右转 | Intersection | sub_tag: IsIntersection/LeftIntersection/RightIntersection |
| **直行通过带红绿灯路口** | **topology_intersection** | **⚠ 不用Intersection+successor_count，详见 `references/intersection-straight.md`** |
| 交通灯路口 | TrafficIntersection | — |
| 上匝道/下匝道 | OnRamp / OffRamp | — |
| 环岛 | Roundabout | — |
| 坡道 | Slope | — |
| 下雨/降雨 | RainLevel / over_rain_level | sub_tag: heavy/light/moderate |
| 碰撞 | ObstacleCollision | — |
| 近碰 | ObstacleNearMiss | — |
| 借道避让 | AvoidanceBorrowLane | sub_tag区分左/右 |
| 猛打方向 | SteeringWheelSlam | — |
| 低速/巡航/急刹 | hard_braking / cruising / decelerating | SpeedState系列，互斥 |

### Step 2: 确定关联表

| 需求 | 表组合 | JOIN 方式 |
|------|--------|-----------|
| 只筛选标签 | range_tag | 无 JOIN |
| 标签 + 障碍物详情 | range_tag + dynamic_obj | `d.ts BETWEEN r.start_ts AND r.end_ts` |
| 标签 + 自车状态 | range_tag + ego | `e.ts BETWEEN r.start_ts AND r.end_ts` |
| 标签 + 道路拓扑 | range_tag + ego + static_link | 先 JOIN ego 拿 link_id，再 JOIN static_link |
| 标签 + 车道信息 | range_tag + ego + dynamic_lane/static_lane | 先 JOIN ego 拿 lane_id，再 JOIN lane |
| 障碍物子标签 | range_tag (仅) | `json_extract(r.param, '$.sub_tag')` |

### Step 3: SQL 模板

#### 模板 A: 纯标签筛选

```sql
SELECT bag_id, tag_name, start_ts, end_ts
FROM range_tag
WHERE tag_name = 'Cutin'
```

#### 模板 B: 标签 + 障碍物（最常用）

```sql
SELECT r.tag_name, r.start_ts, r.end_ts,
       d.obj_id, d.type, d.x, d.y,
       d.absolute_velocity_x, d.absolute_velocity_y
FROM range_tag r
JOIN dynamic_obj d ON d.ts BETWEEN r.start_ts AND r.end_ts
WHERE r.tag_name = 'Cutin'
```

#### 模板 C: 标签 + 子标签过滤

```sql
SELECT r.tag_name, r.start_ts, r.end_ts
FROM range_tag r
WHERE r.tag_name = 'Cutin'
  AND json_extract(r.param, '$.sub_tag') = 'CarCutin'
```

#### 模板 D: 标签 + 自车状态

```sql
SELECT r.tag_name, r.start_ts, r.end_ts,
       e.speed, e.traffic_light_status
FROM range_tag r
JOIN ego e ON e.ts BETWEEN r.start_ts AND r.end_ts
WHERE r.tag_name = 'RunRedLight'
  AND e.traffic_light_status = 3  -- 红灯
```

#### 模板 E: 标签 + 道路拓扑

```sql
SELECT r.tag_name, r.start_ts, r.end_ts,
       sl.link_type, sl.link_turn_type, sl.link_class
FROM range_tag r
JOIN ego e ON e.ts BETWEEN r.start_ts AND r.end_ts
JOIN static_link sl ON sl.link_id = e.ego_static_map_link_id
WHERE r.tag_name = 'OnRamp'
  AND sl.link_type = '入口匝道'
```

#### 模板 F: 标签 + 车道属性

```sql
SELECT r.tag_name, r.start_ts, r.end_ts,
       dl.lane_type, dl.lane_turn_type, dl.lane_trans_type
FROM range_tag r
JOIN ego e ON e.ts BETWEEN r.start_ts AND r.end_ts
JOIN dynamic_lane dl ON dl.ts = e.ts AND dl.lane_id = e.ego_lane_id
WHERE r.tag_name = 'LaneChange'
  AND dl.lane_trans_type = '分流'
```

#### 模板 G: 多标签组合（AND 语义）

```sql
-- 同时满足"加塞"和"近距跟车"的场景
SELECT a.bag_id
FROM range_tag a
JOIN range_tag b ON a.bag_id = b.bag_id
  AND a.start_ts <= b.end_ts AND a.end_ts >= b.start_ts
WHERE a.tag_name = 'Cutin'
  AND b.tag_name = 'CloseFollow'
```

#### 模板 H: 直行通过带红绿灯路口

⚠ `successor_count=1` 已废弃。详见 `references/intersection-straight.md`（含完整SQL、GT局限性、验证结果、全方案对比、successor_count为什么错的解释）。

核心逻辑：`topology_intersection` + 排除与 `INTERSECTION_LEFTTURN/RIGHTTURN` 时间重叠 + 要求有 `INTERSECTION_STRAIGHT` 重叠。

### Step 4: 验证 SQL

1. 先在单个 DB 上 `LIMIT 10` 验证语法
2. 检查 JOIN 是否产生笛卡尔积（range_tag×dynamic_obj 时间区间重叠可能放大行数）
3. 子标签用 `json_extract`，不要猜列名
4. 注意 timestamp 单位：`start_ts/end_ts/ts` 是**纳秒级 BIGINT**
5. **端到端测试**（调用平台 execute-sql API 验证）— 详见 `references/e2e-testing.md`

---

## 5. 关键注意事项

1. **DB 间 schema 不一致是常态** — 少数 DB 缺 `predecessors`/`successors`/`obs_dr_trajectory` 列，不是 SQL 错误
2. **云端标签 ≠ SQLite 标签** — `db_py_rule/` 下的标签是云端映射标签，SQLite 中不存在
3. **param 列是 JSON** — sub_tag 信息存储在 param 中，需 `json_extract()` 解析
4. **range_tag 是时间区间表** — JOIN 时用 `BETWEEN start_ts AND end_ts`，不是等值 JOIN
5. **ego 表无 tag_name 列** — 自车状态通过 ts 与 range_tag 做时间区间 JOIN
6. **timestamp 单位** — `start_ts/end_ts/ts` 为纳秒级 BIGINT，`ts_ms` 为毫秒 FLOAT
7. **fix_db_schema.py** — 新数据集测试前先运行此脚本修补缺列
8. **bag_id 不在表内** — 每个 DB 文件 = 一个 bag，bag_id 来自文件名，不在表列中
9. **⚠ successor_count=1 已废弃** — 详见 `references/intersection-straight.md`

---

## 6. 与 SceneSQL Agent 的关系

本 skill 侧重 **手写 SQL**（Agent 辅助人工写 SQL）。
SceneSQL 项目自身还有 **NL→SQL 自动流程**（Two-Round 架构）：

- Round 1: LLM 识别 concept → 从 `concept_groups.yaml` 路由
- Round 2: 组装 schema context → LLM 生成 SQL

两者共享同一套 schema 定义文件。手写 SQL 的优势在于复杂/多表 JOIN 场景可以精确控制；
NL→SQL 的优势在于简单/高频查询可以自动化。
