#!/usr/bin/env python3
"""
从 schema_master_raw.yaml（母表）自动派生 schema_structure.yaml 和 schema_dictionary.yaml。

用法:
    cd /data/var/workspace/projects/projects/SceneSQL
    python .agents/skills/ubm-schema-sync/scripts/derive_schemas.py

设计原则:
    - 母表是唯一权威源，structure 和 dictionary 是派生视图
    - 派生是单向的：母表 → structure + dictionary
    - 每次运行会覆盖 structure 和 dictionary 文件
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
import yaml

BASE = Path(__file__).resolve().parent
CORE = BASE.parent.parent.parent.parent / "agent" / "backend" / "app" / "core"


def load_master() -> dict:
    with open(CORE / "schema_master_raw.yaml") as f:
        return yaml.safe_load(f)


def derive_structure(master: dict) -> dict:
    """从母表派生 schema_structure.yaml（给LLM看的精简版）。"""
    # 从母表的 sqlite_database.tables 提取表结构
    # structure 中每个表只需要: name, type, columns(name/type/nullable/pk), primary_key, enum, notes
    master_tables = master["sqlite_database"]["tables"]
    rt_meta = master.get("range_tag", {})
    tag_enum = rt_meta.get("tag_enum", [])
    rt_notes = rt_meta.get("notes", [])

    # 表类型映射（母表没有type字段，按表名硬编码）
    TABLE_TYPES = {
        "range_tag": "horizontal_event",
        "intersection_info": "horizontal_event",
        "ego": "vertical_timeseries",
        "dynamic_obj": "vertical_timeseries",
        "static_obj": "vertical_timeseries",
        "static_lane": "static",
        "static_link": "static",
        "dynamic_lane": "vertical_timeseries",
        "dynamic_link": "vertical_timeseries",
    }

    derived_tables = []
    for t in master_tables:
        table_name = t["name"]
        dt = {
            "name": table_name,
            "type": TABLE_TYPES.get(table_name, "unknown"),
            "columns": [],
        }

        for col in t.get("columns", []):
            dc = {
                "name": col["name"],
                "type": col["type"],
                "nullable": not col.get("notnull", True),
            }
            if col.get("pk"):
                dc["pk"] = True
            dt["columns"].append(dc)

        # 主键
        pk_cols = [c["name"] for c in t.get("columns", []) if c.get("pk")]
        if pk_cols:
            dt["primary_key"] = pk_cols

        # range_tag 特有：enum + notes
        if table_name == "range_tag" and tag_enum:
            dt["enum"] = tag_enum

        if table_name == "range_tag" and rt_notes:
            dt["notes"] = rt_notes

        if table_name == "intersection_info":
            dt["notes"] = ["存储 EgoIntoIntersection 事件，从 range_tag 中过滤后单独存放"]

        derived_tables.append(dt)

    return {
        "version": master.get("version", "2.0.0"),
        "note": (
            "纯 SQLite 结构文件，用于 LLM SQL 生成阶段注入 prompt。\n"
            "不含任何自然语言解释，只包含表名、列名、数据类型、主键、标签枚举值。\n"
            "字段/标签的详细定义请查阅 schema_dictionary.yaml。\n"
            "\n本文件由 derive_schemas.py 从 schema_master_raw.yaml 自动派生，请勿手动编辑。"
        ),
        "database_schema": {
            "tables": derived_tables,
        },
    }


def derive_dictionary(master: dict) -> dict:
    """从母表派生 schema_dictionary.yaml（语义字典）。"""
    rt_meta = master.get("range_tag", {})
    tag_semantics = rt_meta.get("tag_semantics", {})

    return {
        "version": master.get("version", "2.0.0"),
        "note": (
            "标签与字段语义字典。按 tag_name / field_name 为 key，支持 O(1) 快速查询。\n"
            "包含标签定义、子标签、局限性、来源、相关表等信息。\n"
            "本字典只包含实际会进入 SQLite 的标签和字段。\n"
            "\n本文件由 derive_schemas.py 从 schema_master_raw.yaml 自动派生，请勿手动编辑。"
        ),
        "tags": tag_semantics,
    }


def write_yaml(data: dict, path: Path):
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"[OK] 已写入: {path} ({path.stat().st_size} bytes)")


def main():
    master = load_master()
    print(f"[INFO] 母表加载完成: {len(master['sqlite_database']['tables'])} tables, "
          f"tag_enum={len(master.get('range_tag',{}).get('tag_enum',[]))}, "
          f"tag_semantics={len(master.get('range_tag',{}).get('tag_semantics',[]))}")

    structure = derive_structure(master)
    dictionary = derive_dictionary(master)

    write_yaml(structure, CORE / "schema_structure.yaml")
    write_yaml(dictionary, CORE / "schema_dictionary.yaml")

    print("[DONE] 派生完成。")


if __name__ == "__main__":
    main()
