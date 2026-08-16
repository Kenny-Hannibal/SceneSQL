-- ==============================================================================
-- 场景：无保护左转 v12（v10.4 主分支放宽判据 + 起步后进入冲突区排除）
--
-- 语义（用户 2026-08-04 对齐）：无保护左转是路口静态属性——左转与对向直行
--   共享绿灯、无专用保护相位。拿不到路口规则，用轨迹交互作代理证据：
--   【自车正在左转的同时刻】有原路对向直行车与自车左转轨迹发生时空重叠。
--   非同时刻的轨迹关系（等红灯时对向车流经过、变灯瞬间的末班直行车）属于
--   有保护路口的正常相位，一律不召回。
--
-- v12 相对 v11：
--   1) 删除残差分支（v11 误把"等灯期对向车逼近"当冲突，大量召回有保护路口）
--   2) 新增排除：自车起步前已在冲突区(30m内)的对向车不算冲突车
--      ——排除等灯期间穿过的对向车流与变灯末班车（上一相位尾巴）
--   3) 放宽判据提召回（同时刻要求不变）：
--      closest 10→15m；F5方位 30→35°；对向角 150→140°；
--      路径交叉 5→6m；航向稳定占比 90→85%（±15°不变）
--
-- 判据链（保留自 v10.4）：
--   锚点: range_tag Turning(sub_tag='turn_left') × Intersection(LeftIntersection)
--         重叠（掉头场景整体剔除；标签滞后由全窗口轨迹交叉容忍）
--   对向: 仅原路对向 |diff_init|>140°
--   直行: ≥85% 帧在最近帧朝向±15°内
--   方位: 首个 dist<20m 帧方位角 <35°（F5 对向走廊）
--   运动: DR speed>5km/h 且≥2帧（静止/排队/尖峰剔除）
--   逼近: 最近帧非首帧（首见后继续靠近；纯远离目标剔除）
--   同时刻最近 <15m；最近帧 ≤ 转弯完成+2s；仅 car/bus/truck；T型路口排除
--   轨迹交叉: |Δts|≤5s 的 ego/obj DR idx0 路径点对 min 距离 <6m
-- ==============================================================================

