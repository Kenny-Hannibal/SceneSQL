import json, sys, urllib.request
import urllib.request, json as _j
BASE="http://127.0.0.1:30002"
BATCH="20260702_T68_2471_c5afa57_100w"
def login():
    d=_j.dumps({"username":"gac","password":"gac_data"}).encode()
    q=urllib.request.Request(BASE+"/api/auth/login", data=d, headers={"Content-Type":"application/json"})
    return _j.loads(urllib.request.urlopen(q,timeout=30).read().decode())["access_token"]


FAILS = json.load(open('eval_v3_raw.json'))['cases']
FAILS = [c for c in FAILS if c['verdict'] == 'fail']
V12 = {r['bag_id']: r for r in json.load(open('v12_full.json'))['rows']}

def run(sql, tok, page_size=20000):
    body = json.dumps({"sql": sql, "batch_id": BATCH, "query_mode": "sqlite",
                       "db_limit": 0, "result_limit": 50000, "page": 1, "page_size": page_size}).encode()
    req = urllib.request.Request(f"{BASE}/api/agent/execute-sql", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    return json.loads(urllib.request.urlopen(req, timeout=600).read().decode())

tok = login()
report = []
for c in FAILS:
    bag, t0, t1 = c['bag_id'], c['start_ts'], c['end_ts']
    objs = [o.strip() for o in str(V12[bag]['obj_id']).split(',')]
    pre, post = t0 - 25, t1 + 10
    olist = ','.join(f"'{o}'" for o in objs)
    sql = f"""
SELECT 'EGO' AS kind, ts, NULL AS obj_id, ROUND(speed,2) AS x, ROUND(utm_yaw,3) AS y,
       latest_traffic_light_status AS heading, NULL AS dr_speed, NULL AS type FROM ego
WHERE ts BETWEEN {pre} AND {post}
UNION ALL
SELECT 'OBJ', d.ts, d.obj_id, ROUND(d.x,1), ROUND(d.y,1), ROUND(d.heading,3),
       MAX(COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[1]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[2]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[3]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[4]'),0)), d.type
FROM dynamic_obj d WHERE d.obj_id IN ({olist}) AND d.ts BETWEEN {pre} AND {post}
"""
    res = run(sql, tok)
    rows = [r for r in res.get('rows', []) if r.get('bag_id') == bag]
    ego = sorted([r for r in rows if r['kind'] == 'EGO'], key=lambda r: r['ts'])
    out = {'bag': bag, 'win': [t0, t1], 'objs': {}}
    # ego motion: stop then start
    speeds = [(r['ts'], r['x']) for r in ego]
    out['ego_speed'] = speeds[::max(1, len(speeds)//12)]
    for oid in objs:
        of = sorted([r for r in rows if r['kind'] == 'OBJ' and str(r['obj_id']) == oid], key=lambda r: r['ts'])
        if not of:
            out['objs'][oid] = 'NOT FOUND'; continue
        hs = [r['heading'] for r in of]
        import math
        def dlim(a, b): return abs(math.atan2(math.sin(a-b), math.cos(a-b)))
        sweep = max(dlim(h, hs[len(hs)//2]) for h in hs)
        min_dist = min((r['x']**2 + r['y']**2) ** 0.5 for r in of)
        stopped = sum(1 for r in of if r['ts'] < t0 and (r['dr_speed'] or 0) < 2)
        moving = sum(1 for r in of if (r['dr_speed'] or 0) > 5)
        y_at_min = None; xm = 1e9
        for r in of:
            d = (r['x']**2 + r['y']**2) ** 0.5
            if d < xm: xm, y_at_min, x_at_min = d, r['y'], r['x']
        out['objs'][oid] = {
            'frames': len(of), 'heading_sweep_deg': round(math.degrees(sweep), 1),
            'h_first': hs[0], 'h_mid': hs[len(hs)//2], 'h_last': hs[-1],
            'min_dist': round(min_dist, 1), 'xy_at_min': [round(x_at_min,1), round(y_at_min,1)],
            'stopped_frames_before_t0': stopped, 'moving_frames': moving,
            'first_ts': of[0]['ts'], 'last_ts': of[-1]['ts'],
        }
    report.append(out)
json.dump(report, open('fail_diag.json', 'w'), ensure_ascii=False, indent=1)
for o in report:
    print(o['bag'])
    print('  ego_speed:', [(t, s) for t, s in o['ego_speed']])
    for oid, d in o['objs'].items():
        print(' ', oid, d)
