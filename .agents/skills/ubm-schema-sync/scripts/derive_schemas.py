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
    - v2.0: 母表按表分组织，enum_columns挂到具体表的具体列上
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
    """从母表派生 schema_structure.yaml（给LLM看的精简版）。

    v2.0: 从 master["tables"] 读取表定义，每个表的 enum_columns
    直接作为 enum 字段写入对应表的对应列中。
    """
    master_tables = master.get("tables", {})

    # 表类型映射
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
    for table_name, table_info in master_tables.items():
        dt = {
            "name": table_name,
            "type": TABLE_TYPES.get(table_name, "unknown"),
            "description": table_info.get("description", ""),
            "columns": [],
        }

        # 列定义
        for col in table_info.get("columns", []):
            dc = {
                "name": col["name"],
                "type": col["type"],
                "nullable": not col.get("notnull", True),
            }
            if col.get("pk"):
                dc["pk"] = True

            # 如果此列在 enum_columns 中，附加 enum 值
            enum_cols = table_info.get("enum_columns", {})
            col_name = col["name"]
            if col_name in enum_cols:
                ec = enum_cols[col_name]
                # 只写 values 列表，不写 source_map（那是母表才需要的）
                dc["enum"] = ec.get("values", [])
                if "value_descriptions" in ec:
                    dc["enum_descriptions"] = ec["value_descriptions"]

            dt["columns"].append(dc)

        # 主键
        pk_cols = [c["name"] for c in table_info.get("columns", []) if c.get("pk")]
        if pk_cols:
            dt["primary_key"] = pk_cols

        # range_tag 特有 notes
        if table_name == "range_tag":
            dt["notes"] = [
                "range_tag是时间区间标签表，tag_name列的枚举值是所有合法标签",
                "标签来源: 车端行为标签(refDictEn) + 云端算子(op_*.py + feature_op)",
            ]

        if table_name == "intersection_info":
            dt["notes"] = ["存储 EgoIntoIntersection 事件，从 range_tag 中过滤后单独存放"]

        if table_name == "ego":
            dt["notes"] = ["ego表无tag_name列，其分类列(traffic_light_status等)由车端传感器直接上报"]

        derived_tables.append(dt)

    return {
        "version": master.get("version", "2.0.0"),
        "note": (
            "纯 SQLite 结构文件，用于 LLM SQL 生成阶段注入 prompt。\n"
            "不含任何自然语言解释，只包含表名、列名、数据类型、主键、枚举值。\n"
            "字段/标签的详细定义请查阅 schema_dictionary.yaml。\n"
            "\n本文件由 derive_schemas.py 从 schema_master_raw.yaml 自动派生，请勿手动编辑。"
        ),
        "database_schema": {
            "tables": derived_tables,
        },
    }


def derive_dictionary(master: dict) -> dict:
    """从母表派生 schema_dictionary.yaml（语义字典）。

    v2.0: 从 master["tables"] 中提取每个表的每个enum列的语义信息，
    并保留 master["tag_semantics"] 作为兼容层。
    """
    master_tables = master.get("tables", {})

    # 构建 tags 字典：key = tag_name, value = semantic info
    tags = {}

    # 1. range_tag 的 tag_name → 从 tag_semantics 兼容层获取
    old_tag_semantics = master.get("tag_semantics", [])
    if isinstance(old_tag_semantics, list):
        for ts in old_tag_semantics:
            if isinstance(ts, dict) and "name" in ts:
                tags[ts["name"]] = ts
            elif isinstance(ts, str):
                tags[ts] = {"name": ts, "description": "TODO"}
    elif isinstance(old_tag_semantics, dict):
        tags = dict(old_tag_semantics)

    # 2. 从新结构 tables.range_tag.enum_columns.tag_name.source_map 补充来源信息
    rt_table = master_tables.get("range_tag", {})
    rt_enum_cols = rt_table.get("enum_columns", {})
    tag_name_info = rt_enum_cols.get("tag_name", {})
    source_map = tag_name_info.get("source_map", {})

    for tag_name in tag_name_info.get("values", []):
        if tag_name not in tags:
            # 新标签，没有旧的语义信息，创建基本条目
            source = source_map.get(tag_name, "未知")
            tags[tag_name] = {
                "description": f"标签: {tag_name}",
                "sub_tags": [],
                "source": "vehicle" if source == "车端" else "cloud_operator",
                "operator": source if source != "车端" else "refDictEn",
                "limitations": [],
                "related_tables": ["range_tag"],
            }
        else:
            # 更新已有条目的 source
            if tag_name in source_map:
                src = source_map[tag_name]
                if isinstance(tags[tag_name], dict):
                    tags[tag_name]["source"] = "vehicle" if src == "车端" else "cloud_operator"
                    if src != "车端":
                        tags[tag_name]["operator_path"] = src

    # 3. dynamic_obj.type → 添加到 tags 中
    obj_table = master_tables.get("dynamic_obj", {})
    obj_enum_cols = obj_table.get("enum_columns", {})
    obj_type_info = obj_enum_cols.get("type", {})
    for val in obj_type_info.get("values", []):
        key = f"dynamic_obj.type.{val}"
        if key not in tags:
            OBJ_TRANSLATIONS = {
                "animal": "动物", "bus": "公交车", "car": "轿车", "cyclist": "骑行者",
                "motorcycle": "摩托车", "motorcyclist": "摩托车手", "pedestrian": "行人",
                "stroller": "婴儿车", "suv": "SUV", "truck": "卡车", "wheelchair": "轮椅",
                "traffic_warning_object": "交通警示物",
            }
            tags[key] = {
                "description": OBJ_TRANSLATIONS.get(val, val),
                "source": "vehicle",
                "related_tables": ["dynamic_obj"],
                "column": "type",
            }

    return {
        "version": master.get("version", "2.0.0"),
        "note": (
            "标签与字段语义字典。按 tag_name / field_name 为 key，支持 O(1) 快速查询。\n"
            "包含标签定义、子标签、局限性、来源、相关表等信息。\n"
            "本字典只包含实际会进入 SQLite 的标签和字段。\n"
            "\n本文件由 derive_schemas.py 从 schema_master_raw.yaml 自动派生，请勿手动编辑。"
        ),
        "tags": tags,
    }


def write_yaml(data: dict, path: Path):
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"[OK] 已写入: {path} ({path.stat().st_size} bytes)")


def main():
    master = load_master()
    master_tables = master.get("tables", {})
    rt_table = master_tables.get("range_tag", {})
    rt_enum = rt_table.get("enum_columns", {}).get("tag_name", {})
    print(f"[INFO] 母表加载完成: {len(master_tables)} tables, "
          f"range_tag.tag_name={len(rt_enum.get('values', []))} values, "
          f"tag_semantics={len(master.get('tag_semantics', []))}")

    structure = derive_structure(master)
    dictionary = derive_dictionary(master)

    write_yaml(structure, CORE / "schema_structure.yaml")
    write_yaml(dictionary, CORE / "schema_dictionary.yaml")

    print("[DONE] 派生完成。")


if __name__ == "__main__":
    main()
