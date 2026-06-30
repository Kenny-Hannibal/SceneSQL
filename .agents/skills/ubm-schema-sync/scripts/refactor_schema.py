#!/usr/bin/env python3
"""
一次性迁移脚本：将 schema_structure.yaml 和 schema_dictionary.yaml 的信息
合并到 schema_master_raw.yaml，生成新的统一母表。

运行一次后，schema_structure.yaml 和 schema_dictionary.yaml 将由
derive_schemas.py 从新母表自动派生。
"""
import sys
from pathlib import Path
import yaml

BASE = Path(__file__).resolve().parent
CORE = BASE.parent.parent.parent.parent / "agent" / "backend" / "app" / "core"

def ordered_yaml_load(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)

def main():
    master = ordered_yaml_load(CORE / "schema_master_raw.yaml")
    structure = ordered_yaml_load(CORE / "schema_structure.yaml")
    dictionary = ordered_yaml_load(CORE / "schema_dictionary.yaml")

    # 1. 从 structure.yaml 提取 range_tag 的 enum
    struct_tables = structure["database_schema"]["tables"]
    rt_struct = next(t for t in struct_tables if t["name"] == "range_tag")
    tag_enum = rt_struct.get("enum", [])
    print(f"[INFO] structure.yaml range_tag enum: {len(tag_enum)} tags")

    # 2. 从 structure.yaml 提取 range_tag 的 notes
    rt_notes = rt_struct.get("notes", [])

    # 3. 从 dictionary.yaml 提取 tag_semantics
    tag_semantics = dictionary.get("tags", {})
    print(f"[INFO] dictionary.yaml tags: {len(tag_semantics)} tags")

    # 4. 合并到母表的 range_tag 部分
    if "range_tag" not in master:
        master["range_tag"] = {}

    master["range_tag"]["tag_enum"] = tag_enum
    master["range_tag"]["tag_semantics"] = tag_semantics
    master["range_tag"]["notes"] = rt_notes

    # 5. 更新 version 和 note
    master["version"] = "2.0.0"
    master["note"] = (
        "本文件是 SceneSQL Schema 的唯一权威源（母表），包含：\n"
        "  - SQLite 数据库完整表结构（9张表）\n"
        "  - range_tag 标签枚举（tag_enum）\n"
        "  - 标签语义描述（tag_semantics）\n"
        "  - 注入源说明、实际/潜在标签列表、git_version\n"
        "\n"
        "schema_structure.yaml 和 schema_dictionary.yaml 由 derive_schemas.py 从本文件自动派生。\n"
    )

    # 6. 写入新母表
    out_path = CORE / "schema_master_raw.yaml"
    with open(out_path, "w") as f:
        yaml.dump(master, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"[OK] 新母表已写入: {out_path}")
    print(f"     tag_enum: {len(tag_enum)} entries")
    print(f"     tag_semantics: {len(tag_semantics)} entries")

if __name__ == "__main__":
    main()
