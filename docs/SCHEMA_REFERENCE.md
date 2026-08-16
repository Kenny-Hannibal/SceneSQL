# SceneSQL Schema Reference

> SceneSQL SQLite数据库完整Schema参考文档  
> 自动生成时间: 2026-06-30

## 目录

- [range_tag](#range_tag)

- [dynamic_obj](#dynamic_obj)

- [static_obj](#static_obj)

- [ego](#ego)

- [intersection_info](#intersection_info)

- [static_link](#static_link)

- [dynamic_link](#dynamic_link)

- [dynamic_lane](#dynamic_lane)

- [static_lane](#static_lane)


---

## range_tag

时间区间标签表——每个标签标记一个时间区间`[start_ts, end_ts]`内的场景/行为

### 列定义

| 列名 | 类型 | 说明 |
|------|------|------|
| `start_ts` | INTEGER | 标签生效开始时间（秒级Unix时间戳） |
| `end_ts` | INTEGER | 标签生效结束时间（秒级Unix时间戳，≥start_ts） |
| `tag_name` | TEXT | 标签名称（枚举值，见下方列表） |
| `param` | TEXT | 标签参数（JSON格式，含sub_tag等详细信息） |

### `tag_name` 合法枚举值

| # | tag_name | 中文翻译 | 来源 |
|---|----------|----------|------|
| 1 | `AVOIDANCE_INLANE` | 本车道内避让 | 车端 |
| 2 | `AVOIDANCE_INSAMELANE` | 同车道内避让 | 车端 |
| 3 | `AVOIDANCE_LANEYIELDBICYCLISTORTRICYCLIST` | 车道内让行自行车/三轮车 | 车端 |
| 4 | `AVOIDANCE_LANEYIELDCARORTRUCK` | 车道内让行轿车/卡车 | 车端 |
| 5 | `AVOIDANCE_LANEYIELDCONE` | 车道内让行锥桶 | 车端 |
| 6 | `AVOIDANCE_LANEYIELDTRUCKINADJACENTLANE` | 车道内让行相邻车道卡车 | 车端 |
| 7 | `AVOIDANCE_LANEYIELDVRU` | 车道内让行弱势道路用户 | 车端 |
| 8 | `AVOIDANCE_MULTIOBSTACLEAROUNDEGO` | 多障碍物绕行避让 | 车端 |
| 9 | `AbnormalLaneChange` | 异常变道行为 | `L2_Pred/.../activity_new/op_abnormal_lane_change.py` |
| 10 | `ActiveWiperState` | 车辆雨刮器处于激活工作状态 | `L2_Pred/.../activity_new/op_wiper.py` |
| 11 | `AvoidanceBorrowLane` | 自车借用左右车道避让前方障碍物后返回原车道 | `L2_Pred/.../activity_new/op_avoidance_borrow_lane.py` |
| 12 | `BinCounter` | Bin 计数校验标签（产线专用） | `L2_Pred/.../activity_new/op_bin_counter.py` |
| 13 | `CRUISE_CARORTRUCKCROSS` | 巡航中轿车/卡车横穿 | 车端 |
| 14 | `CRUISE_CARORTRUCKCUTIN` | 巡航中轿车/卡车切入 | 车端 |
| 15 | `CRUISE_CARORTRUCKCUTOUT` | 巡航中轿车/卡车切出 | 车端 |
| 16 | `CRUISE_CLOSECUTIN` | 巡航中紧密切入 | 车端 |
| 17 | `CRUISE_CONGESTION` | 巡航中拥堵 | 车端 |
| 18 | `CRUISE_CREEP` | 蠕行巡航 | 车端 |
| 19 | `CRUISE_CURVE` | 弯道巡航 | 车端 |
| 20 | `CRUISE_CUTIN` | 巡航中切入 | 车端 |
| 21 | `CRUISE_CUTOUT` | 巡航中切出 | 车端 |
| 22 | `CRUISE_DISTANTFOLLOW` | 巡航中远距跟随 | 车端 |
| 23 | `CRUISE_FOLLOW` | 跟行巡航 | 车端 |
| 24 | `CRUISE_FREESPACEAVOIDANCE` | 巡航中自由空间避让 | 车端 |
| 25 | `CRUISE_FREESPACEYIELD` | 巡航中自由空间让行 | 车端 |
| 26 | `CRUISE_LEADCUTOUTLEADINGSTOPPED` | 巡航中前车切出后前车静止 | 车端 |
| 27 | `CRUISE_RAMP` | 匝道巡航 | 车端 |
| 28 | `CRUISE_SLOWFOLLOW` | 慢行跟车巡航 | 车端 |
| 29 | `CRUISE_SLOWMOVE` | 慢行巡航 | 车端 |
| 30 | `CRUISE_STRAIGHT` | 直道巡航 | 车端 |
| 31 | `CRUISE_VRUCROSS` | 巡航中VRU横穿 | 车端 |
| 32 | `CRUISE_VRUCUTIN` | 巡航中VRU切入 | 车端 |
| 33 | `CRUISE_VRUCUTOUT` | 巡航中VRU切出 | 车端 |
| 34 | `CloseFollow` | 自车与前车近距离跟车（高碰撞风险） | `L2_Pred/.../activity_new/op_close_follow.py` |
| 35 | `CongestedFollow` | 拥堵跟车 | `L2_Pred/.../activity_new/past_op/valid/op_congested_follow.py` |
| 36 | `CrossConflict` | ⚠ 待补充 | `L2_Pred/.../activity_new/op_cross_conflict.py` |
| 37 | `CrossStopLineOnYellowLight` | 车辆黄灯期间越过停止线 | `L2_Pred/.../activity_new/op_traffic_light.py` |
| 38 | `CrossVRU` | VRU横穿 | `L2_Pred/.../activity_new/op_cross_vru.py` |
| 39 | `CrossVehicle` | 侧方车辆横穿自车路径，自车减速避让 | `L2_Pred/.../activity_new/op_cross_vehicle.py` |
| 40 | `Cutin` | 他车切入自车车道前方 | `L2_Pred/.../activity_new/op_cutin.py` |
| 41 | `GreenLightNotProceeding` | 绿灯亮起后车辆未及时起步通行 | `L2_Pred/.../activity_new/op_traffic_light.py` |
| 42 | `HighAccPedal` | 高加速踏板 | `L2_Pred/.../activity_new/op_high_acc_pedal.py` |
| 43 | `HighSteeringWheelTorque` | 方向盘力矩过大 | `L2_Pred/.../activity_new/op_high_steering_torque.py` |
| 44 | `INTERSECTION_LEFTTURN` | 路口左转 | 车端 |
| 45 | `INTERSECTION_RIGHTTURN` | 路口右转 | 车端 |
| 46 | `INTERSECTION_ROUNDABOUT` | 环岛 | 车端 |
| 47 | `INTERSECTION_ROUNDABOUTUNSIGNAL` | 无信号灯环岛 | 车端 |
| 48 | `INTERSECTION_STRAIGHT` | 路口直行 | 车端 |
| 49 | `INTERSECTION_UTURNLANE` | 掉头车道 | 车端 |
| 50 | `InteractivePedestrian` | ⚠ 待补充 | `L2_Pred/.../activity_new/op_interactive_pedestrian.py` |
| 51 | `InteractiveVRU` | ⚠ 待补充 | `L2_Pred/.../activity_new/op_interactive_vru.py` |
| 52 | `InteractiveVehicle` | ⚠ 待补充 | `L2_Pred/.../activity_new/op_interactive_vehicle.py` |
| 53 | `Intersection` | 自车处于路口区域（直行 / 左转 / 右转 / 掉头 / 不稳定路口） | `L2_Pred/.../activity_new/op_intersection.py` |
| 54 | `Jerk` | 自车急加速或急刹车（加加速度突变） | `L2_Pred/.../activity_new/op_jerk.py` |
| 55 | `LANECHANGE_ABANDON` | 放弃变道 | 车端 |
| 56 | `LANECHANGE_AVOIDANCE` | 避让变道 | 车端 |
| 57 | `LANECHANGE_DIVERGELANECHANGERIGHTTURN` | 分流变道右转 | 车端 |
| 58 | `LANECHANGE_FREEWAY` | 高速变道 | 车端 |
| 59 | `LANECHANGE_MERGELANECHANGEDROP` | 合流变道驶出 | 车端 |
| 60 | `LANECHANGE_MERGELANECHANGEMAINROAD` | 合流变道进入主路 | 车端 |
| 61 | `LANECHANGE_MERGELANECHANGEZIPPER` | 拉链式合流变道 | 车端 |
| 62 | `LANECHANGE_NAVIGATION` | 导航变道 | 车端 |
| 63 | `LANECHANGE_NAVLANECHANGECONGESTION` | 导航变道拥堵 | 车端 |
| 64 | `LANECHANGE_NAVLANECHANGERAMP` | 导航变道匝道 | 车端 |
| 65 | `LANECHANGE_OVERTAKE` | 变道超车 | 车端 |
| 66 | `LANECHANGE_OVERTAKELEFTBICYCLISTORTRICYCLIST` | 左变道超自行车/三轮车 | 车端 |
| 67 | `LANECHANGE_OVERTAKELEFTCARORTRUCK` | 左变道超轿车/卡车 | 车端 |
| 68 | `LANECHANGE_OVERTAKERIGHTBICYCLISTORTRICYCLIST` | 右变道超自行车/三轮车 | 车端 |
| 69 | `LANECHANGE_OVERTAKERIGHTCARORTRUCK` | 右变道超轿车/卡车 | 车端 |
| 70 | `LaneChange` | 自车执行变道行为 | `L2_Pred/.../activity_new/op_lane_change.py` |
| 71 | `LaneKeep` | 自车保持当前车道直行 | `L2_Pred/.../activity_new/op_lane_keep.py` |
| 72 | `LowTTC` | 自车与障碍物碰撞时间过低（高风险） | `L2_Pred/.../activity_new/op_low_ttc.py` |
| 73 | `NoSpeedIncrease` | 自车低于限速且不主动提速（不追限速） | `L2_Pred/.../activity_new/op_speed_limit/op_no_speed_increase.py` |
| 74 | `NotCenter` | 自车未在车道中心行驶 | `L2_Pred/.../activity_new/op_not_center.py` |
| 75 | `ObstacleAvoidance` | 障碍物避让 | `L2_Pred/.../activity_new/op_obstacle_avoidance.py` |
| 76 | `ObstacleCollision` | 自车与障碍物发生碰撞 | `L2_Pred/.../activity_new/op_obstacle_collision.py` |
| 77 | `ObstacleNearMiss` | 自车与障碍物发生近碰（未实际碰撞但距离极近） | `L2_Pred/.../activity_new/op_obstacle_near_miss.py` |
| 78 | `OnRamp` | 自车行驶在高速 / 快速路上匝道 | `L2_Pred/.../activity_new/op_on_ramp.py` |
| 79 | `OppositeLaneDriving` | 逆行 | `L2_Pred/.../activity_new/op_opposite_lane_driving.py` |
| 80 | `OtherRamp` | 自车行驶在其他匝道路段 | `L2_Pred/.../activity_new/op_other_ramp.py` |
| 81 | `OverSpeedLimit` | 自车行驶速度超过道路限速 | `L2_Pred/.../activity_new/op_speed_limit/op_over_speed.py` |
| 82 | `RunFullYellowLight` | ⚠ 待补充 | `L2_Pred/.../activity_new/op_traffic_light.py` |
| 83 | `RunRedLight` | 车辆闯红灯越过停止线 | `L2_Pred/.../activity_new/op_traffic_light.py` |
| 84 | `RunYellowLight` | ⚠ 待补充 | `L2_Pred/.../activity_new/op_traffic_light.py` |
| 85 | `STOPANDGO_BRAKESTOPANTICRASHBUCKET` | 刹停防撞桶 | 车端 |
| 86 | `STOPANDGO_BRAKESTOPCONE` | 刹停锥桶 | 车端 |
| 87 | `STOPANDGO_FIRSTCARSTARTATGREENLIGHT` | 红绿灯路口首车绿灯起步 | 车端 |
| 88 | `STOPANDGO_FIRSTCARSTOPATREDLIGHT` | 红绿灯路口首车红灯停车 | 车端 |
| 89 | `STOPANDGO_STARTCONGESTION` | 拥堵起步 | 车端 |
| 90 | `STOPANDGO_STARTFOLLOWOBSTACLE` | 跟随障碍物起步 | 车端 |
| 91 | `STOPANDGO_STARTNONFOLLOWOBSTACLE` | 非跟随障碍物起步 | 车端 |
| 92 | `STOPANDGO_STARTRAMP` | 匝道起步 | 车端 |
| 93 | `STOPANDGO_STOP` | 停车 | 车端 |
| 94 | `STOPANDGO_STOPBEHINDOBSTACLE` | 障碍物后停车 | 车端 |
| 95 | `Slope` | 车辆行驶在坡道路段 | `L2_Pred/.../activity_new/op_slope.py` |
| 96 | `SolidLaneChange` | 车辆在实线区域违规变道 | `L2_Pred/.../activity_new/op_solid_lane_change.py` |
| 97 | `SpeedIncreaseStats` | 加速统计 | `L2_Pred/.../activity_new/op_speed_limit/op_speed_increase_stats.py` |
| 98 | `SteeringSmallSwing` | 方向盘小角度左右连续交替摆动 | `L2_Pred/.../activity_new/op_steering_small_swing.py` |
| 99 | `SteeringWheelSlam` | 自车猛打方向盘（短时高强度急转向） | `L2_Pred/.../activity_new/op_steering_wheel_slam.py` |
| 100 | `StraightDriving` | 自车直行超过 3 秒（测试标签） | `L2_Pred/.../activity_new/op_straight_driving.py` |
| 101 | `ToRoadsideStop` | 靠边停车 | `L2_Pred/.../activity_new/op_to_roadside_stop.py` |
| 102 | `TrafficIntersection` | 自车处于交通灯控制的路口 | `L2_Pred/.../activity_new/op_traffic_intersection.py` |
| 103 | `TrafficLightAbnormal` | 交通灯信号状态异常 | `L2_Pred/.../activity_new/op_traffic_light.py` |
| 104 | `Turning` | 自车转向（左转 / 右转 / 掉头） | `L2_Pred/.../activity_new/op_turning.py` |
| 105 | `VRUCrossConflict` | ⚠ 待补充 | `L2_Pred/.../activity_new/op_vru_cross_conflict.py` |
| 106 | `YieldToPedestrian` | 让行行人 | `L2_Pred/.../activity_new/op_yield_to_pedestrian.py` |
| 107 | `accelerating` | 自车加速状态 | `L2_Pred/.../feature_op/ego_motion_feature.py` |
| 108 | `creeping` | 自车蠕行状态（极低速移动） | `L2_Pred/.../feature_op/ego_motion_feature.py` |
| 109 | `cruising` | 自车巡航状态 | `L2_Pred/.../feature_op/ego_motion_feature.py` |
| 110 | `decelerating` | 自车减速状态 | `L2_Pred/.../feature_op/ego_motion_feature.py` |
| 111 | `hard_braking` | 自车急刹车状态 | `L2_Pred/.../feature_op/ego_motion_feature.py` |
| 112 | `invalid_navi_guide_line_infos_empty` | 无效导航(引导线信息为空) | `user_workspace/xiangchenming/op_navi_command.py` |
| 113 | `invalid_navi_not_in_sd` | 无效导航(不在SD地图中) | `user_workspace/xiangchenming/op_navi_command.py` |
| 114 | `invalid_topology_ego_static_map_link_id_invalid` | 无效拓扑(静态地图link_id无效) | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 115 | `invalid_topology_ego_static_map_link_id_not_match` | 无效拓扑(静态地图link_id不匹配) | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 116 | `invalid_topology_no_map_schema` | 无效拓扑(无地图方案) | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 117 | `invalid_topology_other_no_match` | 无效拓扑(其他不匹配) | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 118 | `navi_enter` | 导航进入 | `user_workspace/xiangchenming/op_navi_command.py` |
| 119 | `navi_exit` | 导航驶出 | `user_workspace/xiangchenming/op_navi_command.py` |
| 120 | `navi_keep` | 导航直行 | `user_workspace/xiangchenming/op_navi_command.py` |
| 121 | `navi_other` | 导航其他场景 | `user_workspace/xiangchenming/op_navi_command.py` |
| 122 | `navi_turn_left` | 导航左转 | `user_workspace/xiangchenming/op_navi_command.py` |
| 123 | `navi_turn_right` | 导航右转 | `user_workspace/xiangchenming/op_navi_command.py` |
| 124 | `navi_u_turn` | 导航掉头 | `user_workspace/xiangchenming/op_navi_command.py` |
| 125 | `over_rain_level` | 降雨状态（与 RainLevel 相同算子，但使用 over_rain_level 作为 tag_name） | `L2_Pred/.../feature_op/weather_feature.py` |
| 126 | `reversing` | 自车倒车状态 | `L2_Pred/.../feature_op/ego_motion_feature.py` |
| 127 | `route_deviation_curb` | 偏离路缘 | `L2_Pred/.../feature_op/route_deviation_feature.py` |
| 128 | `route_deviation_guide_line` | 偏离导流线 | `L2_Pred/.../feature_op/route_deviation_feature.py` |
| 129 | `route_deviation_sd_newpath_need_action` | 偏离需接管 | `L2_Pred/.../feature_op/route_deviation_feature.py` |
| 130 | `route_deviation_sd_newpath_noneed_action` | 偏离无需接管 | `L2_Pred/.../feature_op/route_deviation_feature.py` |
| 131 | `route_deviation_sd_yaw` | 偏离航向 | `L2_Pred/.../feature_op/route_deviation_feature.py` |
| 132 | `stationary` | 自车静止状态 | `L2_Pred/.../feature_op/ego_motion_feature.py` |
| 133 | `steering_left_120_185` | 方向盘左转 120°~185° | `L2_Pred/.../feature_op/steering_feature.py` |
| 134 | `steering_left_15_60` | 方向盘左转 15°~60° | `L2_Pred/.../feature_op/steering_feature.py` |
| 135 | `steering_left_60_120` | 方向盘左转 60°~120° | `L2_Pred/.../feature_op/steering_feature.py` |
| 136 | `steering_left_above_185` | 方向盘左转超过 185° | `L2_Pred/.../feature_op/steering_feature.py` |
| 137 | `steering_right_120_185` | 方向盘右转 120°~185° | `L2_Pred/.../feature_op/steering_feature.py` |
| 138 | `steering_right_15_60` | 方向盘右转 15°~60° | `L2_Pred/.../feature_op/steering_feature.py` |
| 139 | `steering_right_60_120` | 方向盘右转 60°~120° | `L2_Pred/.../feature_op/steering_feature.py` |
| 140 | `steering_slightly` | 方向盘小角度转动 | `L2_Pred/.../feature_op/steering_feature.py` |
| 141 | `topology_auxiliary` | 辅路 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 142 | `topology_bridge` | 桥梁 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 143 | `topology_interchange` | 立交 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 144 | `topology_interchange_roundabout` | 立交环岛 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 145 | `topology_intersection` | 路口 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 146 | `topology_intersection_cross_road` | 十字路口 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 147 | `topology_intersection_multi_fork` | 多岔路口 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 148 | `topology_intersection_other` | 其他路口 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 149 | `topology_intersection_small_cross_road` | 小十字路口 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 150 | `topology_intersection_small_t_junction` | 小T型路口 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 151 | `topology_intersection_straight_intersection` | 直行路口 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 152 | `topology_intersection_t_junction` | T型路口 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 153 | `topology_intersection_y_junction` | Y型路口 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 154 | `topology_left_turn_lane` | 左转车道 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 155 | `topology_main` | 主路 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 156 | `topology_merge` | 合流 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 157 | `topology_ramp` | 匝道 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 158 | `topology_right_turn_lane` | 右转车道 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 159 | `topology_roundabout` | 环岛 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 160 | `topology_service_area` | 服务区 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 161 | `topology_split` | 分流 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 162 | `topology_toll_station` | 收费站 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 163 | `topology_tunnel` | 隧道 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 164 | `topology_tunnel_roundabout` | 隧道环岛 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |
| 165 | `topology_unstructured_road` | 非结构化道路 | `L2_Pred/.../feature_op/topology_constraint_feature.py` |

---

## dynamic_obj

动态障碍物表——每个时间步的动态物体位置/速度/类型

### 列定义

| 列名 | 类型 | 说明 |
|------|------|------|
| `ts` | INTEGER | 时间戳 |
| `obj_id` | INTEGER | 障碍物唯一ID |
| `type` | TEXT | 障碍物类型（枚举值） |
| `x/y/z` | REAL | 位置（UTM坐标） |
| `l/w/h` | REAL | 长宽高 |
| `heading` | REAL | 朝向角 |
| `absolute_velocity_x/y` | REAL | 绝对速度分量 |
| `relative_velocity_x/y` | REAL | 相对自车速度分量 |
| `obs_dr_trajectory` | TEXT | DR轨迹（JSON） |
| `obs_lane_id` | TEXT | 所在车道ID |
| `obs_to_left/right_boundary_dist` | REAL | 到左右边界距离 |
| `is_static` | INTEGER | 是否静止 |

### `type` 合法枚举值

| # | type | 中文翻译 |
|---|------|----------|
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

静态障碍物表——每个时间步的静态物体位置/类型

### 列定义

| 列名 | 类型 | 说明 |
|------|------|------|
| `ts` | INTEGER | 时间戳 |
| `obj_id` | INTEGER | 障碍物唯一ID |
| `type` | TEXT | 障碍物类型（枚举值） |
| `x/y/z` | REAL | 位置 |
| `l/w/h` | REAL | 长宽高 |
| `heading` | REAL | 朝向角 |
| `param` | TEXT | 附加参数（JSON） |

### `type` 合法枚举值

| # | type | 中文翻译 |
|---|------|----------|
| 1 | `traffic_warning_object` | 交通警示物 |
| 2 | `未知` | 未知 |

---

## ego

自车状态表——每个时间步的自车速度/加速度/转向/信号灯等状态。**注意：ego表的各分类列（非tag_name）是车端传感器/导航直接上报的值，不是算子产出的标签。**

### 列定义

| 列名 | 类型 | 说明 |
|------|------|------|
| `ts` | INTEGER | 时间戳 |
| `speed` | REAL | 自车速度 |
| `steering_angle` | REAL | 方向盘转角 |
| `acc_magnitude` | REAL | 加速度幅值 |
| `utm_x/utm_y/utm_yaw` | REAL | UTM坐标和航向 |
| `traffic_light_status` | INTEGER | 当前信号灯状态 |
| `latest_traffic_light_status` | TEXT | 最近信号灯状态 |
| `split_merge_tag` | TEXT | 分流合流标签 |
| `wiper_status` | TEXT | 雨刮状态 |
| `navigation_status` | TEXT | 导航状态 |
| `indicator_status` | TEXT | 转向灯状态 |
| `latest_stop_line_direction` | REAL | 最近停止线方向角（连续值） |
| `cumulative_distance` | REAL | 累计行驶距离 |
| `ego_dr_trajectory` | TEXT | 自车DR轨迹（JSON） |
| `ego_lane_id/ego_link_id/ego_hq_lane_id` | TEXT | 车道/链接/HQ车道ID |
| `ego_lane_curvature` | REAL | 车道曲率 |
| `ego_to_centerline_dist` | REAL | 到中心线距离 |
| `ego_to_left/right_boundary_dist` | REAL | 到左右边界距离 |
| `ego_corner_*_2_*_boundary_dist` | REAL | 四角到边界距离（8列） |
| `topology_constraint_tag` | TEXT | 拓扑约束标签 |
| `specify_topology_tag` | TEXT | 指定拓扑标签 |

### 分类列枚举值


#### `traffic_light_status`

| 值 | 中文 |
|----|------|
| `-1` | 无信号灯 |
| `1` | 绿灯 |
| `2` | 黄灯 |
| `3` | 红灯 |

#### `latest_traffic_light_status`

| 值 | 中文 |
|----|------|
| `绿` | 绿灯 |
| `黄` | 黄灯 |
| `红` | 红灯 |
| `未知` | 未知 |

#### `split_merge_tag`

| 值 | 中文 |
|----|------|
| `split` | 分流 |
| `merge` | 合流 |
| `none` | 无 |

#### `wiper_status`

| 值 | 中文 |
|----|------|
| `关闭` | 关闭 |
| `间歇档` | 间歇档 |

#### `navigation_status`

| 值 | 中文 |
|----|------|
| `导航中` | 正在导航 |

---

## intersection_info

路口信息表——记录路口ID、车道数、自车车道索引、车道详情（本DB无数据）

### 列定义

| 列名 | 类型 | 说明 |
|------|------|------|
| `intersection_id` | INTEGER | 路口唯一ID |
| `lane_count` | INTEGER | 路口车道总数 |
| `ego_lane_index` | INTEGER | 自车车道索引 |
| `lane_info` | TEXT | 车道详情（JSON数组） |

---

## static_link

静态道路链接表——道路拓扑链接的静态属性

### 列定义

| 列名 | 类型 | 说明 |
|------|------|------|
| `link_id` | INTEGER | 链接唯一ID |
| `link_type` | TEXT | 链接类型 |
| `link_turn_type` | TEXT | 转向类型 |
| `link_class` | TEXT | 道路等级 |
| `link_attribute` | TEXT | 特殊属性 |
| `link_speed_limit` | REAL | 限速值 |
| `link_predecessor/successor` | TEXT | 前驱/后继 |
| `link_exp_speed_limit` | REAL | 期望限速 |
| `is_intersection_out` | INTEGER | 是否路口出口 |

### 分类列枚举值


#### `link_type`

| 值 | 中文 |
|----|------|
| `主干道` | 主干道 |
| `主路` | 主路 |
| `入口匝道` | 入口匝道 |
| `匝道` | 匝道 |
| `路口` | 路口 |
| `辅路` | 辅路 |

#### `link_turn_type`

| 值 | 中文 |
|----|------|
| `右转` | 右转 |
| `左转` | 左转 |
| `掉头` | 掉头 |
| `直行` | 直行 |
| `直行右转` | 直行右转 |
| `路口` | 路口 |
| `进入` | 进入 |
| `退出` | 退出 |

#### `link_class`

| 值 | 中文 |
|----|------|
| `主要大街/城市快速道` | 主要大街/城市快速道 |
| `主要道路` | 主要道路 |
| `国道` | 国道 |
| `次要道路` | 次要道路 |
| `省道` | 省道 |
| `高速公路` | 高速公路 |

#### `link_attribute`

| 值 | 中文 |
|----|------|
| `无特殊属性` | 无特殊属性 |
| `隧道` | 隧道 |
| `其他` | 其他 |

---

## dynamic_link

动态道路链接表——随时间变化的道路拓扑链接属性

### 列定义

| 列名 | 类型 | 说明 |
|------|------|------|
| `ts` | INTEGER | 时间戳 |
| `link_id` | INTEGER | 链接唯一ID |
| `link_type` | TEXT | 链接类型 |
| `link_attribute` | TEXT | 特殊属性 |
| `link_exp_speed_limit` | REAL | 期望限速 |
| `link_predecessor/successor` | TEXT | 前驱/后继 |
| `include_lane_ids` | TEXT | 包含车道ID列表 |

---

## dynamic_lane

动态车道表——随时间变化的车道属性

### 列定义

| 列名 | 类型 | 说明 |
|------|------|------|
| `ts` | INTEGER | 时间戳 |
| `lane_id` | INTEGER | 车道唯一ID |
| `ref_link_id` | INTEGER | 所属链接ID |
| `left_boundary_id/right_boundary_id` | INTEGER | 左右边界ID |
| `lane_type` | TEXT | 车道类型 |
| `lane_turn_type` | TEXT | 转向类型 |
| `lane_trans_type` | TEXT | 过渡类型 |
| `predecessors/successors` | TEXT | 前驱/后继车道ID |
| `lane_relate_obs_ids` | TEXT | 关联障碍物ID |

### 分类列枚举值


#### `lane_type`

| 值 | 中文 |
|----|------|
| `LANE_TYPE_EMERGENCY` | 应急车道 |
| `未知` | 未知 |

#### `lane_turn_type`

| 值 | 中文 |
|----|------|
| `TURN_TYPE_AHEADRIGHT` | 直行右转 |
| `TURN_TYPE_LEFTLUTURN` | 左转掉头 |
| `右转` | 右转 |
| `左转` | 左转 |
| `掉头` | 掉头 |
| `直行` | 直行 |
| `路口` | 路口 |

#### `lane_trans_type`

| 值 | 中文 |
|----|------|
| `TRANS_TYPE_Ending` | 车道结束 |
| `TRANS_TYPE_FullConnect` | 全连接 |
| `TRANS_TYPE_LeftConnect` | 左连接 |
| `TRANS_TYPE_OneSplitTwo` | 一分为二 |
| `TRANS_TYPE_RightConnect` | 右连接 |
| `TRANS_TYPE_UNDEFINED` | 未定义 |

---

## static_lane

静态车道表（本DB无数据）

### 列定义

| 列名 | 类型 | 说明 |
|------|------|------|
| `lane_id` | INTEGER | 车道唯一ID |
| `lane_type` | TEXT | 车道类型 |
| `lane_turn_type` | TEXT | 转向类型 |
| `lane_trans_type` | TEXT | 过渡类型 |
| `link_id` | INTEGER | 所属链接ID |
| `lane_relate_obs_id` | INTEGER | 关联障碍物ID |

---

## 附录: range_tag标签来源分类

### 车端行为标签

由车端IDl协议直接上报，经`tag_map.py`中的`refDictEn`映射后写入SQLite。
路径: `gsbag_parser/tag_map.py` → `em_behavior_tag_parser.py` → `TokenizerResult`

- `AVOIDANCE_INLANE`: 本车道内避让
- `AVOIDANCE_INSAMELANE`: 同车道内避让
- `AVOIDANCE_LANEYIELDBICYCLISTORTRICYCLIST`: 车道内让行自行车/三轮车
- `AVOIDANCE_LANEYIELDCARORTRUCK`: 车道内让行轿车/卡车
- `AVOIDANCE_LANEYIELDCONE`: 车道内让行锥桶
- `AVOIDANCE_LANEYIELDTRUCKINADJACENTLANE`: 车道内让行相邻车道卡车
- `AVOIDANCE_LANEYIELDVRU`: 车道内让行弱势道路用户
- `AVOIDANCE_MULTIOBSTACLEAROUNDEGO`: 多障碍物绕行自车
- `CRUISE_CARORTRUCKCROSS`: 巡航中轿车/卡车横穿
- `CRUISE_CARORTRUCKCUTIN`: 巡航中轿车/卡车切入
- `CRUISE_CARORTRUCKCUTOUT`: 巡航中轿车/卡车切出
- `CRUISE_CLOSECUTIN`: 巡航中紧密切入
- `CRUISE_CONGESTION`: 巡航中拥堵
- `CRUISE_CREEP`: 巡航中蠕行
- `CRUISE_CURVE`: ⚠ 待补充
- `CRUISE_CUTIN`: 巡航中切入
- `CRUISE_CUTOUT`: 巡航中切出
- `CRUISE_DISTANTFOLLOW`: 巡航中远距跟随
- `CRUISE_FOLLOW`: ⚠ 待补充
- `CRUISE_FREESPACEAVOIDANCE`: 巡航中自由空间避让
- `CRUISE_FREESPACEYIELD`: 巡航中自由空间让行
- `CRUISE_LEADCUTOUTLEADINGSTOPPED`: 巡航中前车切出后前车静止
- `CRUISE_RAMP`: ⚠ 待补充
- `CRUISE_SLOWFOLLOW`: 巡航中慢速跟随
- `CRUISE_SLOWMOVE`: ⚠ 待补充
- `CRUISE_STRAIGHT`: ⚠ 待补充
- `CRUISE_VRUCROSS`: 巡航中VRU横穿
- `CRUISE_VRUCUTIN`: 巡航中VRU切入
- `CRUISE_VRUCUTOUT`: 巡航中VRU切出
- `INTERSECTION_LEFTTURN`: 路口左转
- `INTERSECTION_RIGHTTURN`: 路口右转
- `INTERSECTION_ROUNDABOUT`: 环岛
- `INTERSECTION_ROUNDABOUTUNSIGNAL`: 无信号灯环岛
- `INTERSECTION_STRAIGHT`: 路口直行
- `INTERSECTION_UTURNLANE`: 掉头车道
- `LANECHANGE_ABANDON`: 放弃变道
- `LANECHANGE_AVOIDANCE`: 避让变道
- `LANECHANGE_DIVERGELANECHANGERIGHTTURN`: 分流变道右转
- `LANECHANGE_FREEWAY`: 高速变道
- `LANECHANGE_MERGELANECHANGEDROP`: 合流变道驶出
- `LANECHANGE_MERGELANECHANGEMAINROAD`: 合流变道进入主路
- `LANECHANGE_MERGELANECHANGEZIPPER`: 拉链式合流变道
- `LANECHANGE_NAVIGATION`: 导航变道
- `LANECHANGE_NAVLANECHANGECONGESTION`: 导航变道拥堵
- `LANECHANGE_NAVLANECHANGERAMP`: 导航变道匝道
- `LANECHANGE_OVERTAKE`: 变道超车
- `LANECHANGE_OVERTAKELEFTBICYCLISTORTRICYCLIST`: 左变道超自行车/三轮车
- `LANECHANGE_OVERTAKELEFTCARORTRUCK`: 左变道超轿车/卡车
- `LANECHANGE_OVERTAKERIGHTBICYCLISTORTRICYCLIST`: 右变道超自行车/三轮车
- `LANECHANGE_OVERTAKERIGHTCARORTRUCK`: 右变道超轿车/卡车
- `STOPANDGO_BRAKESTOPANTICRASHBUCKET`: 刹停防撞桶
- `STOPANDGO_BRAKESTOPCONE`: 刹停锥桶
- `STOPANDGO_FIRSTCARSTARTATGREENLIGHT`: 红绿灯路口首车绿灯起步
- `STOPANDGO_FIRSTCARSTOPATREDLIGHT`: 红绿灯路口首车红灯停车
- `STOPANDGO_STARTCONGESTION`: 拥堵起步
- `STOPANDGO_STARTFOLLOWOBSTACLE`: 跟随障碍物起步
- `STOPANDGO_STARTNONFOLLOWOBSTACLE`: 非跟随障碍物起步
- `STOPANDGO_STARTRAMP`: 匝道起步
- `STOPANDGO_STOP`: 停车
- `STOPANDGO_STOPBEHINDOBSTACLE`: 障碍物后停车

### 云端算子标签

由云端规则算子计算产出，经`tokenizer_processor_new.py`汇入`activity_list`，最终写入SQLite。

- `AbnormalLaneChange`: 异常变道行为 — `L2_Pred/.../activity_new/op_abnormal_lane_change.py`
- `ActiveWiperState`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_wiper.py`
- `AvoidanceBorrowLane`: 借用车道避让 — `L2_Pred/.../activity_new/op_avoidance_borrow_lane.py`
- `BinCounter`: Bin计数器 — `L2_Pred/.../activity_new/op_bin_counter.py`
- `CloseFollow`: 近距离跟随 — `L2_Pred/.../activity_new/op_close_follow.py`
- `CongestedFollow`: 拥堵跟随 — `L2_Pred/.../activity_new/past_op/valid/op_congested_follow.py`
- `CrossConflict`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_cross_conflict.py`
- `CrossStopLineOnYellowLight`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_traffic_light.py`
- `CrossVRU`: VRU横穿 — `L2_Pred/.../activity_new/op_cross_vru.py`
- `CrossVehicle`: 车辆横穿 — `L2_Pred/.../activity_new/op_cross_vehicle.py`
- `Cutin`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_cutin.py`
- `GreenLightNotProceeding`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_traffic_light.py`
- `HighAccPedal`: 高加速踏板 — `L2_Pred/.../activity_new/op_high_acc_pedal.py`
- `HighSteeringWheelTorque`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_high_steering_torque.py`
- `InteractivePedestrian`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_interactive_pedestrian.py`
- `InteractiveVRU`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_interactive_vru.py`
- `InteractiveVehicle`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_interactive_vehicle.py`
- `Intersection`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_intersection.py`
- `Jerk`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_jerk.py`
- `LaneChange`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_lane_change.py`
- `LaneKeep`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_lane_keep.py`
- `LowTTC`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_low_ttc.py`
- `NoSpeedIncrease`: 无加速 — `L2_Pred/.../activity_new/op_speed_limit/op_no_speed_increase.py`
- `NotCenter`: 偏离车道中心 — `L2_Pred/.../activity_new/op_not_center.py`
- `ObstacleAvoidance`: 障碍物避让 — `L2_Pred/.../activity_new/op_obstacle_avoidance.py`
- `ObstacleCollision`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_obstacle_collision.py`
- `ObstacleNearMiss`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_obstacle_near_miss.py`
- `OnRamp`: 上匝道 — `L2_Pred/.../activity_new/op_on_ramp.py`
- `OppositeLaneDriving`: 逆行 — `L2_Pred/.../activity_new/op_opposite_lane_driving.py`
- `OtherRamp`: 其他匝道 — `L2_Pred/.../activity_new/op_other_ramp.py`
- `OverSpeedLimit`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_speed_limit/op_over_speed.py`
- `RunFullYellowLight`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_traffic_light.py`
- `RunRedLight`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_traffic_light.py`
- `RunYellowLight`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_traffic_light.py`
- `Slope`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_slope.py`
- `SolidLaneChange`: 实线变道 — `L2_Pred/.../activity_new/op_solid_lane_change.py`
- `SpeedIncreaseStats`: 加速统计 — `L2_Pred/.../activity_new/op_speed_limit/op_speed_increase_stats.py`
- `SteeringSmallSwing`: 方向盘小幅摆动 — `L2_Pred/.../activity_new/op_steering_small_swing.py`
- `SteeringWheelSlam`: 猛打方向盘 — `L2_Pred/.../activity_new/op_steering_wheel_slam.py`
- `StraightDriving`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_straight_driving.py`
- `ToRoadsideStop`: 靠边停车 — `L2_Pred/.../activity_new/op_to_roadside_stop.py`
- `TrafficIntersection`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_traffic_intersection.py`
- `TrafficLightAbnormal`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_traffic_light.py`
- `Turning`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_turning.py`
- `VRUCrossConflict`: ⚠ 待补充 — `L2_Pred/.../activity_new/op_vru_cross_conflict.py`
- `YieldToPedestrian`: 让行行人 — `L2_Pred/.../activity_new/op_yield_to_pedestrian.py`
- `accelerating`: 加速 — `L2_Pred/.../feature_op/ego_motion_feature.py`
- `creeping`: 蠕行 — `L2_Pred/.../feature_op/ego_motion_feature.py`
- `cruising`: 巡航 — `L2_Pred/.../feature_op/ego_motion_feature.py`
- `decelerating`: 减速 — `L2_Pred/.../feature_op/ego_motion_feature.py`
- `hard_braking`: 急刹车 — `L2_Pred/.../feature_op/ego_motion_feature.py`
- `invalid_navi_guide_line_infos_empty`: 无效导航(引导线信息为空) — `user_workspace/xiangchenming/op_navi_command.py`
- `invalid_navi_not_in_sd`: 无效导航(不在SD地图中) — `user_workspace/xiangchenming/op_navi_command.py`
- `invalid_topology_ego_static_map_link_id_invalid`: 无效拓扑(静态地图link_id无效) — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `invalid_topology_ego_static_map_link_id_not_match`: 无效拓扑(静态地图link_id不匹配) — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `invalid_topology_no_map_schema`: 无效拓扑(无地图方案) — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `invalid_topology_other_no_match`: 无效拓扑(其他不匹配) — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `navi_enter`: 导航进入 — `user_workspace/xiangchenming/op_navi_command.py`
- `navi_exit`: 导航退出 — `user_workspace/xiangchenming/op_navi_command.py`
- `navi_keep`: 导航保持 — `user_workspace/xiangchenming/op_navi_command.py`
- `navi_other`: 导航其他 — `user_workspace/xiangchenming/op_navi_command.py`
- `navi_turn_left`: 导航左转 — `user_workspace/xiangchenming/op_navi_command.py`
- `navi_turn_right`: 导航右转 — `user_workspace/xiangchenming/op_navi_command.py`
- `navi_u_turn`: 导航掉头 — `user_workspace/xiangchenming/op_navi_command.py`
- `over_rain_level`: 雨量等级 — `L2_Pred/.../feature_op/weather_feature.py`
- `reversing`: 倒车 — `L2_Pred/.../feature_op/ego_motion_feature.py`
- `route_deviation_curb`: 偏离路缘 — `L2_Pred/.../feature_op/route_deviation_feature.py`
- `route_deviation_guide_line`: 偏离导流线 — `L2_Pred/.../feature_op/route_deviation_feature.py`
- `route_deviation_sd_newpath_need_action`: 偏离需接管 — `L2_Pred/.../feature_op/route_deviation_feature.py`
- `route_deviation_sd_newpath_noneed_action`: 偏离无需接管 — `L2_Pred/.../feature_op/route_deviation_feature.py`
- `route_deviation_sd_yaw`: 偏离航向 — `L2_Pred/.../feature_op/route_deviation_feature.py`
- `stationary`: 静止 — `L2_Pred/.../feature_op/ego_motion_feature.py`
- `steering_left_120_185`: 左转120-185° — `L2_Pred/.../feature_op/steering_feature.py`
- `steering_left_15_60`: 左转15-60° — `L2_Pred/.../feature_op/steering_feature.py`
- `steering_left_60_120`: 左转60-120° — `L2_Pred/.../feature_op/steering_feature.py`
- `steering_left_above_185`: 左转>185° — `L2_Pred/.../feature_op/steering_feature.py`
- `steering_right_120_185`: 右转120-185° — `L2_Pred/.../feature_op/steering_feature.py`
- `steering_right_15_60`: 右转15-60° — `L2_Pred/.../feature_op/steering_feature.py`
- `steering_right_60_120`: 右转60-120° — `L2_Pred/.../feature_op/steering_feature.py`
- `steering_slightly`: 轻微转向 — `L2_Pred/.../feature_op/steering_feature.py`
- `topology_auxiliary`: 辅路 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_bridge`: 桥梁 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_interchange`: 立交 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_interchange_roundabout`: 立交环岛 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_intersection`: 路口 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_intersection_cross_road`: 十字路口 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_intersection_multi_fork`: 多岔路口 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_intersection_other`: 其他路口 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_intersection_small_cross_road`: 小十字路口 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_intersection_small_t_junction`: 小T型路口 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_intersection_straight_intersection`: 直行路口 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_intersection_t_junction`: T型路口 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_intersection_y_junction`: Y型路口 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_left_turn_lane`: 左转车道 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_main`: 主路 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_merge`: 合流 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_ramp`: 匝道 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_right_turn_lane`: 右转车道 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_roundabout`: 环岛 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_service_area`: 服务区 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_split`: 分流 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_toll_station`: 收费站 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_tunnel`: 隧道 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_tunnel_roundabout`: 隧道环岛 — `L2_Pred/.../feature_op/topology_constraint_feature.py`
- `topology_unstructured_road`: 非结构化道路 — `L2_Pred/.../feature_op/topology_constraint_feature.py`