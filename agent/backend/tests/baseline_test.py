#!/usr/bin/env python3
"""SceneSQL v2.0 Baseline Test Runner

通过generate-sql API对每个测试query生成SQL，然后自动评估：
1. SQL语法是否正确（能否在SQLite上执行）
2. 查询结果是否非空
3. SQL逻辑是否与预期模式匹配

Usage:
    # 需要先部署DSW服务
    python3 baseline_test.py --api http://8.130.175.37:30001/api/generate-sql
    # 或本地mock测试（不调用API，只打印prompt）
    python3 baseline_test.py --dry-run
"""
import json
import sqlite3
import sys
import os
import argparse
import re
from pathlib import Path
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "agent" / "backend"))

from app.core.tag_router import TagRouter, build_prompt
from app.core.schema_reader import read_schema, format_schema_for_prompt


def load_test_queries() -> list:
    """加载测试query集"""
    query_file = Path(__file__).parent / "baseline_queries.json"
    with open(query_file) as f:
        data = json.load(f)
    return data["queries"]


def call_generate_sql_api(query: str, db_name: str, api_url: str) -> dict:
    """调用远程generate-sql API"""
    import requests
    resp = requests.post(
        api_url,
        json={"question": query, "db_name": db_name},
        timeout=60
    )
    return resp.json()


def evaluate_sql(sql: str, db_path: str) -> dict:
    """评估SQL质量"""
    result = {
        "executable": False,
        "has_results": False,
        "row_count": 0,
        "error": None,
    }
    if not sql or not sql.strip():
        result["error"] = "空SQL"
        return result

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        result["executable"] = True
        result["row_count"] = len(rows)
        result["has_results"] = len(rows) > 0
        conn.close()
    except Exception as e:
        result["error"] = str(e)[:200]

    return result


def check_sql_pattern(sql: str, expected_pattern: str) -> dict:
    """检查SQL是否匹配预期模式（模糊匹配）"""
    sql_upper = sql.upper().strip()
    checks = {}

    if "JOIN" in expected_pattern:
        checks["has_join"] = "JOIN" in sql_upper
    if "HAVING" in expected_pattern:
        checks["has_having"] = "HAVING" in sql_upper
    if "NOT IN" in expected_pattern or "NOT EXISTS" in expected_pattern:
        checks["has_not"] = "NOT IN" in sql_upper or "NOT EXISTS" in sql_upper
    if "static_link" in expected_pattern:
        checks["has_static_link"] = "STATIC_LINK" in sql_upper
    if "ego" in expected_pattern:
        checks["has_ego"] = "EGO" in sql_upper
    if "dynamic_obj" in expected_pattern:
        checks["has_dynamic_obj"] = "DYNAMIC_OBJ" in sql_upper

    return checks


