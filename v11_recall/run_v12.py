import json, sys, urllib.request
from api_client import BASE, BATCH, login

SQL = open('/data/var/workspace/projects/projects/SceneSQL/unprotected_left_turn_v12.sql').read()
OUT = 'v12_full.json'

def run_page(token, page, page_size=30000):
    body = json.dumps({"sql": SQL, "batch_id": BATCH, "query_mode": "sqlite",
                       "db_limit": 0, "result_limit": 50000,
                       "page": page, "page_size": page_size}).encode()
    req = urllib.request.Request(f"{BASE}/api/agent/execute-sql", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req, timeout=1200).read().decode())

token = login()
res = run_page(token, 1)
if res.get('error'):
    print('ERROR:', res['error']); sys.exit(1)
total = res.get('total_rows', 0)
rows = list(res.get('rows', []))
print('page1:', len(rows), 'total_rows:', total,
      'matched_dbs:', res.get('matched_dbs'), 'scanned_dbs:', res.get('scanned_dbs'))
page = 2
while len(rows) < total and page <= 10:
    r = run_page(token, page)
    new = r.get('rows', [])
    if not new:
        break
    rows.extend(new)
    print(f'page{page}: +{len(new)}')
    page += 1
res['rows'] = rows
json.dump(res, open(OUT, 'w'))
print('saved', OUT, 'rows:', len(rows))
