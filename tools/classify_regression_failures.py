#!/usr/bin/env python3
"""回归失败归因分类器 — 自修改流第②层。

读取 eval_nl2sql_regression.py 的报告 JSON，把每条失败 case 归因到具体类别，
输出修复方向建议。让人（或下一层自动化）不用逐条人肉看失败原因。

用法：
    .venv/bin/python tools/classify_regression_failures.py /tmp/regression_2026-08-25.json
    .venv/bin/python tools/classify_regression_failures.py /tmp/regression.json --json

分类体系（与修复方向一一对应）：
    route_masked        路径被兜底掩盖（route_method 不在期望）→ 查日志找崩溃点/降级原因
    sql_validation      SQL 校验失败（语法/缺列）→ recipe 模板 bug 或 prompt 规则不足
    semantic_deviation  路径正确但缺语义特征 → prompt 规则/recipe 参考 SQL 未覆盖该约束
    forbidden_feature   出现禁止特征（如不应有的高速过滤）→ prompt 否定语义处理不足
    infra               请求失败（网络/服务）→ 环境问题，非代码问题
"""
import argparse
import json
import sys
from collections import Counter

CATEGORY_FIX_HINT = {
    "route_masked": "查后端日志该请求的降级/崩溃原因（'降级到关键词路径'/Traceback）",
    "sql_validation": "检查 recipe 模板或 Round 2 prompt 规则；dry-run 报错即线索",
    "semantic_deviation": "缺约束语义：补 prompt 规则 / 在 recipe 参考 SQL 加注释提示 / 补 schema",
    "forbidden_feature": "否定语义未被理解：检查否定护栏覆盖词 / Round 2 prompt 复用规则",
    "infra": "检查 DSW 后端/DeepSeek API/embedding 隧道可用性",
}


def classify(result: dict) -> str:
    failures = result.get("failures", [])
    text = " | ".join(failures)
    if "请求失败" in text:
        return "infra"
    if "route_method" in text:
        return "route_masked"
    if "validation_error" in text:
        return "sql_validation"
    if "禁止特征" in text:
        return "forbidden_feature"
    if "缺少特征" in text:
        return "semantic_deviation"
    return "semantic_deviation"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report", help="eval_nl2sql_regression.py 生成的报告 JSON")
    ap.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = ap.parse_args()

    with open(args.report) as f:
        data = json.load(f)

    results = data.get("results", [])
    failures = [r for r in results if not r.get("pass") and not r.get("note")]
    known = [r for r in results if r.get("note")]

    classified = []
    for r in failures:
        classified.append({
            "id": r["id"],
            "question": r["question"],
            "category": classify(r),
            "failures": r.get("failures", []),
            "route_method": r.get("route_method", ""),
            "fix_hint": CATEGORY_FIX_HINT[classify(r)],
        })

    counts = Counter(c["category"] for c in classified)

    if args.json:
        print(json.dumps({"total_failures": len(classified), "known_limitations": len(known),
                          "by_category": dict(counts), "cases": classified},
                         ensure_ascii=False, indent=2))
        return

    print(f"失败 {len(classified)} 条 | 已知限制 {len(known)} 条\n")
    if not classified:
        print("全部通过，无需归因 🎉")
        return
    for cat, cnt in counts.most_common():
        print(f"■ {cat} × {cnt}  → {CATEGORY_FIX_HINT[cat]}")
        for c in classified:
            if c["category"] == cat:
                print(f"   - {c['id']} {c['question']}")
                for fail in c["failures"][:2]:
                    print(f"       └─ {fail[:120]}")
        print()


if __name__ == "__main__":
    main()
