# 场景标签 SQL 开发实战指南（无保护左转项目沉淀）

> 来源：2026-07-27~30 无保护左转标签从 v1 迭代到 v10.4 的全过程。
> 读者：后续开发任何场景标签 SQL（对向/横穿/插队/行人）的人或 agent。
> 原则：**实证优先**——本文每条结论都有 bag_id + obj_id + ts 可复现，不信文档、不猜坐标系。

---

## 1. 开工前：场景定义对齐（比写 SQL 更重要）

### 1.1 先要"金标准片段"

不要凭场景名字自己想象定义。让用户给 1 个**他们认为最标准的 bag_id + ts 窗口**，
把它的一切数据（ego yaw/speed/steering、每个候选 obj 的轨迹/朝向/速度）拉出来逐项核对，
作为所有判据的校准基准。

本项目金标准：`100dstH6eqLKVX1U9ODxic202606, ts 1774235486~494`
—— ego 掉头起步，对向白 SUV 42km/h 贴身 6.4m 通过，轨迹交叉 3.6m。

### 1.2 把场景拆成可计算的子条件

以"无保护左转"为例，用户的定义拆解（对齐颗粒度后）：

| 子条件 | 用户原话语义 | 计算化 |
|--------|-------------|--------|
| 自车行为 | 自车正在左转（非掉头） | `range_tag Turning(sub_tag='turn_left')` 精确锚定 |
| 对象类别 | 机动车，不含行人/电动车 | `type IN ('car','bus','truck')` |
| 对向 | 对向直行车道来的车 | 目标绝对朝向 vs ego 转弯初始朝向 >150°（**仅原路对向**） |
| 直行 | 真的在动、且直行（非自身在转弯） | DR speed>5km/h ≥2帧 + ≥90%帧朝向±15°稳定 |
| 冲突 | 未来轨迹线空间重叠（车辙重叠），**不要求同刻相撞** | 见 1.3 |
| 干涉强度 | 足够近 | 同时刻最近 <10m |
| 业务排除 | T 型路口不算 | `specify_topology_tag NOT IN ('t_junction','small_t_junction')` |

### 1.3 冲突语义：未来轨迹交叉（本项目最重要的模型）

用户的原话模型：
> 每帧每个物体都有一条未来 3~5 秒的轨迹曲线（在它前面）。
> 只要 ego 的未来轨迹线与对向直行车的未来轨迹线**在空间上重叠**，
> 就代表"ego 要左转、有直行车的行驶行为会打断这个左转"——
> **不意味着两车会在某一帧相撞，是车辙重叠。**

离线任务的"作弊"优势：**不用外推**，直接用 ts+x 拿真实未来坐标：

```
判据 = min 空间距离( ego路径点@ts_e, obj路径点@ts_o ), 约束 |ts_e - ts_o| ≤ 5s
     < 5m  →  冲突
```

- 点来源：`ego.ego_dr_trajectory` / `dynamic_obj.obs_dr_trajectory` 的 `$.x[0]`,`$.y[0]`（DR 同系，已实证）
- `|Δts|≤5s` 是灵魂：目标 10 秒前开走、ego 才转到 → 点对时间差超限 → **不算**（时间错配类误报的根治）
- N 可调（3~5s），金标准标定 5s 刚好覆盖

### 1.4 对向的几何真相（易错）

左转几何上，ego 转弯弧线**只横穿原路的对向车道**；驶入路（转入的路）的对向车道与弧线**平行不交叉**（弧线并入近侧车道）。

**推论：只认"原路对向"（diff_init），删除"驶入路对向"（diff_final）。**
否则大量"转弯后正常会车"被误收（bc3 案：`124yD4gPhaHUuJjDATbUm8202606 obj657`）。

---

## 2. Schema 实证事实表（文档没写或写错的）

