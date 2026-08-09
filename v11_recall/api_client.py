import urllib.request, json, sys

BASE = "http://127.0.0.1:30001"
BATCH = "20260702_T68_2471_c5afa57_100w"

def login():
    data = json.dumps({"username":"gac","password":"gac_data"}).encode()
    req = urllib.request.Request(f"{BASE}/api/auth/login", data=data,
        headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())["access_token"]

def run_sql(sql, result_limit=50000, db_limit=0, timeout=1200, page_size=30000):
    token = login()
    body = json.dumps({"sql": sql, "batch_id": BATCH, "query_mode": "sqlite",
                       "db_limit": db_limit, "result_limit": result_limit, "page": 1, "page_size": page_size}).encode()
    req = urllib.request.Request(f"{BASE}/api/agent/execute-sql", data=body,
        headers={"Content-Type":"application/json", "Authorization": f"Bearer {token}"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())

if __name__ == "__main__":
    sql_file = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    res = run_sql(open(sql_file).read())
    print("total_rows:", res.get("total_rows"), "matched_dbs:", res.get("matched_dbs"), "scanned_dbs:", res.get("scanned_dbs"))
    if out:
        json.dump(res, open(out, "w"))
        print("saved:", out)
