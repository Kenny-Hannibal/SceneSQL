-- v11 召回诊断：候选池（v10.4 判据全特征输出，不加 HAVING 闸，仅 frame_count>=2）
-- 每行 = 一个事件窗口内一个 car/bus/truck 候选目标，带全部判据特征 + ego 等灯特征
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
    FROM TimeCalc tc
    JOIN ego e ON e.ts = d.ts
    JOIN dynamic_obj d ON d.ts BETWEEN tc.start_ts AND tc.end_ts
    WHERE d.type IN ('car', 'bus', 'truck')
      AND (d.x * d.x + d.y * d.y) <= 1600
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
        MAX(ts_at_closest) AS ts_at_closest,
        MAX(brg_at_20) AS brg_at_20,
        COUNT(DISTINCT ts) AS frame_count,
        MAX(dr_speed) AS max_dr_speed,
        SUM(CASE WHEN dr_speed > 5 THEN 1 ELSE 0 END) AS frames_moving,
        ATAN2(AVG(SIN(h_abs - init_yaw)),  AVG(COS(h_abs - init_yaw)))  AS diff_init,
        SUM(CASE WHEN COS(h_abs - h_abs_closest) > 0.966 THEN 1 ELSE 0 END) AS frames_hc,
        SUM(CASE WHEN ts <= turn_end_ts + 2 THEN 1 ELSE 0 END) AS frames_during_turn,
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
)
SELECT
    co.start_ts, co.end_ts, co.turn_start_ts, co.turn_end_ts, co.obj_id, co.object_type,
    ROUND(co.min_dist, 2) AS min_dist,
    ROUND(co.dist_first, 2) AS dist_first,
    co.ts_at_closest,
    ROUND(co.brg_at_20, 3) AS brg_at_20,
    co.frame_count,
    ROUND(co.max_dr_speed, 1) AS max_dr_speed,
    co.frames_moving,
    ROUND(co.diff_init, 3) AS diff_init,
    ROUND(co.frames_hc * 1.0 / co.frame_count, 3) AS heading_stability,
    co.frames_during_turn,
    ROUND(SQRT(co.path_dist_sq), 2) AS path_cross_m,
    -- ego 等灯特征：转弯开始前事件窗口内的 ego 速度
    (SELECT AVG(e3.speed) FROM ego e3
      WHERE e3.ts BETWEEN co.start_ts AND co.turn_start_ts) AS ego_pre_speed,
    (SELECT MIN(e3.speed) FROM ego e3
      WHERE e3.ts BETWEEN co.start_ts AND co.turn_start_ts) AS ego_pre_min_speed,
    (SELECT e3.latest_traffic_light_status FROM ego e3
      WHERE e3.ts BETWEEN co.start_ts AND co.turn_start_ts
        AND e3.latest_traffic_light_status != '未知'
      GROUP BY e3.latest_traffic_light_status ORDER BY COUNT(*) DESC LIMIT 1) AS pre_light,
    -- 逼近判定：首帧是否就是最近帧
    CASE WHEN co.ts_at_closest > co.start_ts THEN 1 ELSE 0 END AS is_approaching,
    -- 各闸通过标记
    CASE WHEN co.max_dr_speed > 5 AND co.frames_moving >= 2 THEN 1 ELSE 0 END AS g_motion,
    CASE WHEN co.min_dist < 10 THEN 1 ELSE 0 END AS g_close,
    CASE WHEN co.ts_at_closest > co.start_ts THEN 1 ELSE 0 END AS g_approach,
    CASE WHEN co.frames_hc * 1.0 / co.frame_count >= 0.9 THEN 1 ELSE 0 END AS g_heading,
    CASE WHEN ABS(co.brg_at_20) < 0.52 THEN 1 ELSE 0 END AS g_brg,
    CASE WHEN co.ts_at_closest <= co.turn_end_ts + 2 THEN 1 ELSE 0 END AS g_phase1,
    CASE WHEN co.frames_during_turn >= 2 THEN 1 ELSE 0 END AS g_phase2,
    CASE WHEN co.path_dist_sq < 25 THEN 1 ELSE 0 END AS g_path,
    CASE WHEN ABS(co.diff_init) > 2.62 THEN 1 ELSE 0 END AS g_oncoming
FROM ConflictObjects co
