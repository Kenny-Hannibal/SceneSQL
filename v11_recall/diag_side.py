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
    orows = [r for r in run(f"""SELECT d.ts, d.obj_id, d.x, d.y,
       COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0) AS sp,
       json_extract(d.obs_dr_trajectory,'$.x[0]') AS ox,
       json_extract(d.obs_dr_trajectory,'$.y[0]') AS oy,
       e.utm_yaw AS yaw,
       json_extract(e.ego_dr_trajectory,'$.x[0]') AS epx,
       json_extract(e.ego_dr_trajectory,'$.y[0]') AS epy
FROM dynamic_obj d JOIN ego e ON e.ts = d.ts
WHERE d.obj_id IN ({olist}) AND d.ts BETWEEN {pre} AND {post}""", tok).get('rows', []) if r.get('bag_id') == bag]
    # calibrate world->ego rotation convention on first usable frame
    conv = None
    for r0 in orows:
        if r0['ox'] is None or r0['epx'] is None or r0['yaw'] is None: continue
        dx, dy = r0['ox'] - r0['epx'], r0['oy'] - r0['epy']
        for sign in (1, -1):
            xe = math.cos(r0['yaw']) * dx + sign * math.sin(r0['yaw']) * dy
            ye = -sign * math.sin(r0['yaw']) * dx + math.cos(r0['yaw']) * dy
            if abs(xe - r0['x']) < 1.5 and abs(ye - r0['y']) < 1.5:
                conv = sign
                break
        if conv is not None: break
    epf = [r for r in run(f"""SELECT ts, utm_yaw, json_extract(ego_dr_trajectory,'$.x[0]') AS px,
       json_extract(ego_dr_trajectory,'$.y[0]') AS py FROM ego WHERE ts BETWEEN {pre} AND {post}""", tok).get('rows', []) if r.get('bag_id') == bag]
    emap = {r['ts']: r for r in epf}
    rec = {'verdict': c['verdict'], 'objs': {}}
    for oid in objs:
        of = sorted([r for r in orows if r['obj_id'] == oid], key=lambda r: r['ts'])
        if not of: continue
        omap = {r['ts']: r for r in of}
        maxsp = max((r['sp'] or 0) for r in of)
        best = None
        for o in of:
            if o['ox'] is None or o['oy'] is None: continue
            for e in epf:
                if e['px'] is None or e['py'] is None: continue
                if abs(o['ts'] - e['ts']) > 5: continue
                dd = (o['ox'] - e['px']) ** 2 + (o['oy'] - e['py']) ** 2
                if best is None or dd < best[0]:
                    best = (dd, o['ts'], e['ts'])
        r = {'maxsp': round(maxsp, 1)}
        if best:
            _, ots, ets = best
            e = emap.get(ets)
            oo = omap.get(ots)
            if e and oo and oo['ox'] is not None and conv is not None:
                yaw = e['utm_yaw']
                dx, dy = oo['ox'] - e['px'], oo['oy'] - e['py']
                xe = math.cos(yaw) * dx + conv * math.sin(yaw) * dy
                ye = -conv * math.sin(yaw) * dx + math.cos(yaw) * dy
                r['cross_side_y'] = round(ye, 1)
                r['cross_x'] = round(xe, 1)
                r['obj_xy_at_ots'] = [round(oo['x'], 1), round(oo['y'], 1)]
                r['dts'] = ots - ets
            elif conv is None:
                r['cal_fail'] = True
        rec['objs'][oid] = r
    report[bag] = rec
json.dump(report, open('eval27_side.json', 'w'), ensure_ascii=False, indent=1)
for b, rc in report.items():
    parts = [f"{o}: maxsp={d.get('maxsp')} sideY={d.get('cross_side_y')} crossX={d.get('cross_x')} dts={d.get('dts')} objAtOts={d.get('obj_xy_at_ots')}" for o, d in rc['objs'].items()]
    print(f"{b[:16]:16} {rc['verdict']:5} | " + ' ; '.join(parts))
