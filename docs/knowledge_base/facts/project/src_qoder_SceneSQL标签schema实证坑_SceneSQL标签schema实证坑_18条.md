---
category: project
tags: qoder
---

[src=qoder:SceneSQL标签schema实证坑] SceneSQL标签schema实证坑（18条）
SceneSQL schema 实证坑（文档没写或写错的，全部 bag_id 可复现）

1. `dynamic_obj.heading` = **自车相对系**（h_rel = obj_yaw - ego_yaw，CCW 正，±π=正对向）；绝对朝向 = `heading + utm_yaw`
2. `dynamic_obj.x/y` = **ego 相对系**（x 前、y 左为正，米），**不是 UTM**
3. `ego.utm_x/utm_y` 部分 bag **全程冻结**（speed 正常坐标不动），不可用
4. `relative_velocity`/`absolute_velocity` 与位置增量物理矛盾，**全部不可用**
5. 目标运动唯一可信源 = `obs_dr_trajectory` 的 speed 数组（JSON，5 采样/行，**单位 km/h**；静止=0、运动 9~55）
6. `ego_dr_trajectory` 与 `obs_dr_trajectory` **同一 DR 系**，idx0≈当前位置，数组=当前+未来~0.4s DR 外推（10Hz）
7. ego 系距离差**不能**当"目标驶来"——ego 驶向静止目标签名相同（105nclK 案）
8. 转弯中目标相对朝向**扫掠**（对向车 h_rel 从 ~90°→180°），逐帧窄带过滤只剩 1~2 帧；必须用绝对朝向+首/末朝向比较
9. `dynamic_obj.type` 仅 5 种：bus/car/motorcycle/pedestrian/truck
10. `ego.specify_topology_tag` 枚举：cross_road/small_cross_road/t_junction/small_t_junction/straight_intersection/multi_fork/other/none —— **直查**，别解析 intersection_info.lane_info（部分 bag malformed JSON）
11. 信号灯只有颜色（latest_traffic_light_status），**无箭头形状字段**；"绿箭头≠无冲突"（100dstH 绿掉头箭头下对向车照过）
12. 自车行为锚点：`range_tag Turning`（sub_tag=turn_left/turn_right）× Intersection 重叠可精确排除掉头类；**标签可能滞后实际行为 15s 或缺失**（s0t 案：truck 213 通过、turn_left 228 才起）——轨迹类判据别绑标签起点，用全窗口
13. `range_tag` 的 start/end_ts 是**秒**；抽帧 API 要**纳秒**（×1e9）
14. 前摄 topic 新老 bag 不同：新=`/gac/cam/orig_fw120_encoded`（10Hz），老=`/gac/cam/fw120_encoded`（28Hz）；**带 120 字样的才是前摄**
15. **抽帧 API 缺 topic 静默 fallback 到任意相机**（rl99/r50/ft30 侧后视）——clip payload 必须显式带 topic，用返回消息 "Extracted N frames from /xxx" 逐 clip 核验（整轮验证用错相机的教训）
16. dynamic_obj 采样 1Hz：高速目标（>40km/h）窗口内常仅 2 帧 → per-obj 帧数闸 ≤2，质量闸放事件级
17. 跟踪器**远距/初段朝向不可靠**（接近 180° 翻转），接近后收敛；朝向判据以**最近帧为锚**或只用窗口内帧（obj278 案）
18. execute-sql API 偶发路由抽风（裸库有数据 API 返回空）→ 排查先 SSH 直查 sqlite3 对照

**完整版（含验证方式/案例/排查套路）**：`/data/var/workspace/projects/projects/SceneSQL/docs/scene_tag_sql_dev_guide.md` §2
