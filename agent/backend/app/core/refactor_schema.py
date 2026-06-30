#!/usr/bin/env python3
"""
refactor_schema.py — 一次性迁移脚本

读取现有三个 YAML schema 文件，合并生成新母表 schema_master.yaml。

新母表在现有 schema_master_raw.yaml 基础上：
1. 在 range_tag 顶级下新增 tag_enum（从 schema_structure.yaml 的 enum 迁移）
2. 在 range_tag 顶级下新增 tag_semantics（从 schema_dictionary.yaml 的 tags 迁移）

其他所有内容（sqlite_database, injection_sources, actual_tags_from_sample_db,
potential_tags_not_in_sample, not_in_sqlite, git_version 等）原样保留。
"""

import sys
from pathlib import Path
from copy import deepcopy

import yaml

# ---------- 路径配置 ----------
CORE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = CORE_DIR / '_backup_pre_refactor'
MASTER_RAW = BACKUP_DIR / 'schema_master_raw.yaml'
STRUCTURE  = BACKUP_DIR / 'schema_structure.yaml'
DICTIONARY = BACKUP_DIR / 'schema_dictionary.yaml'
OUTPUT     = CORE_DIR / 'schema_master.yaml'


def load_yaml(path: Path) -> dict:
    """加载 YAML 文件，保持 key 顺序。"""
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def save_yaml(data: dict, path: Path) -> None:
    """保存 YAML 文件，保持 key 顺序和可读性。"""
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(
            data, f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )
    print(f'  ✓ 已写入: {path}')


def extract_tag_enum(structure_data: dict) -> list:
    """从 schema_structure.yaml 的 range_tag 表定义中提取 enum 列表。"""
    tables = structure_data.get('database_schema', {}).get('tables', [])
    for table in tables:
        if table.get('name') == 'range_tag':
            enum = table.get('enum', [])
            if enum:
                print(f'  ✓ 从 structure.yaml 提取到 {len(enum)} 个 tag_enum 值')
                return enum
    print('  ✗ 未在 structure.yaml 中找到 range_tag.enum')
    return []


def extract_tag_semantics(dictionary_data: dict) -> dict:
    """从 schema_dictionary.yaml 的 tags 部分提取语义信息。"""
    tags = dictionary_data.get('tags', {})
    if tags:
        print(f'  ✓ 从 dictionary.yaml 提取到 {len(tags)} 个 tag 语义')
    else:
        print('  ✗ 未在 dictionary.yaml 中找到 tags')
    return tags


def build_new_master(raw: dict, tag_enum: list, tag_semantics: dict) -> dict:
    """
    在 raw master 基础上，向 range_tag 部分注入 tag_enum 和 tag_semantics。
    """
    master = deepcopy(raw)
    range_tag = master.get('range_tag', {})

    # 注入 tag_enum
    range_tag['tag_enum'] = tag_enum

    # 注入 tag_semantics
    range_tag['tag_semantics'] = tag_semantics

    master['range_tag'] = range_tag
    return master


def validate(master: dict, tag_enum: list, tag_semantics: dict) -> None:
    """基本校验：tag_enum 和 tag_semantics 的 key 是否对齐。"""
    enum_set = set(tag_enum)
    semantics_set = set(tag_semantics.keys())

    only_in_enum = enum_set - semantics_set
    only_in_semantics = semantics_set - enum_set

    if only_in_enum:
        print(f'  ⚠ tag_enum 中有 {len(only_in_enum)} 个 tag 在 tag_semantics 中无对应:')
        for t in sorted(only_in_enum):
            print(f'      - {t}')

    if only_in_semantics:
        print(f'  ⚠ tag_semantics 中有 {len(only_in_semantics)} 个 tag 不在 tag_enum 中:')
        for t in sorted(only_in_semantics):
            print(f'      - {t}')

    if not only_in_enum and not only_in_semantics:
        print(f'  ✓ tag_enum 与 tag_semantics 完全对齐（{len(enum_set)} 个 tag）')


def main():
    print('=' * 60)
    print('SceneSQL Schema 重构 — 一次性迁移')
    print('=' * 60)

    # 1. 加载三个源文件
    print('\n[1/4] 加载源文件...')
    raw_data       = load_yaml(MASTER_RAW)
    structure_data = load_yaml(STRUCTURE)
    dict_data      = load_yaml(DICTIONARY)
    print(f'  ✓ schema_master_raw.yaml  ({len(str(raw_data))} chars loaded)')
    print(f'  ✓ schema_structure.yaml   ({len(str(structure_data))} chars loaded)')
    print(f'  ✓ schema_dictionary.yaml  ({len(str(dict_data))} chars loaded)')

    # 2. 提取要合并的数据
    print('\n[2/4] 提取待合并数据...')
    tag_enum     = extract_tag_enum(structure_data)
    tag_semantics = extract_tag_semantics(dict_data)

    # 3. 构建新母表
    print('\n[3/4] 构建新母表...')
    new_master = build_new_master(raw_data, tag_enum, tag_semantics)
    print('  ✓ 新母表构建完成')

    # 4. 校验 & 保存
    print('\n[4/4] 校验 & 保存...')
    validate(new_master, tag_enum, tag_semantics)
    save_yaml(new_master, OUTPUT)

    print('\n' + '=' * 60)
    print('迁移完成！新母表: schema_master.yaml')
    print('后续可运行 derive_schemas.py 验证派生结果')
    print('=' * 60)


if __name__ == '__main__':
    main()