| # | 事实 | 验证方式/案例 |
|---|------|--------------|
| 1 | `dynamic_obj.heading` 是**自车相对系**（h_rel = obj_yaw - ego_yaw，CCW 正，±π=正对向）。绝对朝向 = `heading + utm_yaw` | 运动方向反推（102B3Eu obj357 东行） |
| 2 | `dynamic_obj.x/y` 是 **ego 相对系**（x 前、y 左为正，米），**不是 UTM** | 轨迹+画面对照 |
| 3 | `ego.utm_x/utm_y` 部分 bag **全程冻结**（speed 正常但坐标不动），不可用 | 100dstH/102B3Eu 打印全窗口 |
| 4 | `relative_velocity` / `absolute_velocity` 与位置增量**物理矛盾**，全部不可用 | 数值对拍 |
| 5 | **目标运动状态唯一可信源** = `obs_dr_trajectory` 的 speed 数组（JSON，5 采样/行，**单位 km/h**）。静止车=0，运动车 9~55 | 105nclK obj12 全程 0.0 实锤静止；对照运动车 24~42 |
| 6 | `ego_dr_trajectory` 与 `obs_dr_trajectory` **同一 DR 坐标系**，idx0≈当前位置，数组=当前+未来 ~0.4s DR 外推；10Hz | 单时刻两点距离 vs ego 系 x,y 距离对拍（obj169 案，6.03 vs 6.4m） |
| 7 | ego 系距离差**不能**当"目标驶来"判据——ego 驶向静止目标签名相同 | 105nclK 静止车被误判事件 |
| 8 | 转弯中目标相对朝向会**扫掠**（对向车 h_rel 从 ~90° 扫到 ~180°），逐帧窄带过滤只剩 1~2 帧。必须用绝对朝向 + 首/末朝向比较 | v3→v4 修复史 |
| 9 | `dynamic_obj.type` 全库仅 5 种：bus/car/motorcycle/pedestrian/truck | 全库 distinct |
| 10 | `ego.specify_topology_tag` 枚举：cross_road/small_cross_road/t_junction/small_t_junction/straight_intersection/multi_fork/other/none。**直查**，别解析 intersection_info.lane_info（部分 bag malformed JSON） | 全库分布统计 |
| 11 | 信号灯只有颜色（latest_traffic_light_status 红/黄/绿/未知），**无箭头形状字段**；且"绿箭头≠无冲突"（行为干涉为准） | 100dstH 绿掉头箭头下对向车照过 |
| 12 | **自车行为锚点**：`range_tag` 有 `Turning`（sub_tag=turn_left/turn_right），与 Intersection 重叠段做窗口可精确排除掉头类。注意标签可能**滞后于实际行为**（慢行 setup 案例晚 15s）或**缺失** | s0t 案：truck 213 通过，turn_left 标签 228 才起 |
| 13 | `range_tag` 的 start/end_ts 是**秒**；抽帧 API 要**纳秒**（×1e9） | 直接试用 |
| 14 | 前摄 topic 新老 bag 不同：新=`/gac/cam/orig_fw120_encoded`（10Hz），老=`/gac/cam/fw120_encoded`（28Hz）。**带 120 字样的才是前摄** | bag info 枚举 topic |
| 15 | **抽帧 API 在 clip 缺 topic 字段时静默 fallback 到任意相机**（rl99/r50/ft30 侧后视）。必须显式带 topic，并用返回消息 "Extracted N frames from /xxx" **逐 clip 核验** | v8.1 验证整轮用错相机的教训 |
| 16 | dynamic_obj 采样 1Hz。高速目标（>40km/h）窗口内常仅 2 帧 → per-obj 帧数阈值 ≤2，质量闸放事件级 | 金标准 obj169 仅 2 帧 |
| 17 | 跟踪器**远距/初段朝向不可靠**（接近 180° 翻转），接近后收敛。朝向类判据要么以**最近帧为锚**，要么只用**窗口内**帧 | obj278 案（早期 142°/180° 垃圾帧） |
| 18 | 同 bag 的 execute-sql API 偶发路由抽风（裸库有数据 API 返回空）→ 排查先 SSH 直查 sqlite3 对照 | bc1-3 排查时遇到 |

---

## 3. 误报模式分类学（模式 → 机理 → 判据 → 案例）

