-- v11 诊断②：对 48 个等灯+类对向候选，用 ego 转弯起点锚定系重构轨迹交叉
-- 修复机理：ego 系跨时间戳比较随 ego 移动/自转失真；固定系下 |Δts|≤12s
WITH TimeCalc AS (
    SELECT
        CASE WHEN inter.start_ts < tl.start_ts THEN inter.start_ts ELSE tl.start_ts END AS start_ts,
        CASE WHEN inter.end_ts < tl.end_ts THEN tl.end_ts ELSE inter.end_ts END AS end_ts,
        tl.start_ts AS turn_start_ts,
        tl.end_ts   AS turn_end_ts,
        (SELECT e0.utm_yaw FROM ego e0
          WHERE e0.ts <= tl.start_ts ORDER BY e0.ts DESC LIMIT 1) AS init_yaw,
        (SELECT e0.cumulative_distance FROM ego e0
          WHERE e0.ts <= tl.start_ts ORDER BY e0.ts DESC LIMIT 1) AS init_s
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
        tc.start_ts AS ev_start, tc.end_ts AS ev_end,
        tc.turn_start_ts, tc.turn_end_ts, tc.init_yaw, tc.init_s,
        d.obj_id, d.ts, d.x, d.y, d.heading,
        SQRT(d.x * d.x + d.y * d.y) AS dist,
        e.utm_yaw AS ego_yaw,
        MAX(COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[1]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[2]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[3]'),0),
            COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[4]'),0)) AS dr_speed
    FROM TimeCalc tc
    JOIN dynamic_obj d ON d.ts BETWEEN tc.start_ts AND tc.end_ts
    JOIN ego e ON e.ts = d.ts
    WHERE d.type IN ('car', 'bus', 'truck')
      AND (d.x * d.x + d.y * d.y) <= 1600
),
Cand AS (
    SELECT ev_start, ev_end, turn_start_ts, turn_end_ts, init_yaw, init_s, obj_id,
           COUNT(DISTINCT ts) AS frame_count,
           MAX(dr_speed) AS max_dr_speed,
           SUM(CASE WHEN dr_speed > 5 THEN 1 ELSE 0 END) AS frames_moving,
           MIN(dist) AS min_dist
    FROM ObjFrames
    GROUP BY ev_start, ev_end, turn_start_ts, turn_end_ts, init_yaw, init_s, obj_id
    HAVING COUNT(DISTINCT ts) >= 2
       AND (SELECT MIN(e3.speed) FROM ego e3
             WHERE e3.ts BETWEEN ev_start AND turn_start_ts) < 1.5
),
CandDir AS (
    SELECT c.*,
        ATAN2(AVG(SIN((of.heading + of.ego_yaw) - c.init_yaw)),
              AVG(COS((of.heading + of.ego_yaw) - c.init_yaw))) AS diff_init
    FROM Cand c JOIN ObjFrames of
      ON of.ev_start = c.ev_start AND of.obj_id = c.obj_id
    GROUP BY c.ev_start, c.ev_end, c.turn_start_ts, c.turn_end_ts,
             c.init_yaw, c.init_s, c.obj_id
    HAVING ABS(ATAN2(AVG(SIN((of.heading + of.ego_yaw) - c.init_yaw)),
                     AVG(COS((of.heading + of.ego_yaw) - c.init_yaw)))) > 2.2
),
FixedPath AS (
    SELECT cd.ev_start, cd.ev_end, cd.obj_id,
        MIN((ofx - epx)*(ofx - epx) + (ofy - epy)*(ofy - epy)) AS fd_sq12,
        MIN(CASE WHEN ABS(ots - ets) <= 5 THEN (ofx - epx)*(ofx - epx) + (ofy - epy)*(ofy - epy) END) AS fd_sq5
    FROM CandDir cd
    JOIN (
        SELECT of2.ev_start, of2.obj_id, of2.ts AS ots,
               of2.x * COS(of2.ego_yaw - of2.init_yaw) - of2.y * SIN(of2.ego_yaw - of2.init_yaw) AS ofx,
               of2.x * SIN(of2.ego_yaw - of2.init_yaw) + of2.y * COS(of2.ego_yaw - of2.init_yaw) AS ofy
        FROM ObjFrames of2, CandDir cd2
        WHERE cd2.ev_start = of2.ev_start AND cd2.obj_id = of2.obj_id
    ) ob ON ob.ev_start = cd.ev_start AND ob.obj_id = cd.obj_id
    JOIN (
        SELECT tc2.start_ts AS ev_start, e2.ts AS ets,
               (e2.cumulative_distance - tc2.init_s) * COS(e2.utm_yaw - tc2.init_yaw) AS epx,
               (e2.cumulative_distance - tc2.init_s) * SIN(e2.utm_yaw - tc2.init_yaw) AS epy
        FROM ego e2 JOIN TimeCalc tc2 ON e2.ts BETWEEN tc2.start_ts AND tc2.end_ts
    ) eg ON eg.ev_start = cd.ev_start
    WHERE ABS(ob.ots - eg.ets) <= 12
    GROUP BY cd.ev_start, cd.ev_end, cd.obj_id
)
SELECT cd.ev_start AS start_ts, cd.ev_end AS end_ts, cd.turn_start_ts, cd.turn_end_ts,
       cd.obj_id, ROUND(cd.min_dist,2) AS min_dist, ROUND(cd.max_dr_speed,1) AS max_speed,
       cd.frames_moving, ROUND(cd.diff_init,3) AS diff_init,
       ROUND(SQRT(fp.fd_sq5),2) AS fixed_path_5s, ROUND(SQRT(fp.fd_sq12),2) AS fixed_path_12s,
       (SELECT MIN(e3.speed) FROM ego e3 WHERE e3.ts BETWEEN cd.ev_start AND cd.turn_start_ts) AS ego_pre_min,
       (SELECT e3.latest_traffic_light_status FROM ego e3
         WHERE e3.ts BETWEEN cd.ev_start AND cd.turn_start_ts
           AND e3.latest_traffic_light_status != '未知'
         GROUP BY e3.latest_traffic_light_status ORDER BY COUNT(*) DESC LIMIT 1) AS pre_light
FROM CandDir cd JOIN FixedPath fp
  ON fp.ev_start = cd.ev_start AND fp.obj_id = cd.obj_id
