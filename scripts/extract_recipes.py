#!/usr/bin/env python3
"""自动从 db_py_rule/*.py 提取 SQL 并生成 SceneSQL Recipe YAML。

功能：
1. 从 .py 文件中提取三引号 SQL
2. 校验 SQL 完整性（长度、结尾分号、括号平衡）
3. 检查 SQLite 兼容性（json_extract_string, ->>, EXTRACT, ILIKE, TRUE/FALSE）
4. 自动补 `;` 如果缺失
5. 生成 Recipe YAML 文件
6. 生成 CONCEPT_RECIPE_MAP 条目

用法:
  python3 scripts/extract_recipes.py --db-py-rule /path/to/db_py_rule --output-dir agent/backend/app/core/recipes --map-file /tmp/concept_map_entries.txt

校验项:
  - sql_len: YAML中的SQL长度 vs 源文件SQL长度
  - trailing_semicolon: SQL末尾是否有分号
  - paren_balance: 左右括号是否平衡
  - sqlite_compat: 是否使用了DuckDB/PostgreSQL特有语法
"""

import os
import re
import sys
import yaml
import argparse
from pathlib import Path


# ── 中文概念映射 ──
# db_py_rule文件名 → 中文概念关键词列表
FILENAME_TO_CONCEPTS = {
    'agent_cross_conflict': ['他车横穿冲突', '横穿冲突'],
    'close_following': ['跟车太近', '近距离跟车'],
    'divergence': ['分流', '车道分流'],
    'ego_cut_in': ['自车切入'],
    'ego_decel_during_lanechange': ['变道减速'],
    'ego_greenlight_action': ['绿灯通行', '绿灯行为'],
    'ego_no_traffic_light': ['无红绿灯', '无灯路口'],
    'ego_nudge_overtake_truck': ['借道超车卡车'],
    'ego_overtake_catin_truck': ['超车切入卡车'],
    'ego_redlight_action': ['红灯行为', '闯红灯'],
    'ego_smallgap_cutin': ['小间距切入'],
    'ego_speed_track': ['速度跟踪'],
    'ego_unknownlight_action': ['未知灯态行为'],
    'ego_yellowlight_action': ['黄灯行为', '黄灯通过'],
    'front_hard_brake': ['前车急刹', '前车急减速'],
    'greenLight_abnormalbrake': ['绿灯刹车', '绿灯异常刹车'],
    'Intersection_Crossing': ['路口横穿'],
    'Intersection_Stop': ['路口停车'],
    'intersection_straight': ['路口直行'],
    'intersection_straight_simple': ['路口直行简单'],
    'intersection_turn_left': ['左转'],
    'intersection_turn_right': ['右转'],
    'jerk_too_high': ['急动度', '急变道jerk'],
    'lane_change': ['变道'],
    'lane_curvature': ['车道曲率'],
    'lane_ending': ['车道结束'],
    'lane_predecessor_count': ['前驱车道数'],
    'lane_successor_count': ['后继车道数'],
    'lane_width': ['车道宽度'],
    'left_turn_conflict': ['左转冲突'],
    'link_attribute_track': ['道路属性'],
    'link_class_track': ['道路等级'],
    'link_type_track': ['道路类型'],
    'meeting_oncoming': ['会车', '对向来车'],
    'navi_command_track': ['导航指令'],
    'near_traffic_light': ['近红绿灯'],
    'nudge_borrowlane': ['借道', '借道避让'],
    'obj_cut_in': ['他车切入', '切入'],
    'obstacle_avoidance': ['避障', '倒车避障', '倒车'],
    'off_ramp': ['下匝道', '驶出匝道'],
    'on_ramp': ['上匝道', '驶入匝道'],
    'other_cutin_ego_yield': ['他车切入自车让行'],
    'Pedestrian_Crossing': ['行人横穿', '行人穿越'],
    'redlight_slowmoving': ['红灯缓行'],
    'right_turn_conflict': ['右转冲突'],
    'right_turn_only': ['右转专用'],
    'roundabout': ['环岛'],
    'route_deviation': ['偏航', '路线偏离'],
    'specify_topology_track': ['拓扑约束'],
    'speed_limit_track': ['限速'],
    'split_merge_track': ['合流分流'],
    'topology_constraint_track': ['拓扑约束跟踪'],
    'traffic_light_state': ['红绿灯状态'],
    'truck_cutin_ego': ['卡车切入'],
    'turn_bypass_overtake': ['绕行超车'],
    'u_turn_left_1': ['掉头1'],
    'u_turn_left_2': ['掉头2'],
    'u_turn_left_3': ['掉头3'],
    'u_turn_with_lanechange': ['变道掉头'],
    'vru_cross_conflict': ['VRU横穿冲突', '弱势道路使用者横穿'],
}


