#!/usr/bin/env python3
"""
derive_schemas.py — 从母表自动派生 structure.yaml 和 dictionary.yaml

派生规则:
- structure.yaml: 从母表的 sqlite_database.tables 提取表结构，
                  从 range_tag.tag_enum 提取 enum，
                  从 range_tag 部分提取 notes（structure 版本的 notes）
- dictionary.yaml: 从母表的 range_tag.tag_semantics 提取每个 tag 的语义信息，
                   同时从 dictionary 的 fields 部分提取字段语义

输出格式尽量与原文件一致（key 顺序、缩进风格）。
使用 sort_keys=False 保持 YAML key 顺序。
"""

import sys
from pathlib import Path
from copy import deepcopy

import yaml

# ---------- 路径配置 ----------
CORE_DIR = Path(__file__).resolve().parent
MASTER       = CORE_DIR / 'schema_master.yaml'
STRUCT_OUT   = CORE_DIR / 'schema_structure.yaml'
DICT_OUT     = CORE_DIR / 'schema_dictionary.yaml'

# structure.yaml 中表类型的映射（从原 structure.yaml 的 type 字段）
# 这些 type 字段在 master_raw 的 sqlite_database.tables 中不存在，
# 需要硬编码映射或从原 structure.yaml 读取
TABLE_TYPE_MAP = {
    'range_tag':         'horizontal_event',
    'intersection_info': 'horizontal_event',
    'ego':               'vertical_timeseries',
    'dynamic_obj':       'vertical_timeseries',
    'static_obj':        'vertical_timeseries',
    'static_lane':       'static_reference',
    'static_link':       'static_reference',
    'dynamic_lane':      'vertical_timeseries',
    'dynamic_link':      'vertical_timeseries',
}

# range_tag / intersection_info 的 notes（从原 structure.yaml 保留）
RANGE_TAG_NOTES = [
    '车端行为标签还可能包含以下前缀类别（取决于 bag 文件中的 beh_tag 消息）： CRUISE_*, STOPANDGO_*, LANECHANGE_*, AVOIDANCE_*, INTERSECTION_*',
    'EgoIntoIntersection 被过滤到 intersection_info 表，不在 range_tag 中',
    'param 列为 JSON 字符串，可用 json_extract() 提取子标签（sub_tag）、障碍物 ID 等',
]

INTERSECTION_INFO_NOTES = [
    '存储 EgoIntoIntersection 事件，从 range_tag 中过滤后单独存放',
]


def load_yaml(path: Path) -> dict:
    """加载 YAML 文件，保持 key 顺序。"""
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def save_yaml(data: dict, path: Path) -> None:
    """保存 YAML 文件。"""
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(
            data, f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )
    print(f'  ✓ 已写入: {path}')


# ---------- structure.yaml 派生 ----------

def convert_column_for_structure(col: dict) -> dict:
    """
    将母表的列格式转为 structure 格式。
    母表: {name, type, notnull, pk}  →  structure: {name, type, nullable, pk?}
    
    注意: 母表中 notnull 字段语义为"NOT NULL 约束是否存在"，
    notnull: false = 无 NOT NULL 约束 = 可为空。
    但原 structure.yaml 中所有列统一标为 nullable: false（表示不可为空），
    这是一种简化约定。为保持与原文件一致，这里也统一设为 nullable: false。
    """
    out = {}
    out['name'] = col['name']
    out['type'] = col['type']
    # 保持与原 structure.yaml 一致：所有列标为 nullable: false
    out['nullable'] = False
    return out


def derive_structure(master: dict) -> dict:
    """
    从母表派生 structure.yaml 内容。
    """
    master_tables = master.get('sqlite_database', {}).get('tables', [])
    tag_enum = master.get('range_tag', {}).get('tag_enum', [])

    # 构建 structure 版的 tables 列表
    struct_tables = []
    for tbl in master_tables:
        tbl_name = tbl.get('name', '')
        struct_tbl = {}

        struct_tbl['name'] = tbl_name
        # 添加 type（从映射表）
        if tbl_name in TABLE_TYPE_MAP:
            struct_tbl['type'] = TABLE_TYPE_MAP[tbl_name]

        # 转换列格式
        struct_tbl['columns'] = [convert_column_for_structure(c) for c in tbl.get('columns', [])]

        # primary_key（仅 range_tag 有复合主键需要标注）
        pk_cols = [c for c in tbl.get('columns', []) if c.get('pk', False)]
        if pk_cols and tbl_name == 'range_tag':
            struct_tbl['primary_key'] = [c['name'] for c in pk_cols]

        # enum（仅 range_tag）
        if tbl_name == 'range_tag' and tag_enum:
            struct_tbl['enum'] = tag_enum

        # notes（仅 range_tag 和 intersection_info）
        if tbl_name == 'range_tag':
            struct_tbl['notes'] = RANGE_TAG_NOTES
        elif tbl_name == 'intersection_info':
            struct_tbl['notes'] = INTERSECTION_INFO_NOTES

        # 为有 pk 的列在 columns 中标注 pk（structure 原文件中 ego/dynamic_obj 等用 pk: true）
        if tbl_name != 'range_tag':  # range_tag 用 primary_key 列表
            for col in struct_tbl['columns']:
                orig_col = next((c for c in tbl.get('columns', []) if c['name'] == col['name']), None)
                if orig_col and orig_col.get('pk', False):
                    col['pk'] = True

        struct_tables.append(struct_tbl)

    # 调整表顺序：将 range_tag 和 intersection_info 放在最前面
    # （原 structure.yaml 中 range_tag 排第一，便于 LLM 优先关注）
    priority_tables = ['range_tag', 'intersection_info']
    reordered = []
    for pname in priority_tables:
        match = next((t for t in struct_tables if t['name'] == pname), None)
        if match:
            reordered.append(match)
    for t in struct_tables:
        if t['name'] not in priority_tables:
            reordered.append(t)
    struct_tables = reordered

    # 最终 structure 对象
    result = {}
    result['version'] = master.get('version', '1.2.0')
    result['note'] = (
        '纯 SQLite 结构文件，用于 LLM SQL 生成阶段注入 prompt。 '
        '不含任何自然语言解释，只包含表名、列名、数据类型、主键、标签枚举值。 '
        '字段/标签的详细定义请查阅 schema_dictionary.yaml。'
    )
    result['database_schema'] = {
        'tables': struct_tables,
    }
    result['git_version'] = master.get('git_version', {})

    return result


