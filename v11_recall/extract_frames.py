import json, time, urllib.request, os, sys

BASE = "http://127.0.0.1:30001"
TOPIC_NEW = "/gac/cam/orig_fw120_encoded"
TOPIC_OLD = "/gac/cam/fw120_encoded"

def token():
    data = json.dumps({"username":"gac","password":"gac_data"}).encode()
    req = urllib.request.Request(f"{BASE}/api/auth/login", data=data, headers={"Content-Type":"application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())["access_token"]

def post(path, body, tok):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type":"application/json", "Authorization": f"Bearer {tok}"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read().decode())

def get(path, tok):
    req = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {tok}"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read().decode())

def download(url, dest):
    req = urllib.request.Request(url if url.startswith("http") else BASE+url)
    open(dest, "wb").write(urllib.request.urlopen(req, timeout=60).read())

sample = json.load(open('sample30.json'))
tok = token()
clips = []
for i, s in enumerate(sample):
    dur = s['end_ts'] - s['start_ts']
    fps = max(0.4, min(2.0, 14/max(dur,1)))
    clips.append({"idx": i, "bag_id": s['bag_id'],
                  "start_ts": (s['start_ts']-3)*10**9, "end_ts": (s['end_ts']+2)*10**9,
                  "fps": round(fps,2)})
# 批次抽帧：单批 fps 不同则分批（API 是全局 sample_fps）→ 按 fps 分组
from collections import defaultdict
groups = defaultdict(list)
for c in clips: groups[c['fps']].append(c)

os.makedirs('frames', exist_ok=True)
results = {}
for fps, grp in groups.items():
    body = {"clips": [{"bag_id": c['bag_id'], "start_ts": c['start_ts'], "end_ts": c['end_ts'], "topic": TOPIC_NEW} for c in grp],
            "sample_fps": fps, "max_frames_per_clip": 18, "resolve_bag_path": True}
    resp = post("/api/video/extract-batch", body, tok)
    task_id = resp.get("task_id")
    print(f"batch fps={fps} n={len(grp)} task={task_id}", flush=True)
    for _ in range(120):
        st = get(f"/api/video/extract-batch/{task_id}", tok)
        if st.get("status") in ("completed", "failed"): break
        time.sleep(5)
    for c, cr in zip(grp, st.get("clips", [])):
        results[c['idx']] = {"bag_id": c['bag_id'], "status": cr.get('status'),
                             "message": cr.get('message',''), "frame_urls": cr.get('frame_urls', []),
                             "topic": TOPIC_NEW}
        print(f"  s{c['idx']:02d} {c['bag_id'][:12]} {cr.get('status')} frames={len(cr.get('frame_urls',[]))} msg={str(cr.get('message',''))[:80]}", flush=True)
json.dump(results, open('frame_results.json','w'), indent=1)
print("done")
