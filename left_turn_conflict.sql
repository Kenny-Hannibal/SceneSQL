-- ==============================================================================
-- 场景：左转冲突检测（融合版）
-- 说明：
--   1. 完全保留原版左转冲突检测逻辑；
--   2. conflict_object 仅对 object_type 做基础映射；
--   3. 不引入行为语义，不做扩展推断。
-- ==============================================================================

WITH TimeCalc AS (
    SELECT 
        iv.tag_name,
        CASE WHEN inter.start_ts < iv.start_ts THEN inter.start_ts ELSE iv.start_ts END AS start_ts,
        CASE WHEN inter.end_ts < iv.end_ts THEN iv.end_ts ELSE iv.end_ts END AS end_ts,
        iv.param,
        iv.start_ts AS inter_start_ts,
        iv.end_ts AS inter_end_ts
    FROM range_tag inter
    INNER JOIN range_tag iv 
      ON ABS(iv.start_ts - inter.start_ts) < (inter.end_ts - inter.start_ts) / 2.0
     AND ABS(iv.end_ts - inter.end_ts) < (inter.end_ts - inter.start_ts) / 2.0
     AND (iv.end_ts - iv.start_ts) > 3
    WHERE inter.tag_name = 'Intersection' 
      AND json_extract(inter.param, '$.sub_tag') IN ('LeftIntersection', 'LuturnIntersection')
),
ConflictObjects AS (
    SELECT 
        tc.start_ts,
        tc.end_ts,
        tc.param,
        tc.tag_name AS interaction_type,
        tc.inter_start_ts,
        tc.inter_end_ts,
        d.obj_id,
        d.type AS object_type,
        MIN(d.x * d.x + d.y * d.y) AS min_dist_sq,
        AVG(ABS(d.heading)) AS avg_heading,
        COUNT(DISTINCT d.ts) AS frame_count,
        MAX(d.ts) - MIN(d.ts) AS duration_seconds
    FROM TimeCalc tc
    CROSS JOIN dynamic_obj d
    JOIN ego e ON e.ts = d.ts
    WHERE d.ts BETWEEN tc.start_ts AND tc.end_ts
      AND e.speed > 0.1
      AND (d.x * d.x + d.y * d.y) <= POWER((e.speed / 3.6) * 2.0, 2)
      AND ABS(d.heading) BETWEEN 1.0472 AND 1.5708
    GROUP BY 
        tc.start_ts, tc.end_ts, tc.param, tc.tag_name,
        tc.inter_start_ts, tc.inter_end_ts, d.obj_id, d.type
    HAVING MAX(d.ts) - MIN(d.ts) >= 1
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
        co.start_ts, co.end_ts, co.param, co.interaction_type,
        co.inter_start_ts, co.inter_end_ts, co.obj_id, co.object_type,
        co.min_dist_sq, co.avg_heading, co.frame_count, co.duration_seconds
),
FinalResults AS (
    SELECT 
        esa.start_ts,
        esa.end_ts,
        esa.end_ts - esa.start_ts AS duration,
        esa.obj_id,
        -- conflict_object: 仅对 object_type 做基础转换
        CASE 
            WHEN esa.object_type = 'PEDESTRIAN' THEN 'pedestrian'
            WHEN esa.object_type = 'CYCLIST' THEN 'cyclist'
            WHEN esa.object_type IN ('car', 'sedan', 'suv') THEN 'car'
            WHEN esa.object_type IN ('truck', 'bus') THEN 'truck'
            WHEN esa.object_type IS NOT NULL THEN 'other_vehicle'
            ELSE 'unknown'
        END AS conflict_object,
        -- game_theory_result: 博弈结果
        CASE 
            WHEN esa.pre_speed IS NULL OR esa.during_speed IS NULL THEN 'unclear'
            WHEN esa.min_during_speed < 3 AND esa.max_during_speed < 10 THEN 'mutual_stop'
            WHEN esa.pre_speed - esa.during_speed > 15 AND esa.during_speed < 15 THEN 'ego_yielded_forced'
            WHEN esa.pre_speed - esa.during_speed > 8 AND esa.during_speed > 5 THEN 'ego_yielded_smooth'
            WHEN esa.during_speed > esa.pre_speed THEN 'ego_proceeded_clear'
            ELSE 'ego_proceeded_cautious'
        END AS game_theory_result,
        ROUND(SQRT(esa.min_dist_sq), 2) AS closest_distance,
        ROUND(esa.avg_heading, 3) AS avg_heading_rad,
        esa.frame_count,
        esa.duration_seconds,
        ROUND(esa.pre_speed, 2) AS pre_speed_kmh,
        ROUND(esa.during_speed, 2) AS during_speed_kmh,
        ROUND(esa.post_speed, 2) AS post_speed_kmh
    FROM EgoSpeedAnalysis esa
),
-- 合并相同类型+博弈结果的时间重叠事件
merge_marked AS (
    SELECT 
        obj_id,
        conflict_object,
        game_theory_result,
        start_ts,
        end_ts,
        closest_distance,
        frame_count,
        duration_seconds,
        CASE 
            WHEN start_ts <= LAG(end_ts, 1, -999999) OVER (PARTITION BY conflict_object, game_theory_result ORDER BY start_ts) 
            THEN 0 ELSE 1 
        END AS is_new_group
    FROM FinalResults
),
merge_grouped AS (
    SELECT 
        obj_id,
        conflict_object,
        game_theory_result,
        start_ts,
        end_ts,
        closest_distance,
        frame_count,
        duration_seconds,
        SUM(is_new_group) OVER (PARTITION BY conflict_object, game_theory_result ORDER BY start_ts ROWS UNBOUNDED PRECEDING) AS grp_id
    FROM merge_marked
),
merged_events AS (
    SELECT 
        GROUP_CONCAT(DISTINCT obj_id) AS obj_ids,
        conflict_object,
        game_theory_result,
        MIN(start_ts) AS start_ts,
        MAX(end_ts) AS end_ts,
        MAX(end_ts) - MIN(start_ts) AS duration,
        MIN(closest_distance) AS closest_distance,
        SUM(frame_count) AS frame_count,
        SUM(duration_seconds) AS duration_seconds
    FROM merge_grouped
    GROUP BY conflict_object, game_theory_result, grp_id
)
SELECT 
    'left_turn_conflict' AS tag_name,
    obj_ids AS obj_id,
    start_ts,
    end_ts,
    duration,
    conflict_object,
    game_theory_result,
    closest_distance,
    frame_count,
    duration_seconds
FROM merged_events
ORDER BY start_ts;
