import json, urllib.request, time
BASE = "http://127.0.0.1:30002"
BATCH = "20260702_T68_2471_c5afa57_100w"

def login():
    d = json.dumps({"username": "gac", "password": "gac_data"}).encode()
    q = urllib.request.Request(BASE + "/api/auth/login", data=d, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(q, timeout=30).read().decode())["access_token"]

sql = open('/data/var/workspace/projects/projects/SceneSQL/unprotected_left_turn_v13.sql').read()
tok = login()
t0 = time.time()
body = json.dumps({"sql": sql, "batch_id": BATCH, "query_mode": "sqlite",
                   "db_limit": 0, "result_limit": 50000, "page": 1, "page_size": 50000}).encode()
req = urllib.request.Request(BASE + "/api/agent/execute-sql", data=body,
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
resp = json.loads(urllib.request.urlopen(req, timeout=14400).read().decode())
rows = resp.get('rows', [])
json.dump({'rows': rows, 'meta': {k: resp.get(k) for k in resp if k != 'rows'}},
          open('v13_full.json', 'w'), ensure_ascii=False, indent=1)
print(f"rows={len(rows)} elapsed={time.time()-t0:.0f}s scanned={resp.get('scanned_dbs')}")
for r in rows[:5]:
    print(r.get('bag_id'), r.get('start_ts'), r.get('obj_id'))
