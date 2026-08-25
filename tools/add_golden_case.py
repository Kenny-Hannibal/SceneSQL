#!/usr/bin/env python3
"""Golden 用例转正工具 — 自修改流第⑤层。

把线上发现的 badcase 一键加入 golden_queries.jsonl 成为回归用例。

用法：
    # 最简：只断言 SQL 含某特征
    .venv/bin/python tools/add_golden_case.py --id rg-26 --question "查询高速下匝道场景" \
        --must-contain static_link

    # 完整：指定分类与路径断言
    .venv/bin/python tools/add_golden_case.py --id rg-27 --category negation \
        --question "查询变道但不要高速" \
        --route llm recipe_guided --must-contain tag_name \
        --must-not-contain "sl.link_class = '高速公路'" --comment "线上badcase转正"

幂等：同 id 重复添加会报错退出（防止覆盖既有用例）。
"""
import argparse
import json
import os
import sys

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
GOLDEN_PATH = os.path.join(REPO_ROOT, "golden_queries.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True, help="用例 id（如 rg-26），必须唯一")
    ap.add_argument("--question", required=True, help="自然语言查询")
    ap.add_argument("--category", default="user_badcase", help="分类（默认 user_badcase）")
    ap.add_argument("--route", nargs="*", default=[], help="期望 route_method（可多值）")
    ap.add_argument("--must-contain", nargs="*", default=[], help="SQL 必须包含的特征（可多值）")
    ap.add_argument("--must-not-contain", nargs="*", default=[], help="SQL 禁止包含的特征（可多值）")
    ap.add_argument("--comment", default="", help="备注")
    args = ap.parse_args()

    existing_ids = set()
    if os.path.exists(GOLDEN_PATH):
        with open(GOLDEN_PATH) as f:
            for line in f:
                if line.strip():
                    existing_ids.add(json.loads(line)["id"])
    if args.id in existing_ids:
        sys.exit(f"错误：id {args.id} 已存在，换一个（防止覆盖既有用例）")

    expect = {}
    if args.route:
        expect["route_method"] = args.route
    if args.must_contain:
        expect["must_contain"] = args.must_contain
    if args.must_not_contain:
        expect["must_not_contain"] = args.must_not_contain
    if args.comment:
        expect["comment"] = args.comment
    if not (args.route or args.must_contain or args.must_not_contain):
        sys.exit("错误：至少给一个断言（--route / --must-contain / --must-not-contain）")

    case = {"id": args.id, "category": args.category, "question": args.question, "expect": expect}
    with open(GOLDEN_PATH, "a") as f:
        f.write(json.dumps(case, ensure_ascii=False) + "\n")

    print(f"✅ 已转正 {args.id} → {GOLDEN_PATH}")
    print(f"   问题: {args.question}")
    print(f"   断言: {json.dumps(expect, ensure_ascii=False)}")
    print("\n记得跑回归确认新用例通过：")
    print("  .venv/bin/python tools/eval_nl2sql_regression.py --case " + args.id + " --verbose")


if __name__ == "__main__":
    main()
