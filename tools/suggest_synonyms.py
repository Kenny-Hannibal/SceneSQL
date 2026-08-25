#!/usr/bin/env python3
"""同义词修复建议生成器 — 自修改流第③层。

当向量召回/路由对某条口语查询失效时，调用 LLM 生成口语化同义短语，
输出建议的 vector_synonyms.json diff（只打印不写文件，人审后手动并入）。

用法（在 DSW 或配好 LLM env 的机器上）：
    .venv/bin/python tools/suggest_synonyms.py "旁边车道有车加塞插到我前面" obj_cut_in
    .venv/bin/python tools/suggest_synonyms.py "车子别我" obj_cut_in --apply   # 直接并入 json（谨慎）

LLM 配置与后端一致：OPENAI_BASE_URL / OPENAI_API_KEY / AGENT_MAIN_MODEL 环境变量。
"""
import argparse
import json
import os
import sys

REPO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SYNONYMS_PATH = os.path.join(REPO_ROOT, "agent/backend/app/core/vector_synonyms.json")

PROMPT = """你是自动驾驶场景挖掘的查询理解专家。
用户的一条自然语言查询未能正确路由到目标场景模板。

用户查询：{query}
目标场景模板（recipe）：{recipe}

请生成 5 条「口语化同义短语」——真实工程师描述该场景时可能用的不同说法
（包含方言/缩写/中英文混说/动作描述，每条 4-20 字，用空格连接成一行）。
要求：语义必须严格指向「{recipe}」，不要引入其他场景的含义。
只输出一行短语，不要解释。"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", help="未能正确路由的用户查询")
    ap.add_argument("recipe", help="目标 recipe 名（如 obj_cut_in）")
    ap.add_argument("--apply", action="store_true", help="直接把建议并入 vector_synonyms.json（默认只打印）")
    args = ap.parse_args()

    base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:30000/v1")
    api_key = os.getenv("OPENAI_API_KEY", "vllm-local")
    model = os.getenv("AGENT_MAIN_MODEL", "qwen3.5")

    try:
        from openai import OpenAI
    except ImportError:
        sys.exit("需要 openai 包（在 DSW .venv 下运行）")

    client = OpenAI(base_url=base_url, api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": PROMPT.format(query=args.query, recipe=args.recipe)}],
        temperature=0.3,
    )
    suggestion = resp.choices[0].message.content.strip().strip('"\'')

    print(f"查询: {args.query}")
    print(f"目标 recipe: {args.recipe}")
    print(f"\n建议的同义短语:\n  {suggestion}\n")
    print("建议的 vector_synonyms.json 修改:")
    print(f'  "{args.recipe}": ["<原有短语> {suggestion}"]')

    if args.apply:
        with open(SYNONYMS_PATH) as f:
            data = json.load(f)
        existing = data.get(args.recipe, [""])
        if isinstance(existing, list):
            existing = [" ".join(str(s) for s in existing)]
        merged = (existing[0] + " " + suggestion).strip() if existing else suggestion
        data[args.recipe] = [merged]
        with open(SYNONYMS_PATH, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n已并入 {SYNONYMS_PATH}")
        print("⚠️ 别忘了在 DSW 重建索引并跑回归：")
        print('  .venv/bin/python -c "import agent.backend.app.core.vector_router as vr; vr.load_from_templates(force=True)"')
        print("  .venv/bin/python tools/eval_vector_recall.py")


if __name__ == "__main__":
    main()
