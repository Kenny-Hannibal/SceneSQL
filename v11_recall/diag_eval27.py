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
CASES = json.load(open('eval_v3_raw.json'))['cases']
V12 = {r['bag_id']: r for r in json.load(open('v12_full.json'))['rows']}
report = {}
for c in CASES:
    bag = c['bag_id']
    v = V12.get(bag)
    if not v:
        continue
    objs = [o.strip() for o in str(v['obj_id']).split(',')]
    t0, t1 = v['start_ts'], v['end_ts']
    pre, post = t0 - 30, t1 + 10
    olist = ','.join(f"'{o}'" for o in objs)
    rows = [r for r in run(f"SELECT ts, utm_yaw, speed FROM ego WHERE ts BETWEEN {pre} AND {post}", tok).get('rows', []) if r.get('bag_id') == bag]
    ego = {r['ts']: r for r in rows}
    orows = [r for r in run(f"""SELECT d.ts, d.obj_id, d.x, d.y, d.heading + e.utm_yaw AS h_abs,
       COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0) AS sp
FROM dynamic_obj d JOIN ego e ON e.ts = d.ts
WHERE d.obj_id IN ({olist}) AND d.ts BETWEEN {pre} AND {post}""", tok).get('rows', []) if r.get('bag_id') == bag]
    eyaws = sorted(ego.items())
    init_yaw = next((e['utm_yaw'] for t, e in reversed(eyaws) if t <= t0), None)
    if init_yaw is None:
        report[bag] = {'err': 'no ego'}; continue
    stops = [t for t, e in eyaws if t <= t0 and (e['speed'] or 0) < 1]
    last_stop = max(stops) if stops else None
    move_start = None
    if last_stop is not None:
        ms = [t for t, e in eyaws if t > last_stop and (e['speed'] or 0) > 1.5]
        move_start = min(ms) if ms else None
    rec = {'verdict': c['verdict'], 'wait': (last_stop, move_start), 'wait_oncoming': None, 'objs': {}}
    for oid in objs:
        of = sorted([r for r in orows if r['obj_id'] == oid], key=lambda r: r['ts'])
        if not of: continue
        dists = [math.hypot(r['x'], r['y']) for r in of]
        imin = dists.index(min(dists))
        i15 = next((i for i, d in enumerate(dists) if d < 15), None)
        rec['objs'][oid] = {'xy_at_min': [round(of[imin]['x'], 1), round(of[imin]['y'], 1)],
                            'brg_at_min': round(math.degrees(math.atan2(of[imin]['y'], of[imin]['x'])), 1),
                            'dmin': round(dists[imin], 1),
                            'brg_at_15': round(math.degrees(math.atan2(of[i15]['y'], of[i15]['x'])), 1) if i15 is not None else None}
    if last_stop is not None:
        ws, we = max(pre, last_stop - 15), (move_start or t0)
        wrows = [r for r in run(f"""SELECT d.obj_id, d.ts, d.heading + e.utm_yaw AS h_abs,
           COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0) AS sp
FROM dynamic_obj d JOIN ego e ON e.ts = d.ts
WHERE d.ts BETWEEN {ws} AND {we} AND d.type IN ('car','bus','truck') AND (d.x*d.x+d.y*d.y) <= 1600""", tok).get('rows', []) if r.get('bag_id') == bag]
        onc = {}
        for r in wrows:
            if abs(dlim(r['h_abs'], init_yaw)) > 2.44 and (r['sp'] or 0) > 5:
                onc.setdefault(r['obj_id'], []).append(r['ts'])
        rec['wait_oncoming'] = sum(1 for vv in onc.values() if len(vv) >= 2)
    report[bag] = rec

json.dump(report, open('eval27_diag.json', 'w'), ensure_ascii=False, indent=1)
print(f"{'bag':18} {'verdict':5} {'wait_onc':8} objs(xy_at_min, brg_at_min, brg_at_15)")
for bag, rec in report.items():
    if 'err' in rec:
        print(f"{bag:18} ERR"); continue
    os_ = '; '.join(f"{oid}:xy{d['xy_at_min']},b{d['brg_at_min']},b15={d['brg_at_15']}" for oid, d in rec['objs'].items())
    print(f"{bag:18} {rec['verdict']:5} {str(rec['wait_oncoming']):8} {os_}")
