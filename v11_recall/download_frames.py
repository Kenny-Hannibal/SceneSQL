import json, urllib.request, os

BASE = "http://127.0.0.1:30001"
OUT = "/data/var/workspace/projects/projects/docs/gac/sql_validation/unprotected_left_turn_visualation_val_v11"

data = json.dumps({"username":"gac","password":"gac_data"}).encode()
req = urllib.request.Request(f"{BASE}/api/auth/login", data=data, headers={"Content-Type":"application/json"})
tok = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())["access_token"]

results = json.load(open('frame_results.json'))
fail = 0
for k in sorted(results, key=int):
    v = results[k]
    urls = v.get('frame_urls', [])
    if not urls:
        print(f"s{k}: NO FRAMES"); fail += 1; continue
    d = os.path.join(OUT, f"sample_{int(k):02d}")
    os.makedirs(d, exist_ok=True)
    n_ok = 0
    for j, u in enumerate(urls):
        dest = os.path.join(d, f"f{j:02d}.jpg")
        if os.path.exists(dest) and os.path.getsize(dest) > 1000: n_ok += 1; continue
        try:
            url = u if u.startswith("http") else BASE + u
            r = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
            open(dest, "wb").write(urllib.request.urlopen(r, timeout=60).read())
            n_ok += 1
        except Exception as ex:
            print(f"s{k} f{j} FAIL {ex}"); fail += 1
    print(f"s{k}: {n_ok}/{len(urls)} -> sample_{int(k):02d}")
print("done, fails:", fail)
