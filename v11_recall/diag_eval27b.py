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
    if not v: continue
    objs = [o.strip() for o in str(v['obj_id']).split(',')]
    t0, t1 = v['start_ts'], v['end_ts']
    pre, post = t0 - 30, t1 + 10
    olist = ','.join(f"'{o}'" for o in objs)
    erows = [r for r in run(f"SELECT ts, utm_yaw, speed FROM ego WHERE ts BETWEEN {pre} AND {post}", tok).get('rows', []) if r.get('bag_id') == bag]
    ego = sorted(erows, key=lambda r: r['ts'])
    emap = {r['ts']: r for r in ego}
    init_yaw = next((r['utm_yaw'] for r in reversed(ego) if r['ts'] <= t0), None)
    if init_yaw is None:
        report[bag] = {'err': 'no ego'}; continue
    stops = [r['ts'] for r in ego if r['ts'] <= t0 and (r['speed'] or 0) < 1]
    last_stop = max(stops) if stops else None
    move_start = None
    if last_stop is not None:
        ms = [r['ts'] for r in ego if r['ts'] > last_stop and (r['speed'] or 0) > 1.5]
        move_start = min(ms) if ms else None
    orows = [r for r in run(f"""SELECT d.ts, d.obj_id, d.x, d.y, d.heading + e.utm_yaw AS h_abs,
       COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0) AS sp,
       json_extract(d.obs_dr_trajectory,'$.x[0]') AS ox,
       json_extract(d.obs_dr_trajectory,'$.y[0]') AS oy
FROM dynamic_obj d JOIN ego e ON e.ts = d.ts
WHERE d.obj_id IN ({olist}) AND d.ts BETWEEN {pre} AND {post}""", tok).get('rows', []) if r.get('bag_id') == bag]
    epf = [r for r in run(f"""SELECT ts, json_extract(ego_dr_trajectory,'$.x[0]') AS px,
       json_extract(ego_dr_trajectory,'$.y[0]') AS py FROM ego WHERE ts BETWEEN {pre} AND {post}""", tok).get('rows', []) if r.get('bag_id') == bag]
    rec = {'verdict': c['verdict'], 'move_start': move_start, 'objs': {}}
    for oid in objs:
        of = sorted([r for r in orows if r['obj_id'] == oid], key=lambda r: r['ts'])
        if not of: continue
        dists = [math.hypot(r['x'], r['y']) for r in of]
        imin = dists.index(min(dists))
        rmin = of[imin]; tmin = rmin['ts']
        espd_at_min = emap.get(tmin, {}).get('speed')
        sps = [(r['sp'] or 0) for r in of]
        pre8 = [s for r, s in zip(of, sps) if r['ts'] < tmin and r['ts'] >= tmin - 8]
        # absolute-heading sweep over full track (circular stats)
        hs = [r['h_abs'] for r in of if r['h_abs'] is not None]
        mean_h = math.atan2(sum(map(math.sin, hs)), sum(map(math.cos, hs))) if hs else None
        devs = [abs(dlim(h, mean_h)) for h in hs]
        hdev = {'max_deg': round(math.degrees(max(devs)), 1),
                'ge15_pct': round(100 * sum(1 for d in devs if d > math.radians(15)) / len(devs), 1)} if devs else None
        # path crossing temporal sync: min dist with its ts delta
        best = None
        for o in of:
            if o['ox'] is None or o['oy'] is None: continue
            for e in epf:
                if e['px'] is None or e['py'] is None: continue
                if abs(o['ts'] - e['ts']) > 5: continue
                dd = (o['ox'] - e['px']) ** 2 + (o['oy'] - e['py']) ** 2
                if best is None or dd < best[0]:
                    best = (dd, o['ts'] - e['ts'], e['ts'])
        pd = {'pdist': round(math.sqrt(best[0]), 2), 'dts_at_cross': best[1], 'ego_ts_at_cross': best[2]} if best else None
        rec['objs'][oid] = {
            'dmin': round(dists[imin], 1),
            'xy_min': [round(rmin['x'], 1), round(rmin['y'], 1)],
            'ego_spd_at_min': round(espd_at_min, 1) if espd_at_min is not None else None,
            'min_ts': tmin,
            'min_after_move': (tmin - move_start) if move_start is not None else None,
            'sp_at_min': round(sps[imin], 1),
            'sp_min_pre8': round(min(pre8), 1) if pre8 else None,
            'sp_min_all': round(min(sps), 1),
            'stopped_any': any(s < 1 for s in sps),
            'hdev': hdev,
            'path': pd}
    report[bag] = rec

json.dump(report, open('eval27_diag4.json', 'w'), ensure_ascii=False, indent=1)
for bag, rec in report.items():
    if 'err' in rec: print(f"{bag:18} ERR"); continue
    parts = []
    for oid, d in rec['objs'].items():
        p = d['path'] or {}
        parts.append(f"{oid}: dmin{d['dmin']} egoV@min={d['ego_spd_at_min']} "
                     f"min-mv={d['min_after_move']} spMin8={d['sp_min_pre8']} stop={d['stopped_any']} "
                     f"hdev={d['hdev']['max_deg']}({d['hdev']['ge15_pct']}%) "
                     f"cross[{p.get('pdist')}m,dts={p.get('dts_at_cross')}]")
    print(f"{bag:18} {rec['verdict']:5} | " + ' ; '.join(parts))