| 模式 | 机理 | 判据（v10.4 对应） | 案例 |
|------|------|-------------------|------|
| 静止/排队等待车 | ego 驶向静止目标 ≡ 目标驶来（ego 系距离差无法区分） | DR speed>5 且 ≥2 帧 | 105nclK obj12/69；10hyiFhr obj522（1/13 帧尖峰） |
| 已通过远离车 | 对向车先过 ego 后转，窗口内纯远离 | 逼近方向：最近帧非首帧 | s02 obj102（13→43m 远离） |
| 时间错配会车 | 目标先走 N 秒 ego 才到，无实际干涉 | 轨迹交叉 \|Δts\|≤5s | s03 obj527；旧真阳性 102B3Eu（Δt=10s 被排除，模型自洽） |
| 自身转弯/横穿车 | 目标朝向扫过对向区间，圆均值落入 >150°；或侧向汇入 | ① ≥90% 帧朝向±15°稳定 ② 首个 dist<20m 帧方位角 <30°（对向走廊） | s06 obj3（扫 117°）；bc1 obj455（扫 52°）；bc2 obj228/215（54°/35°） |
| 驶入路对向 | 与转弯弧平行不交叉，转完正常会车 | 删除 diff_final，仅 diff_init | bc3 obj657；15nb4bHU obj363 |
| 后方跟随车 | 跟车队在 ego 正后方，转角≈180° 时"对向于末路" | diff_init≈0 结构性排除（无需专门闸） | s07 obj794/892/1268 |
| 远距弱冲突 | 轨迹交叉但 10m+ 远，无让行 | 同时刻最近 <10m | s03 obj527（13.8m） |
| 锚定标签滞后/缺失 | Turning 标签晚于实际行为 | 轨迹交叉用**全窗口** ego 路径，别绑标签起点 | s0t 案（见 #12） |
| 宽路口远距对向 | 对向车在对向车道但 10m 闸值边缘 | 接受为 LIKELY_TRUE，交付时标注 | s01 案 |

**排查工具套路**：每个误报必须归因到具体机制，用目标级数据证实/证伪，别靠画面猜：
1. 拉 obj 逐帧 `x,y,heading,utm_yaw,obs_dr.speed` → 算 dist/brg/h_abs 轨迹
2. 看 4 件事：①朝向是否稳定（直行？） ②距离趋势（逼近/远离？） ③方位轨迹（前向走廊/侧向？） ④DR 速度分布（真动/静止/尖峰？）
3. 与 ego 的 init/final yaw 对照（原路对向/驶入路对向/同向？）

---

## 4. 验证协议（发版前强制）

**对照组（回归）与随机抽样（精度）分开做。**

### 4.1 对照组维护

- 每个用户判错的 bag 直接进对照组（正例/反例都要）
- 本项目对照组 14 个：gold(100dstH)、s0t(14Kfppv)、s1t(10mi6gq)、s5t(11kDBlP)、金标准16CS4g、static(105nclK)、fp(100EIej)、s02/s03/s06/s07、bc1/bc2/bc3
- 每次改 SQL 全量跑一遍，记录 rows 数变化

### 4.2 随机抽样验证（≥10 样本）

1. 全量跑批次（`db_limit=20000, max_workers=32`，15460 DBs ~1-2min）
2. `random.seed(换一个新的)` 抽 10 条——**别复用 seed，别精选**
3. 抽帧：
   - clip payload **显式带前摄 topic**（新 orig_fw120 / 老 fw120，两轮兜底）
   - fps 按事件时长自适应（`fps = max(0.4, min(2, 11/duration))`）让帧铺满窗口
   - 高速目标按 **ts_at_closest 定向补帧**（2fps 会错过 42km/h 的贴身瞬间）
   - 用返回消息逐 clip 核验实际 topic
4. 逐条"画面 + 目标级数据"双核对，分类 TRUE / LIKELY / UNCERTAIN / FALSE
5. 精度统计 + 误报归因 + 打包（README 清单 + 每样本文件夹）给用户复核
6. 用户判错的 → 归因 → 修 → 进对照组 → 重跑

### 4.3 交付清单

- 底稿 SQL（头注含版本史和判据链）
- recipe yaml（与底稿**逐字节一致**，脚本校验）
- 验证集目录（docs/gac/sql_validation/场景_val_版本/）
- 精度表 + 已知边界（不藏着）

---

## 5. SQL 工程技巧集

### 5.1 圆均值朝向差（防 ±π 环绕）

```sql
ATAN2(AVG(SIN(h_abs - init_yaw)), AVG(COS(h_abs - init_yaw))) AS diff_init
```

### 5.2 窗口函数取关键帧值（同 SELECT 多 WINDOW 分区）

```sql
WINDOW w  AS (PARTITION BY tc.start_ts, d.obj_id ORDER BY d.ts
              ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING),
       wc AS (PARTITION BY tc.start_ts, d.obj_id ORDER BY SQRT(d.x*d.x+d.y*d.y)
              ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING),
       w20 AS (PARTITION BY tc.start_ts, d.obj_id
              ORDER BY CASE WHEN SQRT(d.x*d.x+d.y*d.y) < 20 THEN 0 ELSE 1 END, d.ts
              ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
-- w: 首帧值;  wc: 最近帧值;  w20: 首个<20m帧值
```
**坑**：WINDOW 的 ORDER BY 不能引用同 SELECT 的别名（`ORDER BY dist` 会静默错误），写全表达式。

