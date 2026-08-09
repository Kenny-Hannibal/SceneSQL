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
    t0, t1 = v['start_ts'], v['end_ts']
    pre, post = t0 - 30, t1 + 10
    erows = sorted([r for r in run(f"SELECT ts, utm_yaw, speed FROM ego WHERE ts BETWEEN {pre} AND {post}", tok).get('rows', []) if r.get('bag_id') == bag], key=lambda r: r['ts'])
    init_yaw = next((r['utm_yaw'] for r in reversed(erows) if r['ts'] <= t0), None)
    if init_yaw is None:
        report[bag] = {'err': 'no ego'}; continue
    stops = [r['ts'] for r in erows if r['ts'] <= t0 and (r['speed'] or 0) < 1]
    last_stop = max(stops) if stops else None
    move_start = None
    if last_stop is not None:
        ms = [r['ts'] for r in erows if r['ts'] > last_stop and (r['speed'] or 0) > 1.5]
        move_start = min(ms) if ms else None
    if move_start is None:
        report[bag] = {'verdict': c['verdict'], 'note': 'no stop'}; continue
    # all oncoming movers within 40m over [move_start-10, move_start+12]
    wrows = [r for r in run(f"""SELECT d.obj_id, d.ts, d.heading + e.utm_yaw AS h_abs,
       SQRT(d.x*d.x+d.y*d.y) AS dist,
       COALESCE(json_extract(d.obs_dr_trajectory,'$.speed[0]'),0) AS sp
FROM dynamic_obj d JOIN ego e ON e.ts = d.ts
WHERE d.ts BETWEEN {move_start-10} AND {move_start+12} AND d.type IN ('car','bus','truck')
  AND (d.x*d.x+d.y*d.y) <= 1600""", tok).get('rows', []) if r.get('bag_id') == bag]
    onc = {}
    for r in wrows:
        if abs(dlim(r['h_abs'], init_yaw)) > 2.44 and (r['sp'] or 0) > 5 and r['dist'] < 40:
            onc.setdefault(r['obj_id'], []).append((r['ts'], round(r['dist'], 1)))
    # features
    at_move = sum(1 for vv in onc.values() if any(abs(t - move_start) <= 3 for t, _ in vv))
    near_at_move = sum(1 for vv in onc.values() if any(abs(t - move_start) <= 3 and d < 30 for t, d in vv))
    after = sum(1 for vv in onc.values() if any(move_start <= t <= move_start + 10 for t, _ in vv))
    before_ts = [t for vv in onc.values() for t, _ in vv if t < move_start]
    gap_before = (move_start - max(before_ts)) if before_ts else None
    report[bag] = {'verdict': c['verdict'], 'move_start': move_start,
                   'onc_total': len(onc), 'at_move_pm3s': at_move, 'near30_at_move_pm3s': near_at_move,
                   'after_move_10s': after, 'gap_before_s': gap_before}
json.dump(report, open('eval27_gap.json', 'w'), ensure_ascii=False, indent=1)
for b, r in report.items():
    if 'err' in r or 'note' in r:
        print(f"{b[:16]:16} {r.get('verdict','?'):5} SKIP {r.get('err') or r.get('note')}")
        continue
    print(f"{b[:16]:16} {r['verdict']:5} onc={r['onc_total']:2} atMove={r['at_move_pm3s']:2} near30={r['near30_at_move_pm3s']:2} after={r['after_move_10s']:2} gapBef={r['gap_before_s']}")