# ---------- dictionary.yaml 派生 ----------

def derive_dictionary(master: dict) -> dict:
    """
    从母表派生 dictionary.yaml 内容。

    tags 部分来自 range_tag.tag_semantics，
    fields 部分需要从原 dictionary.yaml 的 fields 保留（因为母表中不含字段级语义），
    git_version 从母表复制。
    """
    tag_semantics = master.get('range_tag', {}).get('tag_semantics', {})

    result = {}
    result['version'] = master.get('version', '1.2.0')
    result['note'] = (
        '标签与字段语义字典。按 tag_name / field_name 为 key，支持 O(1) 快速查询。 '
        '包含标签定义、子标签、局限性、来源、相关表等信息。 '
        '本字典只包含实际会进入 SQLite 的标签和字段。'
    )
    result['tags'] = tag_semantics
    result['fields'] = {}  # 将从原 dictionary 中填充

    result['git_version'] = master.get('git_version', {})

    return result


def enrich_dictionary_fields(derived_dict: dict, original_dict: dict) -> dict:
    """
    用原 dictionary.yaml 的 fields 部分丰富派生的 dictionary。
    母表不包含字段级语义信息，所以需要从原文件保留。
    """
    original_fields = original_dict.get('fields', {})
    if original_fields:
        derived_dict['fields'] = deepcopy(original_fields)
        print(f'  ✓ 从原 dictionary.yaml 保留 {len(original_fields)} 个字段语义')
    else:
        print('  ⚠ 原 dictionary.yaml 中无 fields 部分')

    # 同时保留 car_end_behavior_tags 等非 tag 的顶级语义条目（如果有的话）
    # 检查原 dictionary.tags 中是否有一些母表 tag_semantics 中没有的额外 key
    original_tags = original_dict.get('tags', {})
    derived_tags = derived_dict.get('tags', {})

    # 原字典中有但母表 tag_semantics 中没有的 tag
    missing_in_derived = set(original_tags.keys()) - set(derived_tags.keys())
    if missing_in_derived:
        print(f'  ⚠ 原 dictionary 有 {len(missing_in_derived)} 个 tag 不在母表 tag_semantics 中，将保留:')
        for t in sorted(missing_in_derived):
            print(f'      - {t}')
            derived_tags[t] = deepcopy(original_tags[t])

    return derived_dict


def main():
    print('=' * 60)
    print('SceneSQL Schema 派生 — 从母表生成 structure & dictionary')
    print('=' * 60)

    # 1. 加载母表
    print('\n[1/4] 加载母表...')
    if not MASTER.exists():
        print(f'  ✗ 母表文件不存在: {MASTER}')
        print('  请先运行 refactor_schema.py 生成母表')
        sys.exit(1)
    master = load_yaml(MASTER)
    print(f'  ✓ schema_master.yaml 已加载')

    # 同时加载原 dictionary.yaml 以获取 fields 部分
    original_dict_path = CORE_DIR / '_backup_pre_refactor' / 'schema_dictionary.yaml'
    original_dict = {}
    if original_dict_path.exists():
        original_dict = load_yaml(original_dict_path)
        print(f'  ✓ 原始 dictionary.yaml 已加载（用于 fields 保留）')
    else:
        print(f'  ⚠ 未找到原始 dictionary.yaml: {original_dict_path}')
        print('  → fields 部分将无法保留')

    # 2. 派生 structure
    print('\n[2/4] 派生 structure.yaml...')
    structure = derive_structure(master)
    struct_tables = structure.get('database_schema', {}).get('tables', [])
    range_tag_table = next((t for t in struct_tables if t.get('name') == 'range_tag'), {})
    enum_count = len(range_tag_table.get('enum', []))
    print(f'  ✓ 包含 {len(struct_tables)} 个表定义')
    print(f'  ✓ range_tag.enum 包含 {enum_count} 个值')

    # 3. 派生 dictionary
    print('\n[3/4] 派生 dictionary.yaml...')
    dictionary = derive_dictionary(master)
    if original_dict:
        dictionary = enrich_dictionary_fields(dictionary, original_dict)
    tags_count = len(dictionary.get('tags', {}))
    fields_count = len(dictionary.get('fields', {}))
    print(f'  ✓ 包含 {tags_count} 个 tag 语义')
    print(f'  ✓ 包含 {fields_count} 个 field 语义')

    # 4. 保存
    print('\n[4/4] 保存派生文件...')
    save_yaml(structure, STRUCT_OUT)
    save_yaml(dictionary, DICT_OUT)

    print('\n' + '=' * 60)
    print('派生完成！生成文件:')
    print(f'  - {STRUCT_OUT}')
    print(f'  - {DICT_OUT}')
    print('=' * 60)


if __name__ == '__main__':
    main()
