-- v11 残余冲突分支（独立执行版）：红灯排队等左转，对向末班直行车在等灯期通过，
-- 其通行位置落在 ego 之后实际左转路径上（实证 14GHqPsrhUgo1qw3VOzuqK202606）。
-- 分级闸：便宜闸前置，昂贵的固定系路径距离只对幸存者计算。
-- 输出去重由客户端完成（与主分支事件按转弯窗口重叠去重）。
WITH ResidTurn AS (
    SELECT tl.start_ts AS turn_start, tl.end_ts AS turn_end,
        (SELECT e0.utm_yaw FROM ego e0
          WHERE e0.ts <= tl.start_ts ORDER BY e0.ts DESC LIMIT 1) AS init_yaw,
        (SELECT e0.cumulative_distance FROM ego e0
          WHERE e0.ts <= tl.start_ts ORDER BY e0.ts DESC LIMIT 1) AS init_s
    FROM range_tag tl
    WHERE tl.tag_name = 'Turning'
      AND json_extract(tl.param, '$.sub_tag') = 'turn_left'
      AND (tl.end_ts - tl.start_ts) > 3
),
ResidObjFrames AS (
    SELECT rt.turn_start, rt.turn_end, rt.init_yaw, rt.init_s,
           d.obj_id, d.ts, d.x, d.y,
           SQRT(d.x*d.x + d.y*d.y) AS dist,
           d.x * COS(e.utm_yaw - rt.init_yaw) - d.y * SIN(e.utm_yaw - rt.init_yaw) AS fx,
           d.x * SIN(e.utm_yaw - rt.init_yaw) + d.y * COS(e.utm_yaw - rt.init_yaw) AS fy,
           (d.heading + e.utm_yaw - rt.init_yaw) AS h_rel,
           e.speed AS ego_speed,
           MAX(COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0),
               COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[1]'),0),
               COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[2]'),0),
               COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[3]'),0),
               COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[4]'),0)) AS dr_speed
    FROM ResidTurn rt
    JOIN dynamic_obj d ON d.ts BETWEEN rt.turn_start - 20 AND rt.turn_start + 3
    JOIN ego e ON e.ts = d.ts
    WHERE d.type IN ('car', 'bus', 'truck')
      AND (d.x*d.x + d.y*d.y) <= 2500
),
ResidMean AS (
    SELECT turn_start, obj_id,
           ATAN2(AVG(SIN(h_rel)), AVG(COS(h_rel))) AS h_mean
    FROM ResidObjFrames
    GROUP BY turn_start, obj_id
),
ResidCheap AS (
    SELECT
        f.turn_start, f.turn_end, f.init_yaw, f.init_s, f.obj_id,
        COUNT(DISTINCT f.ts) AS frame_count,
        MIN(f.ts) AS obj_first_ts,
        MIN(f.dist) AS min_dist,
        MAX(f.dr_speed) AS max_dr_speed,
        SUM(CASE WHEN f.dr_speed > 5 THEN 1 ELSE 0 END) AS frames_moving,
        SUM(CASE WHEN f.ego_speed < 1.5 AND f.dist <= 30 THEN 1 ELSE 0 END) AS ego_stopped_frames,
        SUM(CASE WHEN COS(f.h_rel - m.h_mean) > 0.966 THEN 1 ELSE 0 END) AS frames_hc,
        m.h_mean AS diff_init
    FROM ResidObjFrames f
    JOIN ResidMean m ON m.turn_start = f.turn_start AND m.obj_id = f.obj_id
    GROUP BY f.turn_start, f.turn_end, f.init_yaw, f.init_s, f.obj_id
    HAVING COUNT(DISTINCT f.ts) >= 2
       AND MAX(f.dr_speed) > 5
       AND SUM(CASE WHEN f.dr_speed > 5 THEN 1 ELSE 0 END) >= 2
       AND ABS(m.h_mean) > 2.62
       AND SUM(CASE WHEN COS(f.h_rel - m.h_mean) > 0.966 THEN 1 ELSE 0 END) * 1.0
           / COUNT(DISTINCT f.ts) >= 0.9
       AND SUM(CASE WHEN f.ego_speed < 1.5 AND f.dist <= 30 THEN 1 ELSE 0 END) >= 1
       AND SUM(CASE WHEN f.x > 2 THEN 1 ELSE 0 END) >= 1
       AND MAX(f.ts) >= f.turn_start - 15
       AND MIN(f.dist) < 30
),
ResidAgg AS (
    SELECT * FROM (
        SELECT rc.*,
            (SELECT MIN(SQRT((f2.fx - ep.epx)*(f2.fx - ep.epx) + (f2.fy - ep.epy)*(f2.fy - ep.epy)))
             FROM ResidObjFrames f2
             CROSS JOIN (
                SELECT e2.ts AS ets,
                       (e2.cumulative_distance - rc.init_s) * COS(e2.utm_yaw - rc.init_yaw) AS epx,
                       (e2.cumulative_distance - rc.init_s) * SIN(e2.utm_yaw - rc.init_yaw) AS epy
                FROM ego e2
                WHERE e2.ts BETWEEN rc.turn_start - 20 AND rc.turn_end + 5
             ) ep
             WHERE f2.turn_start = rc.turn_start AND f2.obj_id = rc.obj_id
               AND ep.ets >= f2.ts
            ) AS resid_dist
        FROM ResidCheap rc
    )
    WHERE resid_dist < 5
),
ResidEvents AS (
    SELECT
        turn_start,
        turn_end,
        GROUP_CONCAT(DISTINCT obj_id) AS obj_ids,
        MIN(obj_first_ts) AS start_ts,
        MAX(turn_end) AS end_ts,
        MIN(min_dist) AS closest_distance,
        SUM(frame_count) AS frame_count,
        MAX(max_dr_speed) AS obj_max_speed_kmh,
        MIN(resid_dist) AS path_cross_dist_m
    FROM ResidAgg
    GROUP BY turn_start, turn_end
)
SELECT
    'unprotected_left_turn' AS tag_name,
    r.obj_ids AS obj_id,
    r.turn_start,
    r.turn_end,
    r.start_ts,
    r.end_ts,
    r.end_ts - r.start_ts AS duration,
    'car' AS conflict_object,
    'residual_conflict' AS game_theory_result,
    ROUND(r.closest_distance, 2) AS closest_distance,
    r.frame_count,
    r.end_ts - r.start_ts AS duration_seconds,
    ROUND(r.obj_max_speed_kmh, 2) AS obj_max_speed_kmh,
    ROUND(r.path_cross_dist_m, 2) AS path_cross_dist_m,
    (SELECT e2.latest_traffic_light_status FROM ego e2
      WHERE e2.ts BETWEEN r.start_ts AND r.end_ts
        AND e2.latest_traffic_light_status != '未知'
      GROUP BY e2.latest_traffic_light_status
      ORDER BY COUNT(*) DESC LIMIT 1) AS light_color,
    CASE WHEN EXISTS (
        SELECT 1 FROM range_tag rt
        WHERE rt.tag_name = 'TrafficIntersection'
          AND rt.start_ts <= r.end_ts AND rt.end_ts >= r.start_ts
    ) THEN 1 ELSE 0 END AS is_signalized,
    (SELECT e3.specify_topology_tag FROM ego e3
      WHERE e3.ts BETWEEN r.start_ts AND r.end_ts
        AND e3.specify_topology_tag IS NOT NULL
        AND e3.specify_topology_tag != 'none'
      GROUP BY e3.specify_topology_tag
      ORDER BY COUNT(*) DESC LIMIT 1) AS junction_type
FROM ResidEvents r
WHERE r.frame_count >= 2
  AND ((SELECT e3.specify_topology_tag FROM ego e3
        WHERE e3.ts BETWEEN r.start_ts AND r.end_ts
          AND e3.specify_topology_tag IS NOT NULL
          AND e3.specify_topology_tag != 'none'
        GROUP BY e3.specify_topology_tag
        ORDER BY COUNT(*) DESC LIMIT 1) IS NULL
       OR (SELECT e3.specify_topology_tag FROM ego e3
        WHERE e3.ts BETWEEN r.start_ts AND r.end_ts
          AND e3.specify_topology_tag IS NOT NULL
          AND e3.specify_topology_tag != 'none'
        GROUP BY e3.specify_topology_tag
        ORDER BY COUNT(*) DESC LIMIT 1) NOT IN ('t_junction', 'small_t_junction'))
ORDER BY r.start_ts;
