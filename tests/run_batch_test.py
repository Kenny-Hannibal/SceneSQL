#!/usr/bin/env python3
"""批量测试两轮引擎 — 20个场景"""
import requests, json, time, sys

API = "http://localhost:30001/api/agent/generate-sql"
DB = "/mnt/gacrnd-oss/gac_huangzijian/common_data/sqlite_dbs/20260601_ay05_2146_10w"

queries = [
    "找出变道的片段",
    "找出急刹车的片段",
    "找出路口直行的片段",
    "找出闯红灯的片段",
    "找出跟车太近的片段",
    "找出路口且为绿灯的场景",
    "找出路口且有红绿灯的场景",
    "找出同时有切入和急刹的片段",
    "路口左转时自车速度",
    "闯红灯时自车速度",
    "变道时前方有什么目标",
    "切入时旁车的位置和速度",
    "路口左转时对向来车的位置",
    "变道时自车速度和前方目标",
    "黄灯过线时自车速度",
    "路口右转时距离最近的行人",
    "找出无保护左转轨迹交叉场景",
    "在第几车道进行掉头",
    "避障时借道避让的车道信息",
    "路口左转时自车和障碍物的DR轨迹漂移值",
]

results = []
for i, q in enumerate(queries, 1):
    t0 = time.time()
    try:
        r = requests.post(API, json={"question": q, "db_path": DB}, timeout=180)
        d = r.json()
        sql = d.get("sql", "")
        err = d.get("validation_error") or d.get("error")
        dur = time.time() - t0
        results.append({"i": i, "q": q, "sql": sql[:300], "error": err, "dur": round(dur,1)})
        status = "OK" if sql and not err else "FAIL"
        print(f"[{i:2d}] {status} {dur:5.1f}s | {q}")
        if err: print(f"     err: {err}")
        print(f"     sql: {sql[:200]}")
    except Exception as e:
        dur = time.time() - t0
        results.append({"i": i, "q": q, "sql": "", "error": str(e), "dur": round(dur,1)})
        print(f"[{i:2d}] FAIL {dur:5.1f}s | {q} | {e}")
    sys.stdout.flush()
    time.sleep(1)

ok = sum(1 for r in results if r["sql"] and not r["error"])
print(f"\n=== 结果: {ok}/20 通过 ===")

with open("/tmp/two_round_results.json", "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