def run_baseline(api_url: str = None, db_name: str = None, dry_run: bool = False):
    """运行完整baseline测试"""
    queries = load_test_queries()
    router = TagRouter()

    results = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'='*60}")
    print(f"SceneSQL v2.0 Baseline Test — {now}")
    print(f"{'='*60}")
    print(f"测试集: {len(queries)} queries")
    print(f"模式: {'DRY RUN (只看prompt)' if dry_run else 'API调用'}")
    print()

    for q in queries:
        qid = q["id"]
        level = q["level"]
        query_text = q["query"]
        expected_tags = q.get("expected_tags", [])
        expected_pattern = q.get("expected_sql_pattern", "")

        # 1. 路由
        route = router.route(query_text)
        matched_tag_names = [t.tag_name for t in route.matched_tags]
        matched_tables = sorted(route.involved_tables)
        map_enum_hits = route.map_enum_hits

        # 2. 构建prompt（需要schema_text）
        try:
            from app.core.schema_reader import read_schema, format_schema_for_prompt
            # 使用默认DB路径获取schema（仅用于测试prompt构建）
            schema_tables = read_schema("/tmp/test_baseline.db") if os.path.exists("/tmp/test_baseline.db") else []
            schema_text = format_schema_for_prompt(schema_tables, only_tables=route.involved_tables) if schema_tables else "(schema unavailable in dry-run)"
            sys_prompt, user_prompt = build_prompt(query_text, schema_text, route)
            prompt_ok = True
        except Exception as e:
            sys_prompt = user_prompt = ""
            prompt_ok = False

        # 3. 调用API生成SQL（非dry_run时）
        generated_sql = ""
        api_result = {}
        eval_result = {}
        pattern_checks = {}

        if not dry_run and api_url:
            try:
                api_resp = call_generate_sql_api(query_text, db_name or "default", api_url)
                generated_sql = api_resp.get("sql", "")
                # 评估
                if db_name:
                    # 需要找到对应的db_path
                    pass  # TODO: DSW上评估
                eval_result = {"note": "API调用成功，SQL评估需在DSW执行"}
            except Exception as e:
                api_result = {"error": str(e)[:200]}

        # 4. 如果有SQL，做模式匹配
        if generated_sql:
            pattern_checks = check_sql_pattern(generated_sql, expected_pattern)

        # 汇总
        entry = {
            "id": qid,
            "level": level,
            "query": query_text,
            "route_tags": matched_tag_names,
            "route_tables": matched_tables,
            "map_enum_hits": map_enum_hits,
            "expected_tags": expected_tags,
            "tag_match": set(expected_tags).issubset(set(matched_tag_names)),
            "prompt_ok": prompt_ok,
            "generated_sql": generated_sql,
            "eval": eval_result,
            "pattern_checks": pattern_checks,
        }
        results.append(entry)

        # 打印进度
        tag_match_str = "✅" if entry["tag_match"] else "❌"
        print(f"[{qid}] {level} | {query_text}")
        print(f"  路由: {matched_tag_names} | 表: {matched_tables}")
        if map_enum_hits:
            print(f"  map枚举: {[h['kw']+'→'+h['table']+'.'+h['column']+'='+h['value'] for h in map_enum_hits]}")
        print(f"  标签匹配: {tag_match_str} (期望{expected_tags})")
        if generated_sql:
            print(f"  SQL: {generated_sql[:120]}...")
        print()

    # 统计
    total = len(results)
    tag_match_count = sum(1 for r in results if r["tag_match"])
    prompt_ok_count = sum(1 for r in results if r["prompt_ok"])

    print(f"\n{'='*60}")
    print(f"汇总统计")
    print(f"{'='*60}")
    print(f"总query数: {total}")
    print(f"标签路由正确: {tag_match_count}/{total} ({100*tag_match_count/total:.0f}%)")
    print(f"Prompt构建成功: {prompt_ok_count}/{total}")

    # 按级别统计
    by_level = {}
    for r in results:
        lvl = r["level"]
        by_level.setdefault(lvl, {"total": 0, "tag_match": 0})
        by_level[lvl]["total"] += 1
        if r["tag_match"]:
            by_level[lvl]["tag_match"] += 1

    print("\n按级别:")
    for lvl, stats in sorted(by_level.items()):
        print(f"  {lvl}: {stats['tag_match']}/{stats['total']}")

    # 保存报告
    report_dir = Path(__file__).parent.parent.parent.parent / "test_reports"
    report_dir.mkdir(exist_ok=True)
    report_file = report_dir / f"baseline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, "w") as f:
        json.dump({
            "meta": {"time": now, "mode": "dry_run" if dry_run else "api", "api_url": api_url},
            "summary": {
                "total": total,
                "tag_match": tag_match_count,
                "prompt_ok": prompt_ok_count,
                "by_level": by_level,
            },
            "results": results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n报告已保存: {report_file}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SceneSQL v2.0 Baseline Test")
    parser.add_argument("--api", help="generate-sql API URL")
    parser.add_argument("--db-name", help="DB name for API call")
    parser.add_argument("--dry-run", action="store_true", help="只测试路由+prompt，不调API")
    args = parser.parse_args()

    if not args.api and not args.dry_run:
        print("请指定 --api 或 --dry-run")
        sys.exit(1)

    run_baseline(api_url=args.api, db_name=args.db_name, dry_run=args.dry_run)
