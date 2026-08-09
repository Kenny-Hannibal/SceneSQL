import json, urllib.request, math
BASE = "http://127.0.0.1:30002"
BATCH = "20260702_T68_2471_c5afa57_100w"

def login():
    d = json.dumps({"username": "gac", "password": "gac_data"}).encode()
    q = urllib.request.Request(BASE + "/api/auth/login", data=d, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(q, timeout=30).read().decode())["access_token"]

def run(sql, tok):
    body = json.dumps({"sql": sql, "batch_id": BATCH, "query_mode": "sqlite",
                       "db_limit": 0, "result_limit": 50000, "page": 1, "page_size": 50000}).encode()
    req = urllib.request.Request(BASE + "/api/agent/execute-sql", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    return json.loads(urllib.request.urlopen(req, timeout=600).read().decode())

def dlim(a, b):
    return math.atan2(math.sin(a - b), math.cos(a - b))

FAILS = [c for c in json.load(open('eval_v3_raw.json'))['cases'] if c['verdict'] == 'fail']
V12 = {r['bag_id']: r for r in json.load(open('v12_full.json'))['rows']}
tok = login()

for c in FAILS:
    bag, t0, t1 = c['bag_id'], c['start_ts'], c['end_ts']
    v = V12[bag]
    objs = [o.strip() for o in str(v['obj_id']).split(',')]
    pre, post = t0 - 25, t1 + 10
    olist = ','.join(f"'{o}'" for o in objs)
    sql = f"""
SELECT 'EGO' AS k, ts, NULL AS oid, ROUND(x,0) AS a, ROUND(utm_yaw,3) AS b, ROUND(speed,1) AS c, NULL AS d
FROM (SELECT ts, utm_yaw, speed, json_extract(ego_dr_trajectory,'$.x[0]') AS x FROM ego)
WHERE ts BETWEEN {pre} AND {post}
UNION ALL
SELECT 'OBJ', d.ts, d.obj_id, ROUND(d.x,1), ROUND(d.y,1), ROUND(d.heading,3),
       MAX(COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[1]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[2]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[3]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[4]'),0))
FROM dynamic_obj d WHERE d.obj_id IN ({olist}) AND d.ts BETWEEN {pre} AND {post}
"""
    rows = [r for r in run(sql, tok).get('rows', []) if r.get('bag_id') == bag]
    ego = sorted([r for r in rows if r['k'] == 'EGO'], key=lambda r: r['ts'])
    # init_yaw: ego yaw at turn_start (v12 start_ts)
    ts0 = v['start_ts']
    init_yaw = next((r['b'] for r in reversed(ego) if r['ts'] <= ts0), None)
    # ego move start
    stops = [r['ts'] for r in ego if r['ts'] <= ts0 and (r['c'] or 0) < 1]
    move_start = None
    if stops:
        ls = max(stops)
        ms = [r['ts'] for r in ego if r['ts'] > ls and (r['c'] or 0) > 1.5]
        move_start = min(ms) if ms else None
    print(f"===== {bag} init_yaw={init_yaw} move_start={move_start} turn_start={ts0} =====")
    print("  ego:", [(r['ts'] % 1000, r['c']) for r in ego])
    for oid in objs:
        of = sorted([r for r in rows if r['k'] == 'OBJ' and r['oid'] == oid], key=lambda r: r['ts'])
        if not of:
            print(f"  {oid}: NO DATA"); continue
        print(f"  obj {oid}:")
        hs = [r['c'] for r in of]
        hrel = [dlim(h, init_yaw) for h in hs]
        # circular range of hrel
        pairs = [abs(dlim(a, b)) for i, a in enumerate(hrel) for b in hrel[i + 1:]]
        sweep = max(pairs) if pairs else 0
        print(f"    hrel_to_init (deg): {[round(math.degrees(x),1) for x in hrel]}")
        print(f"    circular sweep: {round(math.degrees(sweep),1)} deg")
        print(f"    dr_speed: {[round(r['d'] or 0,1) for r in of]}")
        print(f"    xy: {[(r['a'], r['b']) for r in of]}")
        print(f"    ts: {[r['ts'] % 1000 for r in of]}")
        stopped_before_closest = sum(1 for r in of if (r['d'] or 0) < 2)
        print(f"    low-speed(<2) frames: {stopped_before_closest}/{len(of)}")
