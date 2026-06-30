# SceneSQL 数据库 Schema 参考手册

> 自动驾驶场景挖掘 SQLite 数据库结构文档
> 基于schema_structure.yaml v2.0.0 自动生成
> 共 9 张表

## 目录

1. [range_tag](#range_tag) — 时间区间事件表
2. [dynamic_obj](#dynamic_obj) — 纵向时序表
3. [static_obj](#static_obj) — 纵向时序表
4. [ego](#ego) — 纵向时序表
5. [intersection_info](#intersection_info) — 时间区间事件表
6. [static_link](#static_link) — static
7. [dynamic_link](#dynamic_link) — 纵向时序表
8. [dynamic_lane](#dynamic_lane) — 纵向时序表
9. [static_lane](#static_lane) — static

## 表概览

| # | 表名 | 类型 | 列数 | 枚举列 | 描述 |
|---|------|------|------|--------|------|
| 1 | `range_tag` | 时间区间事件表 | 4 | 1 | 时间区间标签表——每个标签标记一个时间区间[start_ts |
| 2 | `dynamic_obj` | 纵向时序表 | 31 | 1 | 动态障碍物表——每个时间步的动态物体位置/速度/类型信息 |
| 3 | `static_obj` | 纵向时序表 | 12 | 1 | 静态障碍物表——每个时间步的静态物体位置/类型信息 |
| 4 | `ego` | 纵向时序表 | 42 | 4 | 自车状态表——每个时间步的自车速度/加速度/转向/信号灯等状 |
| 5 | `intersection_info` | 时间区间事件表 | 4 | 0 | 路口信息表——记录路口ID、车道数、自车车道索引、车道详情 |
| 6 | `static_link` | static | 9 | 4 | 静态道路链接表——道路拓扑链接的静态属性 |
| 7 | `dynamic_link` | 纵向时序表 | 8 | 2 | 动态道路链接表——随时间变化的道路拓扑链接属性 |
| 8 | `dynamic_lane` | 纵向时序表 | 11 | 3 | 动态车道表——随时间变化的车道属性 |
| 9 | `static_lane` | static | 1 | 0 | 静态车道表——车道的静态属性 |

## 表分类说明

| 类型 | 含义 | 时间特征 |
|------|------|----------|
| horizontal_event | 时间区间事件 | 每行=一个事件，start_ts/end_ts标记区间 |
| vertical_timeseries | 纵向时序 | 每行=一帧(10Hz)，时间戳秒级 |
| dynamic_ref | 动态参考 | 拓扑随实时变化 |
| static_ref | 静态参考 | 不随帧变化 |

## 时间戳约定

- 所有 `ts`/`start_ts`/`end_ts` 字段为**秒级 Unix 时间戳**（量级 ~1.78×10⁹）
- ego/dynamic_obj 等时序表使用 `ts`（每帧一个时间点）
- range_tag 使用 `start_ts`/`end_ts`（标记事件区间）
- JOIN 时直接比较：`ego.ts BETWEEN range_tag.start_ts AND range_tag.end_ts`
- **禁止** `*1e9` 或任何单位转换

---

## range_tag

**类型**: 时间区间事件表

**描述**: 时间区间标签表——每个标签标记一个时间区间[start_ts, end_ts]内的场景/行为

### 列定义

| 列名 | 类型 | 可空 | 主键 | 说明 |
|------|------|------|------|------|
| `start_ts` | BIGINT | ✓ | 🔑 | 标签生效开始时间戳（秒级） |
| `end_ts` | BIGINT | ✓ | 🔑 | 标签生效结束时间戳（秒级，≥start_ts） |
| `tag_name` | TEXT | ✓ | 🔑 | 标签名称，枚举 192 个值（见下方） |
| `param` | TEXT | ✓ |  | 标签参数（JSON格式存储） |

### tag_name 枚举值（192个）

#### 避障/绕行（12个）

| 标签名 | 中文描述 |
|--------|----------|
| `AVOIDANCE_INLANE` | — |
| `AVOIDANCE_INSAMELANE` | — |
| `AVOIDANCE_LANEYIELDBICYCLISTORTRICYCLIST` | — |
| `AVOIDANCE_LANEYIELDCARORTRUCK` | — |
| `AVOIDANCE_LANEYIELDCONE` | — |
| `AVOIDANCE_LANEYIELDCRUB` | — |
| `AVOIDANCE_LANEYIELDTRUCKINADJACENTLANE` | — |
| `AVOIDANCE_LANEYIELDVRU` | — |
| `AVOIDANCE_MULTIOBSTACLEAROUNDEGO` | 多障碍物绕行避让 |
| `AvoidanceBorrowLane` | 自车借用左右车道避让前方障碍物后返回原车道 |
| `Avoidance_InLane` | 自车在当前车道内避让前方障碍物 |
| `Avoidance_InLane_VLM` | — |

#### 巡航(CRUISE)（21个）

| 标签名 | 中文描述 |
|--------|----------|
| `CRUISE_CARORTRUCKCROSS` | — |
| `CRUISE_CARORTRUCKCUTIN` | — |
| `CRUISE_CARORTRUCKCUTOUT` | — |
| `CRUISE_CLOSECUTIN` | — |
| `CRUISE_CONGESTION` | — |
| `CRUISE_CREEP` | 蠕行巡航 |
| `CRUISE_CURVE` | 弯道巡航 |
| `CRUISE_CUTIN` | — |
| `CRUISE_CUTOUT` | — |
| `CRUISE_DISTANTFOLLOW` | — |
| `CRUISE_FOLLOW` | 跟行巡航 |
| `CRUISE_FREESPACEAVOIDANCE` | — |
| `CRUISE_FREESPACEYIELD` | — |
| `CRUISE_LEADCUTOUTLEADINGSTOPPED` | — |
| `CRUISE_RAMP` | 匝道巡航 |
| `CRUISE_SLOWFOLLOW` | 慢行跟车巡航 |
| `CRUISE_SLOWMOVE` | 慢行巡航 |
| `CRUISE_STRAIGHT` | 直道巡航 |
| `CRUISE_VRUCROSS` | — |
| `CRUISE_VRUCUTIN` | — |
| `CRUISE_VRUCUTOUT` | — |

#### 交叉口(INTERSECTION)（6个）

| 标签名 | 中文描述 |
|--------|----------|
| `INTERSECTION_LEFTTURN` | 路口左转 |
| `INTERSECTION_RIGHTTURN` | 路口右转 |
| `INTERSECTION_ROUNDABOUT` | — |
| `INTERSECTION_ROUNDABOUTUNSIGNAL` | — |
| `INTERSECTION_STRAIGHT` | 路口直行 |
| `INTERSECTION_UTURNLANE` | — |

#### 变道(LANECHANGE)（15个）

| 标签名 | 中文描述 |
|--------|----------|
| `LANECHANGE_ABANDON` | 放弃变道 |
| `LANECHANGE_AVOIDANCE` | — |
| `LANECHANGE_DIVERGELANECHANGERIGHTTURN` | — |
| `LANECHANGE_FREEWAY` | — |
| `LANECHANGE_MERGELANECHANGEDROP` | — |
| `LANECHANGE_MERGELANECHANGEMAINROAD` | — |
| `LANECHANGE_MERGELANECHANGEZIPPER` | — |
| `LANECHANGE_NAVIGATION` | — |
| `LANECHANGE_NAVLANECHANGECONGESTION` | — |
| `LANECHANGE_NAVLANECHANGERAMP` | — |
| `LANECHANGE_OVERTAKE` | — |
| `LANECHANGE_OVERTAKELEFTBICYCLISTORTRICYCLIST` | — |
| `LANECHANGE_OVERTAKELEFTCARORTRUCK` | — |
| `LANECHANGE_OVERTAKERIGHTBICYCLISTORTRICYCLIST` | — |
| `LANECHANGE_OVERTAKERIGHTCARORTRUCK` | — |

#### 红绿灯（5个）

| 标签名 | 中文描述 |
|--------|----------|
| `CrossStopLineOnYellowLight` | 车辆黄灯期间越过停止线 |
| `GreenLightNotProceeding` | 绿灯亮起后车辆未及时起步通行 |
| `RunFullYellowLight` | — |
| `RunRedLight` | 车辆闯红灯越过停止线 |
| `RunYellowLight` | — |

#### 起步停车(STOPANDGO)（10个）

| 标签名 | 中文描述 |
|--------|----------|
| `STOPANDGO_BRAKESTOPANTICRASHBUCKET` | — |
| `STOPANDGO_BRAKESTOPCONE` | — |
| `STOPANDGO_FIRSTCARSTARTATGREENLIGHT` | 红绿灯路口首车绿灯起步 |
| `STOPANDGO_FIRSTCARSTOPATREDLIGHT` | 红绿灯路口首车红灯停车 |
| `STOPANDGO_STARTCONGESTION` | — |
| `STOPANDGO_STARTFOLLOWOBSTACLE` | — |
| `STOPANDGO_STARTNONFOLLOWOBSTACLE` | — |
| `STOPANDGO_STARTRAMP` | — |
| `STOPANDGO_STOP` | — |
| `STOPANDGO_STOPBEHINDOBSTACLE` | — |

#### 交互（3个）

| 标签名 | 中文描述 |
|--------|----------|
| `InteractivePedestrian` | — |
| `InteractiveVRU` | — |
| `InteractiveVehicle` | — |

#### 横穿/冲突（5个）

| 标签名 | 中文描述 |
|--------|----------|
| `CrossConflict` | — |
| `CrossVRU` | — |
| `CrossVRUV1` | VRU（行人/骑行者）横穿自车路径 |
| `CrossVehicle` | 侧方车辆横穿自车路径，自车减速避让 |
| `VRUCrossConflict` | — |

#### 跟车（3个）

| 标签名 | 中文描述 |
|--------|----------|
| `CloseFollow` | 自车与前车近距离跟车（高碰撞风险） |
| `CongestedFollow` | 拥堵跟车 |
| `CrawlingFollow` | 极低速蠕行跟车（走走停停、车距极近） |

#### 加减速/速度（20个）

| 标签名 | 中文描述 |
|--------|----------|
| `HighSpeed` | — |
| `Jerk` | 自车急加速或急刹车（加加速度突变） |
| `LongitudinalAcceleration` | — |
| `LongitudinalDeceleration` | — |
| `LowSpeed` | — |
| `LowSpeedOvertake` | — |
| `LowerSpeed` | — |
| `MediumLowSpeed` | — |
| `MediumSpeed` | — |
| `NoSpeedIncrease` | 自车低于限速且不主动提速（不追限速） |
| `OverSpeedLimit` | 自车行驶速度超过道路限速 |
| `SpeedIncreaseStats` | — |
| `Static` | — |
| `accelerating` | 自车加速状态 |
| `creeping` | 自车蠕行状态（极低速移动） |
| `cruising` | 自车巡航状态 |
| `decelerating` | 自车减速状态 |
| `hard_braking` | 自车急刹车状态 |
| `reversing` | 自车倒车状态 |
| `stationary` | 自车静止状态 |

#### 转向（13个）

| 标签名 | 中文描述 |
|--------|----------|
| `HighSteeringWheelTorque` | 方向盘力矩过大 |
| `SteeringSmallSwing` | 方向盘小角度左右连续交替摆动 |
| `SteeringWheelSlam` | 自车猛打方向盘（短时高强度急转向） |
| `Turning` | 自车转向（左转 / 右转 / 掉头） |
| `steering_left_120_185` | 方向盘左转 120°~185° |
| `steering_left_15_60` | 方向盘左转 15°~60° |
| `steering_left_60_120` | 方向盘左转 60°~120° |
| `steering_left_above_185` | 方向盘左转超过 185° |
| `steering_right_120_185` | 方向盘右转 120°~185° |
| `steering_right_15_60` | 方向盘右转 15°~60° |
| `steering_right_60_120` | 方向盘右转 60°~120° |
| `steering_right_above_185` | 方向盘右转超过 185° |
| `steering_slightly` | 方向盘小角度转动 |

#### 车道偏离/对向（7个）

| 标签名 | 中文描述 |
|--------|----------|
| `NavLaneChangeLate` | — |
| `OppositeLaneDriving` | — |
| `OppositeLaneDrivingV1` | — |
| `OppositeLaneDrivingV2` | — |
| `SolidLaneChange` | 车辆在实线区域违规变道 |
| `WrongWay_Deviation` | — |
| `WrongwayNavi` | — |

#### 匝道（3个）

| 标签名 | 中文描述 |
|--------|----------|
| `OffRamp` | 自车行驶在高速 / 快速路下匝道 |
| `OnRamp` | 自车行驶在高速 / 快速路上匝道 |
| `OtherRamp` | 自车行驶在其他匝道路段 |

#### 导航（7个）

| 标签名 | 中文描述 |
|--------|----------|
| `navi_enter` | 导航进入 |
| `navi_exit` | 导航驶出 |
| `navi_keep` | 导航直行 |
| `navi_other` | 导航其他场景 |
| `navi_turn_left` | 导航左转 |
| `navi_turn_right` | 导航右转 |
| `navi_u_turn` | 导航掉头 |

#### 障碍物/碰撞（3个）

| 标签名 | 中文描述 |
|--------|----------|
| `ObstacleAvoidance` | — |
| `ObstacleCollision` | 自车与障碍物发生碰撞 |
| `ObstacleNearMiss` | 自车与障碍物发生近碰（未实际碰撞但距离极近） |

#### 其他（59个）

| 标签名 | 中文描述 |
|--------|----------|
| `AbnormalLaneChange` | 异常变道行为 |
| `AbnormalRightLaneChangeToRightestStop` | — |
| `AbnormalRightLaneChangeToRightestStopV1` | — |
| `ActiveWiperState` | 车辆雨刮器处于激活工作状态 |
| `BinCounter` | Bin 计数校验标签（产线专用） |
| `Cutin` | 他车切入自车车道前方 |
| `HighAccPedal` | — |
| `Intersection` | 自车处于路口区域（直行 / 左转 / 右转 / 掉头 / 不稳定路口） |
| `LaneChange` | 自车执行变道行为 |
| `LaneKeep` | 自车保持当前车道直行 |
| `LowTTC` | 自车与障碍物碰撞时间过低（高风险） |
| `NotCenter` | 自车未在车道中心行驶 |
| `OnRightestLane` | — |
| `RoadConstruction` | — |
| `Roundabout` | 自车行驶在环岛路段 |
| `Slope` | 车辆行驶在坡道路段 |
| `SlowFrontLaneChange` | — |
| `StraightDriving` | 自车直行超过 3 秒（测试标签） |
| `ToRoadsideStop` | — |
| `TrafficIntersection` | 自车处于交通灯控制的路口 |
| `TrafficLightAbnormal` | 交通灯信号状态异常 |
| `YieldToPedestrian` | — |
| `invalid_navi_guide_line_infos_empty` | — |
| `invalid_navi_not_in_sd` | — |
| `invalid_topology_ego_static_map_link_id_invalid` | — |
| `invalid_topology_ego_static_map_link_id_not_match` | — |
| `invalid_topology_no_map_schema` | — |
| `invalid_topology_other_no_match` | — |
| `over_rain_level` | 降雨状态（与 RainLevel 相同算子，但使用 over_rain_level 作为 tag_name） |
| `route_deviation_curb` | — |
| `route_deviation_guide_line` | — |
| `route_deviation_sd_newpath_need_action` | — |
| `route_deviation_sd_newpath_noneed_action` | — |
| `route_deviation_sd_yaw` | — |
| `topology_auxiliary` | — |
| `topology_bridge` | — |
| `topology_interchange` | — |
| `topology_interchange_roundabout` | — |
| `topology_intersection` | — |
| `topology_intersection_cross_road` | — |
| `topology_intersection_multi_fork` | — |
| `topology_intersection_other` | — |
| `topology_intersection_small_cross_road` | — |
| `topology_intersection_small_t_junction` | — |
| `topology_intersection_straight_intersection` | — |
| `topology_intersection_t_junction` | — |
| `topology_intersection_y_junction` | — |
| `topology_left_turn_lane` | — |
| `topology_main` | — |
| `topology_merge` | — |
| `topology_ramp` | — |
| `topology_right_turn_lane` | — |
| `topology_roundabout` | — |
| `topology_service_area` | — |
| `topology_split` | — |
| `topology_toll_station` | — |
| `topology_tunnel` | — |
| `topology_tunnel_roundabout` | — |
| `topology_unstructured_road` | — |

---

## dynamic_obj

**类型**: 纵向时序表

**描述**: 动态障碍物表——每个时间步的动态物体位置/速度/类型信息

### 列定义

| 列名 | 类型 | 可空 | 主键 | 说明 |
|------|------|------|------|------|
| `ts` | BIGINT | ✓ | 🔑 |  |
| `obj_id` | TEXT | ✓ | 🔑 |  |
| `obs_lane_id` | INTEGER | ✓ |  |  |
| `obs_static_map_link_id` | INTEGER | ✓ |  |  |
| `obs_hq_lane_id` | INTEGER | ✓ |  |  |
| `obs_to_left_boundary_dist` | FLOAT | ✓ |  |  |
| `obs_to_right_boundary_dist` | FLOAT | ✓ |  |  |
| `obs_corner_fl_2_left_boundary_dist` | FLOAT | ✓ |  |  |
| `obs_corner_fr_2_left_boundary_dist` | FLOAT | ✓ |  |  |
| `obs_corner_rl_2_left_boundary_dist` | FLOAT | ✓ |  |  |
| `obs_corner_rr_2_left_boundary_dist` | FLOAT | ✓ |  |  |
| `obs_corner_fl_2_right_boundary_dist` | FLOAT | ✓ |  |  |
| `obs_corner_fr_2_right_boundary_dist` | FLOAT | ✓ |  |  |
| `obs_corner_rl_2_right_boundary_dist` | FLOAT | ✓ |  |  |
| `obs_corner_rr_2_right_boundary_dist` | FLOAT | ✓ |  |  |
| `obs_to_centerline_dist` | FLOAT | ✓ |  |  |
| `obs_dr_trajectory` | TEXT | ✓ |  |  |
| `ts_ms` | FLOAT | ✓ |  |  |
| `x` | FLOAT | ✓ |  |  |
| `y` | FLOAT | ✓ |  |  |
| `z` | FLOAT | ✓ |  |  |
| `l` | FLOAT | ✓ |  |  |
| `w` | FLOAT | ✓ |  |  |
| `h` | FLOAT | ✓ |  |  |
| `heading` | FLOAT | ✓ |  |  |
| `type` | TEXT | ✓ |  | 枚举 11 个值（见下方） |
| `absolute_velocity_x` | FLOAT | ✓ |  |  |
| `absolute_velocity_y` | FLOAT | ✓ |  |  |
| `relative_velocity_x` | FLOAT | ✓ |  |  |
| `relative_velocity_y` | FLOAT | ✓ |  |  |
| `is_static` | BOOLEAN | ✓ |  |  |

### type 枚举值（11个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `animal` | 动物 |
| 2 | `bus` | 公交车 |
| 3 | `car` | 轿车 |
| 4 | `cyclist` | 骑行者 |
| 5 | `motorcycle` | 摩托车 |
| 6 | `motorcyclist` | 摩托车手 |
| 7 | `pedestrian` | 行人 |
| 8 | `stroller` | 婴儿车 |
| 9 | `suv` | SUV |
| 10 | `truck` | 卡车 |
| 11 | `wheelchair` | 轮椅 |

---

## static_obj

**类型**: 纵向时序表

**描述**: 静态障碍物表——每个时间步的静态物体位置/类型信息

### 列定义

| 列名 | 类型 | 可空 | 主键 | 说明 |
|------|------|------|------|------|
| `ts` | BIGINT | ✓ | 🔑 |  |
| `obj_id` | TEXT | ✓ | 🔑 |  |
| `ts_ms` | FLOAT | ✓ |  |  |
| `x` | FLOAT | ✓ |  |  |
| `y` | FLOAT | ✓ |  |  |
| `z` | FLOAT | ✓ |  |  |
| `l` | FLOAT | ✓ |  |  |
| `w` | FLOAT | ✓ |  |  |
| `h` | FLOAT | ✓ |  |  |
| `heading` | FLOAT | ✓ |  |  |
| `type` | TEXT | ✓ |  | 枚举 1 个值（见下方） |
| `param` | TEXT | ✓ |  |  |

### type 枚举值（1个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `traffic_warning_object` |  |

---

## ego

**类型**: 纵向时序表

**描述**: 自车状态表——每个时间步的自车速度/加速度/转向/信号灯等状态

### 列定义

| 列名 | 类型 | 可空 | 主键 | 说明 |
|------|------|------|------|------|
| `ts` | BIGINT | ✓ | 🔑 | 时间戳(秒) |
| `acc_magnitude` | FLOAT | ✓ |  |  |
| `ego_lane_id` | INTEGER | ✓ |  |  |
| `ego_link_id` | INTEGER | ✓ |  |  |
| `ego_static_map_link_id` | INTEGER | ✓ |  |  |
| `ego_hq_lane_id` | INTEGER | ✓ |  |  |
| `ego_lane_curvature` | FLOAT | ✓ |  |  |
| `ego_to_centerline_dist` | FLOAT | ✓ |  |  |
| `ego_to_left_boundary_dist` | FLOAT | ✓ |  |  |
| `ego_to_right_boundary_dist` | FLOAT | ✓ |  |  |
| `ego_corner_fl_2_left_boundary_dist` | FLOAT | ✓ |  |  |
| `ego_corner_fr_2_left_boundary_dist` | FLOAT | ✓ |  |  |
| `ego_corner_rl_2_left_boundary_dist` | FLOAT | ✓ |  |  |
| `ego_corner_rr_2_left_boundary_dist` | FLOAT | ✓ |  |  |
| `ego_corner_fl_2_right_boundary_dist` | FLOAT | ✓ |  |  |
| `ego_corner_fr_2_right_boundary_dist` | FLOAT | ✓ |  |  |
| `ego_corner_rl_2_right_boundary_dist` | FLOAT | ✓ |  |  |
| `ego_corner_rr_2_right_boundary_dist` | FLOAT | ✓ |  |  |
| `traffic_light_status` | INTEGER | ✓ |  | 枚举 4 个值（见下方） |
| `ego_hq_lane_ids_on_cross_section` | TEXT | ✓ |  |  |
| `ego_lane_index_on_hq_cross_section` | INTEGER | ✓ |  |  |
| `ego_lane_successor_count` | INTEGER | ✓ |  |  |
| `ego_lane_predecessor_count` | INTEGER | ✓ |  |  |
| `ego_lane_width` | FLOAT | ✓ |  |  |
| `ego_dr_trajectory` | TEXT | ✓ |  |  |
| `ts_ms` | FLOAT | ✓ |  | 时间戳(毫秒) |
| `speed` | FLOAT | ✓ |  | 车速(m/s) |
| `steering_angle` | FLOAT | ✓ |  | 方向盘转角(rad) |
| `latest_traffic_light_status` | TEXT | ✓ |  | 枚举 4 个值（见下方） |
| `latest_stop_line_direction` | FLOAT | ✓ |  |  |
| `navigation_status` | TEXT | ✓ |  | 枚举 1 个值（见下方） |
| `wiper_status` | TEXT | ✓ |  | 枚举 2 个值（见下方） |
| `indicator_status` | TEXT | ✓ |  |  |
| `cumulative_distance` | FLOAT | ✓ |  |  |
| `utm_x` | FLOAT | ✓ |  |  |
| `utm_y` | FLOAT | ✓ |  |  |
| `utm_yaw` | FLOAT | ✓ |  |  |
| `gl_dis_to_left` | FLOAT | ✓ |  |  |
| `gl_dis_to_right` | FLOAT | ✓ |  |  |
| `gl_lane_cnt` | INTEGER | ✓ |  |  |
| `gl_left_cnt` | INTEGER | ✓ |  |  |
| `gl_right_cnt` | INTEGER | ✓ |  |  |

### traffic_light_status 枚举值（4个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `-1` |  |
| 2 | `1` |  |
| 3 | `2` |  |
| 4 | `3` |  |

### latest_traffic_light_status 枚举值（4个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `绿` |  |
| 2 | `黄` |  |
| 3 | `红` |  |
| 4 | `未知` |  |

### navigation_status 枚举值（1个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `导航中` |  |

### wiper_status 枚举值（2个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `关闭` |  |
| 2 | `间歇档` |  |

---

## intersection_info

**类型**: 时间区间事件表

**描述**: 路口信息表——记录路口ID、车道数、自车车道索引、车道详情

### 列定义

| 列名 | 类型 | 可空 | 主键 | 说明 |
|------|------|------|------|------|
| `intersection_id` | TEXT | ✓ |  |  |
| `lane_count` | BIGINT | ✓ |  |  |
| `ego_lane_index` | BIGINT | ✓ |  |  |
| `lane_info` | TEXT | ✓ |  |  |

---

## static_link

**类型**: static

**描述**: 静态道路链接表——道路拓扑链接的静态属性

### 列定义

| 列名 | 类型 | 可空 | 主键 | 说明 |
|------|------|------|------|------|
| `link_id` | TEXT | ✓ | 🔑 |  |
| `link_type` | TEXT | ✓ |  | 枚举 6 个值（见下方） |
| `link_turn_type` | TEXT | ✓ |  | 枚举 8 个值（见下方） |
| `link_class` | TEXT | ✓ |  | 枚举 6 个值（见下方） |
| `link_attribute` | TEXT | ✓ |  | 枚举 3 个值（见下方） |
| `link_speed_limit` | FLOAT | ✓ |  |  |
| `link_predecessor` | TEXT | ✓ |  |  |
| `link_successor` | TEXT | ✓ |  |  |
| `link_exp_speed_limit` | FLOAT | ✓ |  |  |

### link_type 枚举值（6个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `主干道` |  |
| 2 | `主路` |  |
| 3 | `入口匝道` |  |
| 4 | `匝道` |  |
| 5 | `路口` |  |
| 6 | `辅路` |  |

### link_turn_type 枚举值（8个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `右转` |  |
| 2 | `左转` |  |
| 3 | `掉头` |  |
| 4 | `直行` |  |
| 5 | `直行右转` |  |
| 6 | `路口` |  |
| 7 | `进入` |  |
| 8 | `退出` |  |

### link_class 枚举值（6个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `主要大街/城市快速道` |  |
| 2 | `主要道路` |  |
| 3 | `国道` |  |
| 4 | `次要道路` |  |
| 5 | `省道` |  |
| 6 | `高速公路` |  |

### link_attribute 枚举值（3个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `无特殊属性` |  |
| 2 | `隧道` |  |
| 3 | `其他` |  |

---

## dynamic_link

**类型**: 纵向时序表

**描述**: 动态道路链接表——随时间变化的道路拓扑链接属性

### 列定义

| 列名 | 类型 | 可空 | 主键 | 说明 |
|------|------|------|------|------|
| `ts` | BIGINT | ✓ | 🔑 |  |
| `link_id` | TEXT | ✓ | 🔑 |  |
| `link_type` | TEXT | ✓ |  | 枚举 6 个值（见下方） |
| `link_attribute` | TEXT | ✓ |  | 枚举 3 个值（见下方） |
| `link_exp_speed_limit` | FLOAT | ✓ |  |  |
| `link_predecessor` | TEXT | ✓ |  |  |
| `link_successor` | TEXT | ✓ |  |  |
| `include_lane_ids` | TEXT | ✓ |  |  |

### link_type 枚举值（6个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `主干道` |  |
| 2 | `主路` |  |
| 3 | `入口匝道` |  |
| 4 | `匝道` |  |
| 5 | `路口` |  |
| 6 | `辅路` |  |

### link_attribute 枚举值（3个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `无特殊属性` |  |
| 2 | `隧道` |  |
| 3 | `其他` |  |

---

## dynamic_lane

**类型**: 纵向时序表

**描述**: 动态车道表——随时间变化的车道属性

### 列定义

| 列名 | 类型 | 可空 | 主键 | 说明 |
|------|------|------|------|------|
| `ts` | BIGINT | ✓ | 🔑 |  |
| `lane_id` | TEXT | ✓ | 🔑 |  |
| `ref_link_id` | INTEGER | ✓ |  |  |
| `left_boundary_id` | INTEGER | ✓ |  |  |
| `right_boundary_id` | INTEGER | ✓ |  |  |
| `lane_type` | TEXT | ✓ |  | 枚举 2 个值（见下方） |
| `lane_turn_type` | TEXT | ✓ |  | 枚举 7 个值（见下方） |
| `lane_trans_type` | TEXT | ✓ |  | 枚举 6 个值（见下方） |
| `predecessors` | TEXT | ✓ |  |  |
| `successors` | TEXT | ✓ |  |  |
| `lane_relate_obs_ids` | TEXT | ✓ |  |  |

### lane_type 枚举值（2个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `LANE_TYPE_EMERGENCY` |  |
| 2 | `未知` |  |

### lane_turn_type 枚举值（7个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `TURN_TYPE_AHEADRIGHT` |  |
| 2 | `TURN_TYPE_LEFTLUTURN` |  |
| 3 | `右转` |  |
| 4 | `左转` |  |
| 5 | `掉头` |  |
| 6 | `直行` |  |
| 7 | `路口` |  |

### lane_trans_type 枚举值（6个）

| # | 值 | 描述 |
|---|-----|------|
| 1 | `TRANS_TYPE_Ending` |  |
| 2 | `TRANS_TYPE_FullConnect` |  |
| 3 | `TRANS_TYPE_LeftConnect` |  |
| 4 | `TRANS_TYPE_OneSplitTwo` |  |
| 5 | `TRANS_TYPE_RightConnect` |  |
| 6 | `TRANS_TYPE_UNDEFINED` |  |

---

## static_lane

**类型**: static

**描述**: 静态车道表——车道的静态属性

### 列定义

| 列名 | 类型 | 可空 | 主键 | 说明 |
|------|------|------|------|------|
| `lane_id` | TEXT | ✓ | 🔑 |  |

---

## 常用 JOIN 模式

### range_tag × ego（标签+自车状态）
```sql
SELECT r.tag_name, r.start_ts, r.end_ts, e.ts, e.speed
FROM range_tag r
JOIN ego e ON e.ts BETWEEN r.start_ts AND r.end_ts
WHERE r.tag_name = 'Cutin'
```

### range_tag × dynamic_obj（标签+动态障碍物）
```sql
SELECT r.tag_name, d.ts, d.type, d.x, d.y
FROM range_tag r
JOIN dynamic_obj d ON d.ts BETWEEN r.start_ts AND r.end_ts
WHERE r.tag_name = 'Cutin' AND d.type = 'car'
```

### range_tag 自连接（多标签时间交叉）
```sql
SELECT DISTINCT r1.bag_id FROM range_tag r1
JOIN range_tag r2 ON r1.bag_id = r2.bag_id
  AND r1.start_ts < r2.end_ts AND r1.end_ts > r2.start_ts
WHERE r1.tag_name = 'Intersection' AND r2.tag_name = 'GreenLightNotProceeding'
```
