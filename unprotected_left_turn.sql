-- ==============================================================================
-- 场景：无保护左转 v10.4（自车左转[Turning/turn_left 锚定] + 原路对向直行机动车
--                         未来轨迹交叉判定）
--
-- 语义模型（用户定义）：每帧每个目标有一条未来轨迹线；ego 未来轨迹与对向
--   直行车的未来轨迹在空间上重叠（车辙重叠）即算冲突——不要求同刻相撞。
--   离线任务不用外推，直接用 ts+x 的真实未来坐标：
--   判据 = min 空间距离( |Δts|≤5s 的 ego/obj DR idx0 路径点对 ) < 5m
--
-- 判据链（误报修复史见 docs/gac/sql_validation/ 各验证集 README）：
--   锚点: range_tag Turning(sub_tag='turn_left') × Intersection(LeftIntersection)
--         重叠（掉头场景整体剔除；标签滞后由全窗口轨迹交叉容忍）
--   对向: 仅原路对向 diff_init>150°（v10.4 删除 diff_final——驶入路对向与
--         转弯弧几何上永不交叉，两线平行，bc3 案）
--   直行: ≥90% 帧在最近帧朝向±15°内（v10.3 强稳定；窗口内转弯车扫 35°/52°/54°
--         剔除，跟踪器窗口外收敛噪声天然规避）
--   方位: 首个 dist<20m 帧方位角 <30°（F5 对向走廊；侧向横穿/转弯汇入剔除，
--         bc1/bc2 案）
--   运动: DR speed>5km/h 且≥2帧（静止/排队/尖峰剔除）
--   逼近: 最近帧非首帧（首见后继续靠近；纯远离目标剔除，s02 案）
--   同时刻最近 <10m；最近帧 ≤ 转弯完成+2s；仅 car/bus/truck；T型路口排除
-- ==============================================================================
--
-- 锚点（v9 起，按用户指引 2026-07-28）：
--   range_tag Turning(sub_tag='turn_left') 精确锚定自车左转行为窗口，
--   与 Intersection(LeftIntersection) 重叠段为事件窗口 —— 掉头场景整体剔除。
--   init/final yaw、t_turn_start、t_complete 全部直接取 Turning 窗口边界。
--   （Turning 标签滞后可达 15s，s0t 案 → 轨迹交叉用全窗口 ego 路径容忍）
--
-- 语义模型（用户定义）：
--   每帧每个目标有一条未来轨迹线；ego 未来轨迹与对向直行车的未来轨迹
--   在空间上重叠（车辙重叠）即算冲突 —— 不要求两车同刻相撞。
--   离线任务不用外推，直接用 ts+x 的真实未来坐标：
--   判据 = min 空间距离( |Δts|≤5s 的 ego/obj DR idx0 路径点对 ) < 5m
--
-- 其余判据（误报修复史见 docs/gac/sql_validation/ 各验证集 README）：
--   机动车(car/bus/truck)；运动中(DR speed>5km/h 且≥2帧)；
--   对向(目标绝对朝向 vs 转弯初始/末尾朝向>150°)；
--   直行(众数朝向一致±30°内帧占比≥60%，容忍跟踪器翻转)；
--   逼近(首见-最近>2m)；同时刻最近<10m；最近帧方位角<140°；
--   最近帧 ≤ 转弯完成+2s；T型路口排除
-- ==============================================================================

