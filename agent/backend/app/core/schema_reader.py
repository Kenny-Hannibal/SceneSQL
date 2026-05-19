#!/usr/bin/env python3
"""
Schema Reader — 读取 SQLite DB 结构并生成带语义描述的 Schema 上下文

通过分析 UBM_mining 的 db_py_rule 和 sql_py 规则文件，
为每个表和字段提供准确的语义描述，供 LLM 生成 SQL 使用。

增强：从 schema_structure.yaml 读取 range_tag.enum，
      从 schema_dictionary.yaml 读取字段定义，确保 prompt 中的标签列表与实际代码同步。
"""

import sqlite3
import yaml
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass


@dataclass
class ColumnInfo:
    name: str
    dtype: str
    description: str = ""


@dataclass
class TableInfo:
    name: str
    description: str
    columns: List[ColumnInfo]
    enum: Optional[List[str]] = None  # 仅用于 range_tag 等需要枚举值的列


# ------------------------------------------------------------------
# 加载 schema_structure.yaml / schema_dictionary.yaml
# ------------------------------------------------------------------

_SCHEMA_DIR = Path(__file__).parent
_SCHEMA_STRUCTURE_PATH = _SCHEMA_DIR / "schema_structure.yaml"
_SCHEMA_DICTIONARY_PATH = _SCHEMA_DIR / "schema_dictionary.yaml"