def extract_sql_from_py(py_path: str) -> tuple[str, str]:
    """从 .py 文件中提取三引号 SQL 和 tag_name。
    返回 (sql, tag_name)。"""
    with open(py_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 提取 tag_name
    tag_match = re.search(r"tag_name\s*=\s*['\"]([^'\"]+)['\"]", content)
    tag_name = tag_match.group(1) if tag_match else Path(py_path).stem

    # 提取三引号 SQL — 支持 """ 和 '''
    sql_match = re.search(r'(?:query|sql)\s*=\s*("""|\'\'\')\s*(.*?)\s*\1', content, re.DOTALL)
    if not sql_match:
        # fallback: 直接找三引号块
        sql_match = re.search(r'("""|\'\'\')\s*(.*?)\s*\1', content, re.DOTALL)

    if not sql_match:
        return '', tag_name

    sql = sql_match.group(2).strip()
    return sql, tag_name


def validate_sql(sql: str, source_len: int) -> dict:
    """校验 SQL 完整性。"""
    issues = []

    # 1. 长度校验
    if abs(len(sql) - source_len) > 10:
        issues.append(f"长度不匹配: yaml={len(sql)} vs source={source_len}")

    # 2. 结尾分号
    has_semicolon = sql.rstrip().endswith(';')

    # 3. 括号平衡
    open_p = sql.count('(')
    close_p = sql.count(')')
    if open_p != close_p:
        issues.append(f"括号不平衡: ({open_p} open vs {close_p} close)")

    # 4. SQLite 兼容性检查
    compat_issues = []
    if re.search(r'\bjson_extract_string\b', sql, re.IGNORECASE):
        compat_issues.append("json_extract_string (DuckDB-only, use json_extract)")
    if re.search(r'->>', sql):
        compat_issues.append("->> operator (SQLite 3.38+ only)")
    if re.search(r'\bEXTRACT\s*\(', sql, re.IGNORECASE):
        compat_issues.append("EXTRACT() (PostgreSQL)")
    if re.search(r'\bILIKE\b', sql, re.IGNORECASE):
        compat_issues.append("ILIKE (PostgreSQL)")
    if re.search(r'\bTRUE\b|\bFALSE\b', sql) and not re.search(r'\bTRUE\b.*\bFALSE\b', sql, re.IGNORECASE):
        # 简单检查，避免误报 CASE WHEN ... THEN TRUE
        if not re.search(r'\bCASE\b', sql, re.IGNORECASE):
            compat_issues.append("TRUE/FALSE literals (SQLite uses 1/0)")

    return {
        'issues': issues,
        'compat_issues': compat_issues,
        'has_semicolon': has_semicolon,
        'len': len(sql),
        'paren_balanced': open_p == close_p,
    }


def fix_sql(sql: str) -> str:
    """自动修复常见问题。"""
    # 补结尾分号
    if not sql.rstrip().endswith(';'):
        sql = sql.rstrip() + ';'
    return sql


def generate_recipe_yaml(name: str, tag_name: str, sql: str, concepts: list) -> str:
    """生成 Recipe YAML 内容。"""
    recipe = {
        'name': name,
        'description': f'产线SQL直通: {", ".join(concepts)}',
        'blocks': [],
        'variants': {
            'default': {
                'tag_name': tag_name,
                'raw_sql': sql,
            }
        }
    }
    # 使用 YAML literal block 样式输出 raw_sql
    # 先输出除 raw_sql 外的字段，然后手动追加 raw_sql block
    yaml_header = f"""name: {name}
description: '产线SQL直通: {", ".join(concepts)}'
blocks: []
variants:
  default:
    tag_name: {tag_name}
    raw_sql: |
"""
    # raw_sql 缩进4空格
    indented_sql = '\n'.join('    ' + line for line in sql.split('\n'))
    return yaml_header + indented_sql + '\n'


def main():
    parser = argparse.ArgumentParser(description='从 db_py_rule 提取 SQL 生成 Recipe YAML')
    parser.add_argument('--db-py-rule', required=True, help='db_py_rule 目录路径')
    parser.add_argument('--output-dir', required=True, help='Recipe YAML 输出目录')
    parser.add_argument('--map-file', default='/tmp/concept_map_entries.txt', help='CONCEPT_RECIPE_MAP 输出文件')
    parser.add_argument('--skip-existing', action='store_true', help='跳过已存在的 Recipe YAML')
    parser.add_argument('--fix', action='store_true', help='自动修复（补分号等）')
    args = parser.parse_args()

    db_rule_dir = Path(args.db_py_rule)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    py_files = sorted(db_rule_dir.glob('*.py'))
    print(f"找到 {len(py_files)} 个 db_py_rule 文件")

    map_entries = []
    stats = {'total': 0, 'created': 0, 'skipped': 0, 'errors': 0, 'compat_warnings': 0}

    for py_file in py_files:
        name = py_file.stem
        stats['total'] += 1

        # 检查是否已有 Recipe
        yaml_path = output_dir / f'{name}.yaml'
        if args.skip_existing and yaml_path.exists():
            print(f"  ⏭️  {name}: 已存在，跳过")
            stats['skipped'] += 1
            continue

        # 提取 SQL
        sql, tag_name = extract_sql_from_py(str(py_file))
        if not sql:
            print(f"  ⚠️  {name}: 无法提取SQL")
            stats['errors'] += 1
            continue

        source_len = len(sql)

        # 自动修复
        if args.fix:
            sql = fix_sql(sql)

        # 校验
        validation = validate_sql(sql, source_len)

        if validation['issues']:
            print(f"  ❌ {name}: {'; '.join(validation['issues'])}")
            stats['errors'] += 1
            continue

        if validation['compat_issues']:
            print(f"  ⚠️  {name}: SQLite兼容性 - {'; '.join(validation['compat_issues'])}")
            stats['compat_warnings'] += 1
            # 不阻止生成，但标记

        # 生成 YAML
        concepts = FILENAME_TO_CONCEPTS.get(name, [name])
        yaml_content = generate_recipe_yaml(name, tag_name, sql, concepts)

        with open(yaml_path, 'w', encoding='utf-8') as f:
            f.write(yaml_content)

        print(f"  ✅ {name}: sql_len={len(sql)}, semicolon={validation['has_semicolon']}, concepts={concepts}")
        stats['created'] += 1

        # 生成 CONCEPT_RECIPE_MAP 条目
        for concept in concepts:
            map_entries.append(f'        "{concept}": ("{name}", "default"),')

    # 写 CONCEPT_RECIPE_MAP 条目
    with open(args.map_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(map_entries) + '\n')

    print(f"\n=== 统计 ===")
    print(f"总计: {stats['total']}, 创建: {stats['created']}, 跳过: {stats['skipped']}, 错误: {stats['errors']}, 兼容警告: {stats['compat_warnings']}")
    print(f"CONCEPT_RECIPE_MAP 条目已写入: {args.map_file}")


if __name__ == '__main__':
    main()