WITH TimeCalc AS (
    SELECT
        CASE WHEN inter.start_ts < tl.start_ts THEN inter.start_ts ELSE tl.start_ts END AS start_ts,
        CASE WHEN inter.end_ts < tl.end_ts THEN tl.end_ts ELSE inter.end_ts END AS end_ts,
        tl.start_ts AS turn_start_ts,                      -- 左转行为起点（精确）
        tl.end_ts   AS turn_end_ts,                        -- 左转行为终点（精确）
        (SELECT e0.utm_yaw FROM ego e0
          WHERE e0.ts <= tl.start_ts ORDER BY e0.ts DESC LIMIT 1) AS init_yaw,
        (SELECT e9.utm_yaw FROM ego e9
          WHERE e9.ts >= tl.end_ts ORDER BY e9.ts ASC LIMIT 1) AS final_yaw
    FROM range_tag inter
    INNER JOIN range_tag tl
      ON tl.tag_name = 'Turning'
     AND json_extract(tl.param, '$.sub_tag') = 'turn_left'
     AND tl.start_ts <= inter.end_ts
     AND tl.end_ts >= inter.start_ts
     AND (tl.end_ts - tl.start_ts) > 3
    WHERE inter.tag_name = 'Intersection'
      AND json_extract(inter.param, '$.sub_tag') = 'LeftIntersection'
),
ObjFrames AS (
    SELECT
        tc.start_ts AS ev_start,
        tc.end_ts AS ev_end,
        tc.turn_start_ts,
        tc.turn_end_ts,
        tc.init_yaw,
        tc.final_yaw,
        d.obj_id,
        d.type,
        d.ts,
        d.x,
        SQRT(d.x * d.x + d.y * d.y) AS dist,
        -- 首帧距离（逼近判定：目标须在视野内继续靠近，排除已通过的远离车）
        FIRST_VALUE(SQRT(d.x * d.x + d.y * d.y)) OVER w AS dist_first,
        -- 最近帧的 x/y(方位角)与 ts(时间相位)与 h_abs(朝向锚点)
        FIRST_VALUE(d.x)  OVER wc AS x_at_closest,
        FIRST_VALUE(d.y)  OVER wc AS y_at_closest,
        FIRST_VALUE(d.ts) OVER wc AS ts_at_closest,
        FIRST_VALUE(d.heading + e.utm_yaw) OVER wc AS h_abs_closest,
        -- v10.1-F5: 首个 dist<20m 帧的方位角（中距逼近方位：对向走廊<30°，横穿/转弯来自侧面>30°）
        FIRST_VALUE(ATAN2(d.y, d.x)) OVER w20 AS brg_at_20,
        (d.heading + e.utm_yaw) AS h_abs,
        MAX(COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[1]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[2]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[3]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[4]'),0)) AS dr_speed
    FROM TimeCalc tc
    JOIN ego e ON e.ts = d.ts
    JOIN dynamic_obj d ON d.ts BETWEEN tc.start_ts AND tc.end_ts
    WHERE d.type IN ('car', 'bus', 'truck')
      AND (d.x * d.x + d.y * d.y) <= 1600              -- 40m 粗筛
    WINDOW w  AS (PARTITION BY tc.start_ts, d.obj_id ORDER BY d.ts
                  ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING),
           wc AS (PARTITION BY tc.start_ts, d.obj_id ORDER BY SQRT(d.x * d.x + d.y * d.y)
                  ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING),
           w20 AS (PARTITION BY tc.start_ts, d.obj_id
                   ORDER BY CASE WHEN SQRT(d.x * d.x + d.y * d.y) < 20 THEN 0 ELSE 1 END, d.ts
                   ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
),
ConflictObjects AS (
    SELECT
        ev_start AS start_ts,
        ev_end AS end_ts,
        turn_start_ts,
        turn_end_ts,
        obj_id,
        type AS object_type,
        MIN(dist) AS min_dist,
        MAX(dist_first) AS dist_first,
        MAX(x_at_closest) AS x_at_closest,
        MAX(y_at_closest) AS y_at_closest,
        MAX(ts_at_closest) AS ts_at_closest,
        MAX(brg_at_20) AS brg_at_20,
        COUNT(DISTINCT ts) AS frame_count,
        MAX(ts) - MIN(ts) AS duration_seconds,
        MAX(dr_speed) AS max_dr_speed,
        -- 持续运动帧数（修复单帧速度尖峰放行静止/排队车的漏洞，obj522 案）
        SUM(CASE WHEN dr_speed > 5 THEN 1 ELSE 0 END) AS frames_moving,
        ATAN2(AVG(SIN(h_abs - init_yaw)),  AVG(COS(h_abs - init_yaw)))  AS diff_init,
        ATAN2(AVG(SIN(h_abs - final_yaw)), AVG(COS(h_abs - final_yaw))) AS diff_final,
        -- 朝向稳定帧数（最近帧朝向±15°内；v10.3 由±30°收紧至±15°配合0.9占比）
        SUM(CASE WHEN COS(h_abs - h_abs_closest) > 0.966 THEN 1 ELSE 0 END) AS frames_hc,
        MAX(h_abs_closest) AS h_abs_closest,
        -- 转弯完成前出现的帧数（相位门控）
        SUM(CASE WHEN ts <= turn_end_ts + 2 THEN 1 ELSE 0 END) AS frames_during_turn,
        -- v10: 未来轨迹交叉【限 ego 转弯弧线段】——ego 路径点只取 ts>=turn_start_ts
        -- （转弯行为起点之后；接近段与对向车平行会车的距离不再算"交叉"）
        -- |Δts|≤5s 的点对参与（用户模型：双方在场时未来5s车辙重叠才算冲突）
        (SELECT MIN((ox-epx)*(ox-epx)+(oy-epy)*(oy-epy))
         FROM (
            SELECT d2.ts AS ots,
                   json_extract(d2.obs_dr_trajectory,'$.x[0]') AS ox,
                   json_extract(d2.obs_dr_trajectory,'$.y[0]') AS oy
            FROM dynamic_obj d2
            WHERE d2.obj_id = of2.obj_id AND d2.ts BETWEEN of2.ev_start AND of2.ev_end
         )
         CROSS JOIN (
            SELECT e2.ts AS ets,
                   json_extract(e2.ego_dr_trajectory,'$.x[0]') AS epx,
                   json_extract(e2.ego_dr_trajectory,'$.y[0]') AS epy
            FROM ego e2
            WHERE e2.ts BETWEEN of2.ev_start AND of2.ev_end   -- v10.1: 回到全窗口(锚定标签滞后可达15s,s0t案;横穿/转弯车改由F5方位约束剔除)
         )
         WHERE ox IS NOT NULL AND oy IS NOT NULL AND epx IS NOT NULL AND epy IS NOT NULL
           AND ABS(ots - ets) <= 5
        ) AS path_dist_sq
    FROM ObjFrames of2
    GROUP BY ev_start, ev_end, turn_start_ts, turn_end_ts, obj_id, type
    HAVING COUNT(DISTINCT ts) >= 2
       AND max_dr_speed > 5                            -- 运动中
       AND frames_moving >= 2                          -- 持续运动(≥2帧>5km/h)
       AND MIN(dist) < 10                              -- 同时刻最近 <10m
       -- v10.1: 逼近方向约束(最近帧非首帧=目标首见后继续靠近)。纯远离目标最近帧必在首帧(s02案)；
       -- 替代旧"首见-最近>2m"量级阈值——高速目标晚跟踪时幅度不足被误杀(s5t-obj449案)
       AND MIN(ts) < ts_at_closest
       -- v10.3: 朝向稳定性(强)——≥90%帧在最近帧朝向±15°内。
       -- 窗口内真对向车朝向稳定(obj86 8°/金标准0.4°)，窗口内转弯车扫过(obj215 35°/obj228 54°/obj455 52°)。
       -- 注：跟踪器收敛噪声多在窗口外(早期远距帧)，窗口内测量天然抗噪
       AND frames_hc * 1.0 / COUNT(*) >= 0.9
       AND ABS(brg_at_20) < 0.52                       -- v10.1-F5: 中距逼近方位<30°(对向走廊),剔除侧向横穿/转弯车(bc1/bc2案)
       -- (v10.2 移除最近帧方位角<140°检查：贴身通过的对向车最近帧常在侧后141°(s5t-obj449案)；
       --  后方跟随车由 diff_init≈0 结构性排除，无需此闸)
       AND ts_at_closest <= turn_end_ts + 2            -- 最近时刻须在转弯完成前
       AND frames_during_turn >= 2                     -- 转弯完成前出现≥2帧
       AND path_dist_sq < 25                           -- 未来轨迹交叉 <5m
       -- v10: 只认【原路对向】。驶入路对向(diff_final)与转弯弧几何上永不交叉
       -- （ego 弧线并入近侧车道，两线平行），按用户轨迹模型整体删除（bc3 案）；
       -- 顺带解决跟随车在类掉头转角下 diff_final≈同向的误入（s07 案）
       AND ABS(diff_init) > 2.62
),
EgoSpeedAnalysis AS (
    SELECT
        co.*,
        AVG(CASE WHEN e.ts < co.start_ts THEN e.speed END) AS pre_speed,
        AVG(CASE WHEN e.ts BETWEEN co.start_ts AND co.end_ts THEN e.speed END) AS during_speed,
        AVG(CASE WHEN e.ts > co.end_ts THEN e.speed END) AS post_speed,
        MIN(CASE WHEN e.ts BETWEEN co.start_ts AND co.end_ts THEN e.speed END) AS min_during_speed,
        MAX(CASE WHEN e.ts BETWEEN co.start_ts AND co.end_ts THEN e.speed END) AS max_during_speed
    FROM ConflictObjects co
    LEFT JOIN ego e
      ON e.ts BETWEEN co.start_ts - 2 AND co.end_ts + 2
    GROUP BY
        co.start_ts, co.end_ts, co.turn_start_ts, co.turn_end_ts, co.obj_id, co.object_type,
        co.min_dist, co.dist_first, co.x_at_closest, co.y_at_closest, co.ts_at_closest, co.brg_at_20,
        co.frame_count, co.duration_seconds, co.max_dr_speed,
        co.frames_moving, co.diff_init, co.diff_final, co.frames_hc, co.h_abs_closest,
        co.frames_during_turn, co.path_dist_sq
),
FinalResults AS (
    SELECT
        esa.start_ts,
        esa.end_ts,
        esa.end_ts - esa.start_ts AS duration,
        esa.obj_id,
        esa.object_type AS conflict_object,
        CASE
            WHEN esa.pre_speed IS NULL OR esa.during_speed IS NULL THEN 'unclear'
            WHEN esa.min_during_speed < 3 AND esa.max_during_speed < 10 THEN 'mutual_stop'
            WHEN esa.pre_speed - esa.during_speed > 15 AND esa.during_speed < 15 THEN 'ego_yielded_forced'
            WHEN esa.pre_speed - esa.during_speed > 8 AND esa.during_speed > 5 THEN 'ego_yielded_smooth'
            WHEN esa.during_speed > esa.pre_speed THEN 'ego_proceeded_clear'
            ELSE 'ego_proceeded_cautious'
        END AS game_theory_result,
        ROUND(esa.min_dist, 2) AS closest_distance,
        esa.frame_count,
        esa.duration_seconds,
        ROUND(esa.max_dr_speed, 2) AS obj_max_speed_kmh,
        ROUND(SQRT(esa.path_dist_sq), 2) AS path_cross_dist_m,
        ROUND(esa.diff_init, 3) AS diff_init_rad,
        ROUND(esa.diff_final, 3) AS diff_final_rad,
        ROUND(esa.pre_speed, 2) AS pre_speed_kmh,
        ROUND(esa.during_speed, 2) AS during_speed_kmh,
        ROUND(esa.post_speed, 2) AS post_speed_kmh
    FROM EgoSpeedAnalysis esa
),
Scored AS (
    SELECT *,
        CASE game_theory_result
            WHEN 'mutual_stop' THEN 1
            WHEN 'ego_yielded_forced' THEN 2
            WHEN 'ego_yielded_smooth' THEN 3
            WHEN 'ego_proceeded_cautious' THEN 4
            WHEN 'ego_proceeded_clear' THEN 5
            ELSE 6
        END AS severity
    FROM FinalResults
),
merge_marked AS (
    SELECT *,
        CASE
            WHEN start_ts <= LAG(end_ts, 1, -999999) OVER (ORDER BY start_ts)
            THEN 0 ELSE 1
        END AS is_new_group
    FROM Scored
),
merge_grouped AS (
    SELECT *,
        SUM(is_new_group) OVER (ORDER BY start_ts ROWS UNBOUNDED PRECEDING) AS grp_id
    FROM merge_marked
),
merge_repr AS (
    SELECT *,
        FIRST_VALUE(game_theory_result) OVER (
            PARTITION BY grp_id ORDER BY severity, start_ts
        ) AS repr_game
    FROM merge_grouped
),
merged_events AS (
    SELECT
        GROUP_CONCAT(DISTINCT obj_id) AS obj_ids,
        GROUP_CONCAT(DISTINCT conflict_object) AS conflict_objects,
        MAX(repr_game) AS game_theory_result,
        MIN(start_ts) AS start_ts,
        MAX(end_ts) AS end_ts,
        MAX(end_ts) - MIN(start_ts) AS duration,
        MIN(closest_distance) AS closest_distance,
        SUM(frame_count) AS frame_count,
        SUM(duration_seconds) AS duration_seconds,
        MAX(obj_max_speed_kmh) AS obj_max_speed_kmh,
        MIN(path_cross_dist_m) AS path_cross_dist_m
    FROM merge_repr
    GROUP BY grp_id
)
SELECT
    'unprotected_left_turn' AS tag_name,
    obj_ids AS obj_id,
    m.start_ts,
    m.end_ts,
    m.duration,
    m.conflict_objects AS conflict_object,
    m.game_theory_result,
    m.closest_distance,
    m.frame_count,
    m.duration_seconds,
    m.obj_max_speed_kmh,
    m.path_cross_dist_m,
    m.light_color,
    m.is_signalized,
    m.junction_type
FROM (
    SELECT
        m.*,
        (SELECT e2.latest_traffic_light_status FROM ego e2
          WHERE e2.ts BETWEEN m.start_ts AND m.end_ts
            AND e2.latest_traffic_light_status != '未知'
          GROUP BY e2.latest_traffic_light_status
          ORDER BY COUNT(*) DESC LIMIT 1) AS light_color,
        CASE WHEN EXISTS (
            SELECT 1 FROM range_tag rt
            WHERE rt.tag_name = 'TrafficIntersection'
              AND rt.start_ts <= m.end_ts AND rt.end_ts >= m.start_ts
        ) THEN 1 ELSE 0 END AS is_signalized,
        (SELECT e3.specify_topology_tag FROM ego e3
          WHERE e3.ts BETWEEN m.start_ts AND m.end_ts
            AND e3.specify_topology_tag IS NOT NULL
            AND e3.specify_topology_tag != 'none'
          GROUP BY e3.specify_topology_tag
          ORDER BY COUNT(*) DESC LIMIT 1) AS junction_type
    FROM merged_events m
) m
WHERE m.frame_count >= 2
  -- 业务规则：T型路口不算无保护左转
  AND (m.junction_type IS NULL OR m.junction_type NOT IN ('t_junction', 'small_t_junction'))
ORDER BY m.start_ts;
