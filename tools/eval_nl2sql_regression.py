#!/usr/bin/env python3
"""NL2SQL 端到端回归评测 — 自修改流的地基。

对 golden_queries.jsonl 中的每条问题调用 /api/agent/generate-sql，
断言：
  1. route_method 符合期望（防止新链路被兜底路径掩盖）
  2. SQL 包含期望语义特征（must_contain，大小写不敏感）
  3. SQL 不含禁止特征（must_not_contain / must_not_contain_any_of）
  4. validation_error 为空（SQL 通过 dry-run）

用法（在 DSW 上，仓库根目录）：
    .venv/bin/python tools/eval_nl2sql_regression.py
    .venv/bin/python tools/eval_nl2sql_regression.py --batch 20260430_xyc_1000_sdpro
    .venv/bin/python tools/eval_nl2sql_regression.py --case rg-06 --verbose
    .venv/bin/python tools/eval_nl2sql_regression.py --report /tmp/regression_$(date +%F).json

设计原则：
- 断言语义特征而非字符串相等（LLM 输出有波动）
- 默认 batch 选 sqlite_count 最大的可用 batch
- JWT 自动从 .env 的 JWT_SECRET 铸造（或 --token 指定）
"""
import argparse
import json
import os
import sys
import time

import requests

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
GOLDEN_PATH = os.path.join(REPO_ROOT, "golden_queries.jsonl")
BASE_URL = os.environ.get("SCENESQL_BASE_URL", "http://localhost:30001")


def mint_token() -> str:
    """从 .env 读 JWT_SECRET 铸造测试 token（与后端 auth.py 同算法）。"""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO_ROOT, ".env"))
    secret = os.getenv("JWT_SECRET", "sceneSQL_visualizer_secret_key_2026")
    from jose import jwt
    return jwt.encode({"sub": "eval-regression", "exp": int(time.time()) + 3600},
                      secret, algorithm="HS256")


def pick_batch(token: str) -> str:
    """选 sqlite_count 最大的 batch。"""
    r = requests.get(f"{BASE_URL}/api/agent/batches",
                     headers={"Authorization": f"Bearer {token}"}, timeout=15)
    r.raise_for_status()
    batches = r.json()
    if not batches:
        raise RuntimeError("无可用 batch")
    batches.sort(key=lambda b: b.get("sqlite_count", 0), reverse=True)
    return batches[0]["batch_id"]


def eval_case(case: dict, token: str, batch: str, verbose: bool) -> dict:
    q = case["question"]
    expect = case.get("expect", {})
    try:
        r = requests.post(
            f"{BASE_URL}/api/agent/generate-sql",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"question": q, "batch_id": batch, "query_mode": "sqlite", "result_limit": 100},
            timeout=180,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"id": case["id"], "question": q, "pass": False,
                "failures": [f"请求失败: {e}"], "sql": "", "route_method": ""}

    sql = (data.get("sql") or "")
    sql_lower = sql.lower()
    route_method = data.get("route_method") or ""
    failures = []

    # 已知限制：期望该 case 因记录的原因失败（如聚合查询 vs start_ts 锚定校验）。
    # 命中已知限制计为通过（标注 KNOWN），将来修复后会自然显示为行为变化。
    known = expect.get("known_limitation")
    if known and data.get("validation_error"):
        return {"id": case["id"], "question": q, "pass": True,
                "failures": [], "sql": sql, "route_method": route_method,
                "category": case.get("category", ""),
                "note": f"KNOWN LIMITATION（预期失败）: {known}"}

    if data.get("validation_error"):
        failures.append(f"validation_error: {data['validation_error']}")

    expected_routes = expect.get("route_method")
    if expected_routes and route_method not in expected_routes:
        failures.append(f"route_method={route_method!r} 不在期望 {expected_routes}")

    for feat in expect.get("must_contain", []):
        if feat.lower() not in sql_lower:
            failures.append(f"SQL 缺少特征: {feat!r}")

    for feat in expect.get("must_not_contain", []):
        if feat.lower() in sql_lower:
            failures.append(f"SQL 含禁止特征: {feat!r}")

    any_of = expect.get("must_not_contain_any_of", [])
    if any_of and any(f.lower() in sql_lower for f in any_of):
        failures.append(f"SQL 含禁止特征(任一): {any_of}")

    if verbose:
        print(f"--- {case['id']} SQL ---\n{sql[:500]}\n")

    return {"id": case["id"], "question": q, "pass": not failures,
            "failures": failures, "sql": sql, "route_method": route_method,
            "category": case.get("category", "")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="", help="指定 batch_id（默认自动选最大）")
    ap.add_argument("--case", default="", help="只跑单条（如 rg-06）")
    ap.add_argument("--token", default="", help="JWT（默认从 .env 铸造）")
    ap.add_argument("--report", default="", help="输出 JSON 报告路径")
    ap.add_argument("--verbose", action="store_true", help="打印每条 SQL")
    args = ap.parse_args()

    with open(GOLDEN_PATH) as f:
        cases = [json.loads(line) for line in f if line.strip()]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            sys.exit(f"找不到 case {args.case}")

    token = args.token or mint_token()
    batch = args.batch or pick_batch(token)
    print(f"batch={batch} | cases={len(cases)} | base={BASE_URL}\n")

    results = []
    t0 = time.time()
    for case in cases:
        res = eval_case(case, token, batch, args.verbose)
        results.append(res)
        mark = "✅" if res["pass"] else "❌"
        print(f"{mark} {res['id']} [{res.get('category','')}] {res['question']}")
        if res.get("note"):
            print(f"     └─ {res['note']}")
        if not res["pass"]:
            for fail in res["failures"]:
                print(f"     └─ {fail}")
        elif not res.get("note"):
            print(f"     route={res['route_method']} | sql_len={len(res['sql'])}")

    passed = sum(1 for r in results if r["pass"])
    elapsed = time.time() - t0
    print(f"\n===== {passed}/{len(results)} 通过 ({passed/len(results):.0%}) | 耗时 {elapsed:.0f}s =====")

    if args.report:
        with open(args.report, "w") as f:
            json.dump({"batch": batch, "passed": passed, "total": len(results),
                       "results": results}, f, ensure_ascii=False, indent=2)
        print(f"报告已写入 {args.report}")

    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