### 5.3 obs_dr_trajectory JSON 处理

```sql
-- 标量最大（一行内 5 采样取 max，SQLite 多参 MAX=标量 max）
MAX(COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0), ... ) AS dr_speed
-- 展开（路径点集）：json_each 双数组按 key 对齐
FROM dynamic_obj d2, json_each(d2.obs_dr_trajectory,'$.x') ox, json_each(d2.obs_dr_trajectory,'$.y') oy
WHERE ox.key = oy.key AND ox.value IS NOT NULL
```

### 5.4 相关标量子查询算路径距离（SQLite 允许引用外层别名）

```sql
(SELECT MIN((ox-epx)*(ox-epx)+(oy-epy)*(oy-epy))
 FROM (SELECT d2.ts ots, json_extract(...) ox, ... FROM dynamic_obj d2
       WHERE d2.obj_id = of2.obj_id AND d2.ts BETWEEN of2.ev_start AND of2.ev_end)
 CROSS JOIN (SELECT e2.ts ets, ... FROM ego e2 WHERE e2.ts BETWEEN ...)
 WHERE ... AND ABS(ots - ets) <= 5) AS path_dist_sq
```
成本：每候选 obj ≈ 15×15 点对，可忽略。

### 5.5 事件合并（重叠窗口并为一事件）

```sql
merge_marked:  start_ts <= LAG(end_ts) OVER (ORDER BY start_ts) → 同组
merge_grouped: SUM(is_new_group) OVER (ORDER BY start_ts) → grp_id
merge_repr:    FIRST_VALUE(game) OVER (PARTITION BY grp_id ORDER BY severity) → 代表
```
最后 `frame_count >= 2` 收事件级质量闸。

---

## 6. v10.4 判据链速查（当前定稿）

```
锚点:   Turning(turn_left) × Intersection(LeftIntersection) 重叠窗口
对象:   car/bus/truck, ego 系 40m 粗筛
运动:   max(DR speed)>5km/h 且 ≥2 帧>5
对向:   |圆均值(h_abs - init_yaw)| > 150°   （仅原路对向！）
直行:   ≥90% 帧在最近帧朝向 ±15° 内
方位:   首个 dist<20m 帧 |方位角| < 30°
逼近:   最近帧非首帧（首见后继续靠近）
近距:   同时刻最近 < 10m
交叉:   min dist(ego/obj DR idx0 点对, |Δts|≤5s) < 5m
相位:   最近帧 ≤ 转弯完成+2s；转弯完成前 ≥2 帧
排除:   T 型路口（specify_topology_tag）
```

当前精度（55 事件/55 bags，10 样本）：7 TRUE + 2 LIKELY + 1 UNCERTAIN，0 FALSE。

---

## 7. 遗留已知边界

1. 102B3Eu 类：轨迹交叉点对 Δt=10s > 5s 窗 → 被排除（用户模型自洽结果）
2. 夜间复杂路口帧证据不足的样本（建议按 ts_at_closest 补帧复核）
3. "ego 即将转弯时对向车贴身通过"与"ego 起步时通过" timing 同构，特征分不开（曾接受为残留）
4. 高速目标 1Hz 仅 2 帧，任何 per-obj ≥3 帧的闸都会误杀（金标准即 2 帧）
5. 对向走廊 30° 阈值（F5）在超宽路口（3+ 对向车道）可能误杀远道对向车（26°@20m 接近边界）

---

## 8. 心智守则（本项目最贵的教训）

1. **不信文档、不猜坐标系**——一切从轨迹+画面对拍实证
2. **金标准先行**——没有用户认可的正例，所有判据都是空中楼阁
3. **固定对照组会过拟合**——每版必须新 seed 随机抽样验证
4. **每个误报归因到机制**——"看着不对"不是理由，数据里的机理才是
5. **ego 系的距离/速度字段都可能是坑**——优先用 DR 系（同系、带速度、带未来）
6. **验证用的相机必须核验**——抽帧 API 会静默 fallback
7. **用户判错的样本是最宝贵资产**——直接进对照组，逐版回归