def _load_yaml(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _get_range_tag_enum(structure: Optional[Dict[str, Any]]) -> List[str]:
    """从 schema_structure.yaml 提取 range_tag.enum 列表。"""
    if not structure:
        return []
    tables = structure.get("database_schema", {}).get("tables", [])
    for t in tables:
        if t.get("name") == "range_tag":
            return t.get("enum", [])
    return []


def _get_field_descriptions(dictionary: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """从 schema_dictionary.yaml 提取 fields 描述，键格式: '表名.列名'。"""
    desc_map: Dict[str, str] = {}
    if not dictionary:
        return desc_map
    fields = dictionary.get("fields", {})
    for key, info in fields.items():
        if isinstance(info, dict):
            desc = info.get("description", "")
            if desc:
                desc_map[key] = desc
    return desc_map


def _get_tag_descriptions(dictionary: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """从 schema_dictionary.yaml 提取 tags 描述，键格式: tag_name。"""
    desc_map: Dict[str, str] = {}
    if not dictionary:
        return desc_map
    tags = dictionary.get("tags", {})
    for key, info in tags.items():
        if isinstance(info, dict):
            desc = info.get("description", "")
            if desc:
                desc_map[key] = desc
    return desc_map


# 字段语义映射 — 基于 UBM_mining 代码分析
FIELD_SEMANTICS = {
    # ego 表（自车状态，10Hz）
    "ego": {
        "_desc": "自车状态表，记录主车（ego）的实时运动状态和感知信息，10Hz采样",
        "ts": "时间戳，单位：纳秒",
        "ts_ms": "时间戳，单位：毫秒",
        "speed": "自车速度，单位：km/h（1Hz降采样）",
        "steering_angle": "方向盘转角",
        "acc_magnitude": "加速度大小",
        "ego_lane_id": "自车所在车道ID",
        "ego_link_id": "自车所在道路link ID",
        "ego_static_map_link_id": "静态地图中的link ID",
        "ego_hq_lane_id": "高精地图车道ID",
        "ego_lane_curvature": "车道曲率",
        "ego_to_centerline_dist": "自车中心到车道中心线的横向距离（米）",
        "ego_to_left_boundary_dist": "自车到左车道边界的距离",
        "ego_to_right_boundary_dist": "自车到右车道边界的距离",
        "traffic_light_status": "红绿灯状态码",
        "latest_traffic_light_status": "最新红绿灯状态（JSON）",
        "latest_stop_line_direction": "停止线方向",
        "navigation_status": "导航状态",
        "wiper_status": "雨刷状态",
        "indicator_status": "转向灯状态（左/右/双闪）",
        "cumulative_distance": "累计行驶距离",
        "utm_x": "UTM坐标X",
        "utm_y": "UTM坐标Y",
        "utm_yaw": "UTM航向角",
        "ego_dr_trajectory": "轨迹JSON，包含5个时间点（0/0.2/0.4/0.6/0.8s）的速度和位置数据",
        "ego_hq_lane_ids_on_cross_section": "路口断面所在的高精车道ID列表（JSON）",
        "ego_lane_index_on_hq_cross_section": "自车在路口断面的车道索引",
        "ego_lane_successor_count": "当前车道的后继车道数量",
        "ego_lane_predecessor_count": "当前车道的前驱车道数量",
        "ego_lane_width": "当前车道宽度",
        "ego_corner_fl_2_left_boundary_dist": "左前角到左边界的距离",
        "ego_corner_fr_2_left_boundary_dist": "右前角到左边界的距离",
        "ego_corner_rl_2_left_boundary_dist": "左后角到左边界的距离",
        "ego_corner_rr_2_left_boundary_dist": "右后角到左边界的距离",
        "ego_corner_fl_2_right_boundary_dist": "左前角到右边界的距离",
        "ego_corner_fr_2_right_boundary_dist": "右前角到右边界的距离",
        "ego_corner_rl_2_right_boundary_dist": "左后角到右边界的距离",
        "ego_corner_rr_2_right_boundary_dist": "右后角到右边界的距离",
    },
    # dynamic_obj 表（动态目标，10Hz）
    "dynamic_obj": {
        "_desc": "动态目标表，记录感知系统检测到的移动物体（车辆、行人、骑行者等），坐标系以自车为原点，X向前、Y向左",
        "ts": "时间戳，单位：纳秒",
        "obj_id": "目标唯一ID",
        "x": "相对自车的纵向距离（米），正前方为正",
        "y": "相对自车的横向距离（米），左侧为正",
        "z": "相对自车的高度（米）",
        "l": "目标长度",
        "w": "目标宽度",
        "h": "目标高度",
        "heading": "目标朝向角",
        "type": "目标类型：car, truck, bus, suv, pedestrian, cyclist, tricycle, unknown_vehicle 等",
        "absolute_velocity_x": "目标绝对速度X分量",
        "absolute_velocity_y": "目标绝对速度Y分量",
        "relative_velocity_x": "相对自车的速度X分量",
        "relative_velocity_y": "相对自车的速度Y分量",
        "is_static": "是否为静止目标（0=运动，1=静止）",
        "obs_lane_id": "观察到的车道ID",
        "obs_static_map_link_id": "静态地图link ID",
        "obs_hq_lane_id": "高精地图车道ID",
        "obs_to_centerline_dist": "目标到车道中心线距离",
        "obs_to_left_boundary_dist": "目标到左边界距离",
        "obs_to_right_boundary_dist": "目标到右边界距离",
        "obs_corner_fl_2_left_boundary_dist": "目标左前角到左边界的距离",
        "obs_corner_fr_2_left_boundary_dist": "目标右前角到左边界的距离",
        "obs_corner_rl_2_left_boundary_dist": "目标左后角到左边界的距离",
        "obs_corner_rr_2_left_boundary_dist": "目标右后角到左边界的距离",
        "obs_corner_fl_2_right_boundary_dist": "目标左前角到右边界的距离",
        "obs_corner_fr_2_right_boundary_dist": "目标右前角到右边界的距离",
        "obs_corner_rl_2_right_boundary_dist": "目标左后角到右边界的距离",
        "obs_corner_rr_2_right_boundary_dist": "目标右后角到右边界的距离",
        "obs_dr_trajectory": "目标轨迹预测JSON",
    },
    # static_obj 表（静态目标）
    "static_obj": {
        "_desc": "静态目标表，记录交通标志、红绿灯、锥桶等静态物体",
        "ts": "时间戳",
        "obj_id": "目标ID",
        "ts_ms": "时间戳（毫秒）",
        "x": "相对自车的纵向距离",
        "y": "相对自车的横向距离",
        "z": "高度",
        "l": "长度",
        "w": "宽度",
        "h": "高度",
        "heading": "朝向",
        "type": "类型：traffic_sign, traffic_light, cone 等",
        "param": "参数JSON",
    },
    # range_tag 表（场景标签）
    "range_tag": {
        "_desc": "场景标签表，记录算法识别或人工标注的场景片段。标签信息存储在param JSON字段中",
        "start_ts": "标签开始时间，单位：秒",
        "end_ts": "标签结束时间，单位：秒",
        "tag_name": "标签名称（映射后的名称）",
        "param": "标签参数JSON，包含 name（原始算子名）、sub_tag（子标签）、object_type（目标类型）、reason_summary（原因摘要）等",
    },
    # static_link 表
    "static_link": {
        "_desc": "静态道路link表，记录道路拓扑结构",
        "link_id": "link ID",
        "link_type": "道路类型",
        "link_turn_type": "转向类型",
        "link_class": "道路等级",
        "link_attribute": "道路属性",
        "link_speed_limit": "限速",
        "link_predecessor": "前驱link（JSON列表）",
        "link_successor": "后继link（JSON列表）",
        "link_exp_speed_limit": "期望限速",
    },
    # static_lane 表
    "static_lane": {
        "_desc": "静态车道表",
        "lane_id": "车道ID",
    },
    # dynamic_link 表
    "dynamic_link": {
        "_desc": "动态link表，记录自车当前所在link的实时信息",
        "ts": "时间戳",
        "link_id": "link ID",
        "link_type": "道路类型",
        "link_attribute": "道路属性",
        "link_exp_speed_limit": "期望限速",
        "link_predecessor": "前驱link",
        "link_successor": "后继link",
        "include_lane_ids": "包含的车道ID列表（JSON）",
    },
    # dynamic_lane 表
    "dynamic_lane": {
        "_desc": "动态车道表，记录自车当前所在车道的实时信息",
        "ts": "时间戳",
        "lane_id": "车道ID",
        "ref_link_id": "参考link ID",
        "left_boundary_id": "左边界ID",
        "right_boundary_id": "右边界ID",
        "lane_type": "车道类型",
        "lane_turn_type": "转向类型",
        "lane_trans_type": "过渡类型",
        "predecessors": "前驱车道（JSON）",
        "successors": "后继车道（JSON）",
        "lane_relate_obs_ids": "相关观察目标ID（JSON）",
    },
    # intersection_info 表
    "intersection_info": {
        "_desc": "路口信息表",
        "intersection_id": "路口ID",
        "lane_count": "路口车道数",
        "ego_lane_index": "自车所在车道索引",
        "lane_info": "车道信息JSON",
    },
}


def read_schema(db_path: str) -> List[TableInfo]:
    """读取 SQLite 数据库的完整 Schema，并注入语义描述。"""
    # 加载外部 schema 文档
    structure = _load_yaml(_SCHEMA_STRUCTURE_PATH)
    dictionary = _load_yaml(_SCHEMA_DICTIONARY_PATH)
    field_desc_map = _get_field_descriptions(dictionary)
    tag_desc_map = _get_tag_descriptions(dictionary)
    range_tag_enum = _get_range_tag_enum(structure)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [t[0] for t in cursor.fetchall()]

    result = []
    for t in tables:
        cursor.execute(f"PRAGMA table_info({t})")
        cols = cursor.fetchall()
        semantics = FIELD_SEMANTICS.get(t, {})
        table_desc = semantics.get("_desc", f"表 {t}")
        col_infos = []
        for col in cols:
            col_name = col[1]
            col_type = col[2]
            # 优先从 schema_dictionary.yaml 读取字段描述，回退到 FIELD_SEMANTICS
            dict_key = f"{t}.{col_name}"
            col_desc = field_desc_map.get(dict_key, semantics.get(col_name, ""))
            col_infos.append(ColumnInfo(name=col_name, dtype=col_type, description=col_desc))

        # 为 range_tag 附加 enum
        enum = None
        if t == "range_tag" and range_tag_enum:
            enum = range_tag_enum

        result.append(TableInfo(name=t, description=table_desc, columns=col_infos, enum=enum))

    conn.close()
    return result


def format_schema_for_prompt(tables: List[TableInfo], max_columns_per_table: int = 50) -> str:
    """将 Schema 格式化为 Markdown，供 LLM Prompt 使用。"""
    lines = ["# 数据库 Schema", ""]
    for t in tables:
        lines.append(f"## {t.name}")
        lines.append(f"{t.description}")
        lines.append("")

        # 注入 enum 信息（主要用于 range_tag）
        if t.enum:
            lines.append(f"> **tag_name 可选值**（共 {len(t.enum)} 个）：")
            # 将 enum 分组显示，每行 8 个，避免单行过长
            enum_chunks = [t.enum[i:i + 8] for i in range(0, len(t.enum), 8)]
            for chunk in enum_chunks:
                lines.append("> " + ", ".join(f"`{v}`" for v in chunk))
            lines.append("")

        lines.append("| 字段名 | 类型 | 描述 |")
        lines.append("|--------|------|------|")
        for c in t.columns[:max_columns_per_table]:
            desc = c.description or "-"
            # 对 range_tag.tag_name 额外提示可用 enum
            if t.name == "range_tag" and c.name == "tag_name" and t.enum:
                desc += f" （见上方可选值，共 {len(t.enum)} 个）"
            lines.append(f"| {c.name} | {c.dtype} | {desc} |")
        if len(t.columns) > max_columns_per_table:
            lines.append(f"| ... | ... | 共 {len(t.columns)} 个字段，已截断 |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python schema_reader.py <db_path>")
        exit(1)
    schema = read_schema(sys.argv[1])
    print(format_schema_for_prompt(schema))
