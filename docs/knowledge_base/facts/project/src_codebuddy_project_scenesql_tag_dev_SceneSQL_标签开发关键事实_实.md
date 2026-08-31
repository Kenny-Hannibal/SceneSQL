---
category: project
tags: codebuddy,project
---

[src=codebuddy:project_scenesql_tag_dev] SceneSQL 标签开发关键事实（实证得出，文档无）
SceneSQL 标签 SQL 开发的实证事实（2026-07-27 通过轨迹重构+抽帧交叉验证，schema 文档未记载或记载错误）：

**Why:** 开发无保护左转标签时发现旧 left_turn_conflict SQL 因坐标系误用大量误报，逐一实证得出以下事实；用户（黄梓建）已确认结论正确。

**How to apply:** 写任何涉及目标朝向/路口拓扑/抽帧的 SQL 时直接引用，别再从文档猜。

- `dynamic_obj.heading` 是**自车相对系**（h_rel = obj_yaw - ego_yaw，CCW 正；0=同向，±π=正对向——已用运动方向实证）。文档 SCHEMA_REFERENCE 说 x/y 是 UTM —— **错的**，x/y 是 ego 相对坐标（x 前、y 左为正，米级）
- **对向判定必须用 sweep-free 方法**：左转时 ego 自转 70°~90°，对向车相对朝向从 ~90° 扫到 ~180°，逐帧窄带过滤只能抓住末尾 1~2 帧。正确做法：h_abs = heading + utm_yaw（直行目标全程恒定），与 ego 转弯初始/末尾朝向比较，任一相差>150°。yaw 差用圆均值 ATAN2(AVG(SIN),AVG(COS))
- **数据质量坑（v4/v5 已规避）**：ego.utm_x/utm_y 部分 bag 全程冻结（speed 正常但坐标不动）；relative_velocity/absolute_velocity 与位置增量物理矛盾，全部不可用
- **目标运动状态唯一可信源 = `obs_dr_trajectory` 的 speed 数组**（JSON，5 采样/行，单位 km/h，实证标定：运动车 24~34，静止车 0）。ego 系距离差**不能**当接近判据——ego 驶向静止目标时签名相同（无保护左转项目在此踩坑：静止等待车被误判"对向驶来"，用户抽检发现）。"直行"类判定必须 max(speed)>5km/h
- `dynamic_obj.type` 全库仅 5 种：bus/car/motorcycle/pedestrian/truck（无 sedan/suv/cyclist）
- `ego.specify_topology_tag` 枚举：cross_road / small_cross_road / t_junction / small_t_junction / straight_intersection / multi_fork / other / none —— 路口拓扑直查，别解析 intersection_info.lane_info JSON（部分 bag 是 malformed JSON）
- 信号灯：ego 表只有颜色（latest_traffic_light_status 红/黄/绿/未知 + traffic_light_status -1/1/2/3），**圆饼/箭头形状 sqlite 无字段**
- dynamic_obj 采样 1Hz；2 秒 TTC 门限内对向目标仅停留 1~2 帧 → per-obj 帧数阈值要 ≤2，质量阈值放合并后事件级
- 前摄 topic 新旧 bag 命名不同：新 bag = `/gac/cam/orig_fw120_encoded`（10Hz），老 bag（202603 批次）= `/gac/cam/fw120_encoded`（28Hz）——抽帧 0 帧时先查 bag info 对 topic 名（用户指定：带 120 字样的才是前摄）
- **抽帧 API 的 clip 缺 topic 字段时会静默 fallback 到任意相机**（rl99/r50/ft30...）——必须显式带 topic，并用返回消息 "Extracted N frames from /xxx" 逐 clip 核验实际相机；我曾因此用侧后视帧做了一整轮错误验证被用户抓到
- 自车行为锚定：`range_tag Turning(sub_tag='turn_left'/'turn_right')` 精确标记转弯行为窗口（比 Intersection 子标签干净，掉头由 LuturnIntersection 或转向角区分）
- range_tag start_ts/end_ts 是**秒**；抽帧 API 要**纳秒**（×1e9）
- 抽帧 API 三件套：POST /api/video/extract-batch → GET /api/video/extract-batch/{task_id} → GET /api/video/frames/{task_id}/{clip_idx}/{filename}
- 可视化/搜索服务在 DSW，批次 20260702_T68_2471_c5afa57_100w（sqlite 模式，15460 DBs）
