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

tok = login()
FAILS = [c for c in json.load(open('eval_v3_raw.json'))['cases'] if c['verdict'] == 'fail']
V12 = {r['bag_id']: r for r in json.load(open('v12_full.json'))['rows']}
report = {}

for c in FAILS:
    bag, t0, t1 = c['bag_id'], c['start_ts'], c['end_ts']
    v = V12[bag]
    objs = [o.strip() for o in str(v['obj_id']).split(',')]
    pre, post = t0 - 30, t1 + 10
    olist = ','.join(f"'{o}'" for o in objs)
    sql_ego = f"SELECT ts, utm_yaw, speed FROM ego WHERE ts BETWEEN {pre} AND {post}"
    sql_obj = f"""SELECT d.ts, d.obj_id, d.x, d.y, d.heading + e.utm_yaw AS h_abs,
       MAX(COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[1]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[2]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[3]'),0),
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[4]'),0)) AS sp
FROM dynamic_obj d JOIN ego e ON e.ts = d.ts
WHERE d.obj_id IN ({olist}) AND d.ts BETWEEN {pre} AND {post}"""
    rows = [r for r in run(sql_ego, tok).get('rows', []) if r.get('bag_id') == bag]
    ego = {r['ts']: r for r in rows}
    orows = [r for r in run(sql_obj, tok).get('rows', []) if r.get('bag_id') == bag]
    ts0 = v['start_ts']
    eyaws = sorted(ego.items())
    init_yaw = next((e['utm_yaw'] for t, e in reversed(eyaws) if t <= ts0), None)
    stops = [t for t, e in eyaws if t <= ts0 and (e['speed'] or 0) < 1]
    last_stop = max(stops) if stops else None
    move_start = None
    if last_stop is not None:
        ms = [t for t, e in eyaws if t > last_stop and (e['speed'] or 0) > 1.5]
        move_start = min(ms) if ms else None
    wait_span = (last_stop, move_start)
    print(f"===== {bag} init_yaw={round(init_yaw,3) if init_yaw else None} last_stop={last_stop} move_start={move_start} =====")
    rec = {'bag': bag, 'init_yaw': init_yaw, 'wait': wait_span, 'objs': {}}
    for oid in objs:
        of = sorted([r for r in orows if r['obj_id'] == oid], key=lambda r: r['ts'])
        if not of: print(f"  {oid}: NO DATA"); continue
        hrel = [round(math.degrees(dlim(r['h_abs'], init_yaw)), 1) for r in of]
        brg = [round(math.degrees(math.atan2(r['y'], r['x'])), 1) for r in of]
        dists = [round(math.hypot(r['x'], r['y']), 1) for r in of]
        imin = dists.index(min(dists))
        print(f"  obj {oid}: n={len(of)}")
        print(f"    hrel_abs: {hrel}")
        print(f"    bearing : {brg}")
        print(f"    dist    : {dists}")
        print(f"    xy_at_min: ({round(of[imin]['x'],1)},{round(of[imin]['y'],1)}) ts={of[imin]['ts']}")
        print(f"    dr_speed: {[round(r['sp'] or 0,1) for r in of]}")
        print(f"    ts      : {[r['ts'] for r in of]}")
        rec['objs'][oid] = {'hrel': hrel, 'brg': brg, 'dists': dists,
                            'xy_at_min': [round(of[imin]['x'],1), round(of[imin]['y'],1)],
                            'ts': [r['ts'] for r in of], 'sp': [round(r['sp'] or 0,1) for r in of]}
    # 等灯期（last_stop 前 15s ~ move_start）其他对向直行车流量
    if last_stop is not None:
        ws, we = max(pre, last_stop - 15), (move_start or ts0)
        sql_w = f"""SELECT d.obj_id, d.ts, d.x, d.y, d.heading + e.utm_yaw AS h_abs,
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0) AS sp
FROM dynamic_obj d JOIN ego e ON e.ts = d.ts
WHERE d.ts BETWEEN {ws} AND {we} AND d.type IN ('car','bus','truck')
  AND (d.x*d.x + d.y*d.y) <= 1600"""
        wrows = [r for r in run(sql_w, tok).get('rows', []) if r.get('bag_id') == bag]
        onc = {}
        for r in wrows:
            if abs(dlim(r['h_abs'], init_yaw)) > 2.44 and (r['sp'] or 0) > 5:
                onc.setdefault(r['obj_id'], []).append(r['ts'])
        onc = {k: vv for k, vv in onc.items() if len(vv) >= 2}
        print(f"    wait-period oncoming movers: {len(onc)} -> { {k: (min(vv), max(vv)) for k, vv in list(onc.items())[:8]} }")
        rec['wait_oncoming'] = len(onc)
    report[bag] = rec
json.dump(report, open('fail_diag3.json', 'w'), ensure_ascii=False, indent=1)
