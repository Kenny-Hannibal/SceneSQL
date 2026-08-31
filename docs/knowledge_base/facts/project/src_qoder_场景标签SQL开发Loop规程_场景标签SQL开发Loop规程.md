---
category: project
tags: qoder
---

[src=qoder:场景标签SQL开发Loop规程] 场景标签SQL开发Loop规程
场景标签 SQL 开发 Loop（自 Qoder 接手版）

**详细知识库（开工必读）**：`/data/var/workspace/projects/projects/SceneSQL/docs/scene_tag_sql_dev_guide.md`
——18 条 schema 实证事实、误报分类学（机理→判据→案例 bag_id）、SQL 工程技巧、当前判据链。

开工强制顺序

1. **先向用户要金标准片段**（bag_id + ts 窗口），不要凭场景名想象定义
2. 拉金标准全部数据核对：ego yaw/speed/steering + 候选 obj 的 x/y/heading/DR speed 逐帧轨迹
3. 场景拆子条件表（自车行为/对象类别/方向/运动/冲突/强度/排除），与用户逐条对齐颗粒度

核心语义（用户模型，已对齐）

- 冲突 = **未来轨迹线空间重叠（车辙重叠）**，不是同刻相撞
- 离线实现：`min dist(ego/obj DR idx0 路径点对, |Δts|≤5s) < 5m`，用真实未来坐标不外推
- 对向：**只认原路对向 diff_init>150°**（驶入路对向与转弯弧平行不交叉）
- 直行：运动中（`obs_dr_trajectory` speed>5km/h 且≥2帧）+ 朝向稳定（≥90% 帧在最近帧朝向 ±15° 内）
- 自车行为锚点：`range_tag Turning(sub_tag='turn_left')` × `Intersection(LeftIntersection)` 重叠

高频坑速查（详见指南 §2 共 18 条）

- heading 自车相对系；x/y ego 相对系（非 UTM）；ego utm_x/y 冻结；velocity 字段全不可用
- 目标运动唯一可信源 = `obs_dr_trajectory` 的 speed 数组（**km/h**，静止=0）
- ego 系距离差**不能**当"驶来"判据（ego 驶向静止目标同签名）
- 1Hz 采样：高速目标仅 2 帧，per-obj 帧数闸 ≤2；质量闸放事件级
- 跟踪器远距朝向垃圾（180° 翻转），朝向判据以**最近帧为锚**
- 抽帧 API 缺 topic **静默 fallback 到侧后视**：必须显式带 `orig_fw120_encoded`(新bag)/`fw120_encoded`(老bag)，并用返回消息逐 clip 核验
- 对向走廊判据（F5）：首个 dist<20m 帧方位角 <30°，剔除侧向横穿/转弯汇入

迭代 Loop（每轮必走）

1. 改底稿 `SceneSQL/<tag>.sql`（头注写版本史）
2. **对照组回归**（14 个 bag 清单在指南 §4.1；用户判错的 bag 永远进组）
3. 全量批次（`db_limit=20000, max_workers=32`，15460 DBs）
4. **新 seed** 随机抽 10 条 → 抽帧（fps 自适应铺满窗口；高速目标按 ts_at_closest 补帧）→ 逐条画面+目标级数据双核对 → TRUE/LIKELY/UNCERTAIN/FALSE 分类 + 精度统计
5. 误报归因到机制（朝向稳定性/距离趋势/方位轨迹/速度分布 四维诊断），进对照组
6. 交付：底稿 + recipe yaml **逐字节一致**（python yaml 校验脚本）+ 验证集打包到 `docs/gac/sql_validation/<tag>_val_<版本>/`（README 清单 + 每样本文件夹）
7. 用户复核判错 → 回到 1

误报分类学（机理 → 判据）

| 模式 | 判据 |
|------|------|
| 静止/排队车 | DR speed>5 且 ≥2 帧 |
| 已通过远离车 | 最近帧非首帧（逼近方向） |
| 时间错配会车 | 轨迹交叉 \|Δts\|≤5s |
| 自身转弯/横穿车 | ≥90% 帧朝向±15° + F5 方位<30° |
| 驶入路对向（平行不交叉） | 删除 diff_final，仅 diff_init |
| 后方跟随车 | diff_init≈0 结构性排除 |
| 远距弱冲突 | 同时刻最近 <10m |
| 锚定标签滞后 | 轨迹交叉用全窗口 ego 路径 |

心智守则

不信文档（每条结论要 bag_id 可复现）、金标准先行、对照组会过拟合（每版新 seed）、
每个误报归因到机制、DR 系优先于 ego 系、验证相机必须核验、用户判错的样本是最宝贵资产。

参考实现

- 定稿底稿：`/data/var/workspace/projects/projects/SceneSQL/unprotected_left_turn.sql`（v10.4）
- recipe：`SceneSQL/agent/backend/app/core/recipes/unprotected_left_turn.yaml`（与底稿逐字节一致）
- 验证集：`docs/gac/sql_validation/unprotected_left_turn_visualation_val_v10.4/`
- 精度基线：55 事件/55 bags，10 样本 7 TRUE + 2 LIKELY + 1 UNCERTAIN，0 FALSE
