import json, sys
from api_client import run_sql

BAG = sys.argv[1]; TS0 = int(sys.argv[2]); TS1 = int(sys.argv[3]); OBJ = sys.argv[4]
pre = TS0 - 25

ego_sql = f"""
SELECT ts, ROUND(speed,2) speed, ROUND(utm_yaw,3) yaw, latest_traffic_light_status light,
       ROUND(cumulative_distance,1) cum_dist
FROM ego WHERE ts BETWEEN {pre} AND {TS1+8} ORDER BY ts
"""
obj_sql = f"""
SELECT d.ts, d.obj_id, ROUND(d.x,1) x, ROUND(d.y,1) y, ROUND(d.heading,3) heading, d.type,
       MAX(COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[1]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[2]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[3]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[4]'),0)) AS dr_speed
FROM dynamic_obj d WHERE d.obj_id = {OBJ} AND d.ts BETWEEN {pre} AND {TS1+8} ORDER BY d.ts
"""
tags_sql = f"""
SELECT tag_name, json_extract(param,'$.sub_tag') sub, start_ts, end_ts
FROM range_tag WHERE start_ts <= {TS1+10} AND end_ts >= {pre}
  AND tag_name IN ('Turning','Intersection','TrafficIntersection','INTERSECTION_LEFTTURN')
ORDER BY start_ts
"""
for label, sql in [("TAGS", tags_sql), ("EGO", ego_sql), ("OBJ", obj_sql)]:
    res = run_sql(sql, result_limit=5000, db_limit=500)
    hit = [r for r in res['rows'] if r.get('bag_id')==BAG] or res['rows']
    print(f"===== {label} ({len(hit)}) =====")
    for r in hit[:60]:
        print(" ", {k:v for k,v in r.items() if k!='bag_id'})