WITH TimeCalc AS (
    SELECT
        CASE WHEN inter.start_ts < tl.start_ts THEN inter.start_ts ELSE tl.start_ts END AS start_ts,
        CASE WHEN inter.end_ts < tl.end_ts THEN tl.end_ts ELSE inter.end_ts END AS end_ts,
        tl.start_ts AS turn_start_ts,
        tl.end_ts   AS turn_end_ts,
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
-- v12: 自车起步时刻 = 转弯前最后一次停车之后首次动起来。
-- 有停车（等灯/排队）时启用"起步后进入冲突区"排除；无停车则不排除。
EgoStart AS (
    SELECT
        tc.*,
        (SELECT MAX(e.ts) FROM ego e
          WHERE e.ts <= tc.turn_start_ts AND e.ts >= tc.turn_start_ts - 60
            AND e.speed < 1) AS last_stop_ts,
        CASE WHEN (SELECT MAX(e.ts) FROM ego e
                    WHERE e.ts <= tc.turn_start_ts AND e.ts >= tc.turn_start_ts - 60
                      AND e.speed < 1) IS NOT NULL
             THEN (SELECT MIN(e.ts) FROM ego e
                    WHERE e.ts > (SELECT MAX(e2.ts) FROM ego e2
                                   WHERE e2.ts <= tc.turn_start_ts AND e2.ts >= tc.turn_start_ts - 60
                                     AND e2.speed < 1)
                      AND e.ts <= tc.turn_end_ts AND e.speed > 1.5)
             ELSE NULL END AS ego_move_start
    FROM TimeCalc tc
),
ObjFrames AS (
    SELECT
        tc.start_ts AS ev_start,
        tc.end_ts AS ev_end,
        tc.turn_start_ts,
        tc.turn_end_ts,
        tc.init_yaw,
        tc.final_yaw,
        tc.ego_move_start,
        d.obj_id,
        d.type,
        d.ts,
        d.x,
        SQRT(d.x * d.x + d.y * d.y) AS dist,
        FIRST_VALUE(SQRT(d.x * d.x + d.y * d.y)) OVER w AS dist_first,
        FIRST_VALUE(d.x)  OVER wc AS x_at_closest,
        FIRST_VALUE(d.y)  OVER wc AS y_at_closest,
        FIRST_VALUE(d.ts) OVER wc AS ts_at_closest,
        FIRST_VALUE(d.heading + e.utm_yaw) OVER wc AS h_abs_closest,
        FIRST_VALUE(ATAN2(d.y, d.x)) OVER w20 AS brg_at_20,
        (d.heading + e.utm_yaw) AS h_abs,
        MAX(COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[1]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[2]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[3]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[4]'),0)) AS dr_speed
    FROM EgoStart tc
    JOIN ego e ON e.ts = d.ts
    JOIN dynamic_obj d ON d.ts BETWEEN tc.start_ts AND tc.end_ts
    WHERE d.type IN ('car', 'bus', 'truck')
      AND (d.x * d.x + d.y * d.y) <= 2500              -- 50m 粗筛（v12：40→50，保证30m进入时刻可见）
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
        MAX(ego_move_start) AS ego_move_start,
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
        SUM(CASE WHEN dr_speed > 5 THEN 1 ELSE 0 END) AS frames_moving,
        ATAN2(AVG(SIN(h_abs - init_yaw)),  AVG(COS(h_abs - init_yaw)))  AS diff_init,
        ATAN2(AVG(SIN(h_abs - final_yaw)), AVG(COS(h_abs - final_yaw))) AS diff_final,
        SUM(CASE WHEN COS(h_abs - h_abs_closest) > 0.966 THEN 1 ELSE 0 END) AS frames_hc,
        MAX(h_abs_closest) AS h_abs_closest,
        SUM(CASE WHEN ts <= turn_end_ts + 2 THEN 1 ELSE 0 END) AS frames_during_turn,
        -- v12: 冲突区进入时刻（首个 dist<30m 帧），用于"起步后进入"排除
        MIN(CASE WHEN dist < 30 THEN ts END) AS zone_entry_ts,
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
            WHERE e2.ts BETWEEN of2.ev_start AND of2.ev_end
         )
         WHERE ox IS NOT NULL AND oy IS NOT NULL AND epx IS NOT NULL AND epy IS NOT NULL
           AND ABS(ots - ets) <= 5
        ) AS path_dist_sq
    FROM ObjFrames of2
    GROUP BY ev_start, ev_end, turn_start_ts, turn_end_ts, obj_id, type
    HAVING COUNT(DISTINCT ts) >= 2
       AND max_dr_speed > 5
       AND frames_moving >= 2
       AND MIN(dist) < 15                              -- v12: 10→15m
       AND MIN(ts) < ts_at_closest                     -- 逼近（最近帧非首帧）
       AND frames_hc * 1.0 / COUNT(*) >= 0.85          -- v12: 0.9→0.85（±15°不变）
       AND ABS(brg_at_20) < 0.61                       -- v12: 30°→35°
       AND ts_at_closest <= turn_end_ts + 2
       AND frames_during_turn >= 2
       AND path_dist_sq < 36                           -- v12: 5m→6m（|Δts|≤5s 不变）
       AND ABS(diff_init) > 2.44                       -- v12: 150°→140°
       -- v12 核心排除：自车起步前已进入冲突区(30m)的对向车 = 上一相位尾巴
       -- （等灯期穿过的对向车流 / 变灯末班车），属有保护路口正常相位，不召回
       AND (MAX(ego_move_start) IS NULL
            OR MIN(CASE WHEN dist < 30 THEN ts END) >= MAX(ego_move_start))
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
        co.start_ts, co.end_ts, co.turn_start_ts, co.turn_end_ts, co.ego_move_start,
        co.obj_id, co.object_type, co.zone_entry_ts,
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
  AND (m.junction_type IS NULL OR m.junction_type NOT IN ('t_junction', 'small_t_junction'))
ORDER BY m.start_ts;
