-- v11 诊断③：找用户 bad case —— 红灯排队等左转，对向末班直行车在等灯期通过，
-- 其通行位置落在 ego 之后实际左转路径上（残余冲突）。
WITH TurnWins AS (
    SELECT tl.start_ts AS turn_start, tl.end_ts AS turn_end,
        (SELECT e0.utm_yaw FROM ego e0
          WHERE e0.ts <= tl.start_ts ORDER BY e0.ts DESC LIMIT 1) AS init_yaw,
        (SELECT e0.cumulative_distance FROM ego e0
          WHERE e0.ts <= tl.start_ts ORDER BY e0.ts DESC LIMIT 1) AS init_s
    FROM range_tag tl
    WHERE tl.tag_name = 'Turning'
      AND json_extract(tl.param, '$.sub_tag') = 'turn_left'
      AND (tl.end_ts - tl.start_ts) > 3
      AND EXISTS (SELECT 1 FROM ego e
                   WHERE e.ts BETWEEN tl.start_ts - 30 AND tl.start_ts
                     AND e.speed < 1.0
                     AND e.latest_traffic_light_status = '红')
),
ObjPass AS (
    SELECT tw.turn_start, tw.turn_end, d.obj_id, d.ts,
           SQRT(d.x*d.x + d.y*d.y) AS dist,
           d.x * COS(e.utm_yaw - tw.init_yaw) - d.y * SIN(e.utm_yaw - tw.init_yaw) AS fx,
           d.y * COS(e.utm_yaw - tw.init_yaw) + d.x * SIN(e.utm_yaw - tw.init_yaw) AS fy,
           (d.heading + e.utm_yaw - tw.init_yaw) AS h_rel_abs,
           MAX(COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0),
               COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[1]'),0),
               COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[2]'),0),
               COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[3]'),0),
               COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[4]'),0)) AS dr_speed
    FROM TurnWins tw
    JOIN dynamic_obj d ON d.ts BETWEEN tw.turn_start - 20 AND tw.turn_start + 3
    JOIN ego e ON e.ts = d.ts
    WHERE d.type IN ('car','bus','truck')
      AND (d.x*d.x + d.y*d.y) <= 2500
),
ObjResid AS (
    SELECT op.turn_start, op.turn_end, op.obj_id, op.ts, op.dist, op.dr_speed,
           op.h_rel_abs, op.fx, op.fy,
           -- ego 之后实际路径（固定系）到该 obj 帧位置的最小距离
           (SELECT MIN(SQRT((op.fx - epx)*(op.fx - epx) + (op.fy - epy)*(op.fy - epy)))
            FROM (SELECT (e2.cumulative_distance - tw2.init_s) * COS(e2.utm_yaw - tw2.init_yaw) AS epx,
                         (e2.cumulative_distance - tw2.init_s) * SIN(e2.utm_yaw - tw2.init_yaw) AS epy
                  FROM ego e2 JOIN TurnWins tw2 ON tw2.turn_start = op.turn_start
                  WHERE e2.ts BETWEEN op.ts AND op.turn_end + 5
                 )
           ) AS resid_dist
    FROM ObjPass op
),
ObjAgg AS (
    SELECT turn_start, turn_end, obj_id,
           COUNT(DISTINCT ts) AS n_frames,
           MAX(dr_speed) AS max_speed,
           ROUND(MIN(dist),1) AS min_dist,
           ROUND(MIN(resid_dist),2) AS min_resid,
           ROUND(ATAN2(AVG(SIN(h_rel_abs)), AVG(COS(h_rel_abs))),3) AS diff_init
    FROM ObjResid
    GROUP BY turn_start, turn_end, obj_id
    HAVING COUNT(DISTINCT ts) >= 2
       AND MAX(dr_speed) > 5
       AND ABS(ATAN2(AVG(SIN(h_rel_abs)), AVG(COS(h_rel_abs)))) > 2.62
       AND MIN(resid_dist) < 5
)
SELECT turn_start, turn_end, obj_id, n_frames, max_speed, min_dist, min_resid, diff_init
FROM ObjAgg
ORDER BY min_resid
