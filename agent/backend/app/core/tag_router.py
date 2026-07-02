#!/usr/bin/env python3
"""
Tag Router — 关键词路由 + 标签语义注入

从 schema_dictionary.yaml 自动构建关键词索引，
将用户 NL query 路由到相关标签和表，并格式化为 LLM 可用的上下文。

P0 实现：纯关键词路由，零 LLM 成本。
P2 阶段将增加 RAG 语义检索作为兜底。
"""

import yaml
import re
from pathlib import Path
from typing import List, Dict, Set, Optional, Any
from dataclasses import dataclass, field


_SCHEMA_DIR = Path(__file__).parent
_SCHEMA_DICTIONARY_PATH = _SCHEMA_DIR / "schema_dictionary.yaml"
_SCHEMA_STRUCTURE_PATH = _SCHEMA_DIR / "schema_structure.yaml"


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

@dataclass
class TagInfo:
    """单个标签的完整语义信息。"""
    tag_name: str
    description: str
    sub_tags: List[str] = field(default_factory=list)
    related_tables: List[str] = field(default_factory=list)
    source: str = ""
    limitations: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)  # 从 description 提取的中文关键词


@dataclass
class RouteResult:
    """路由结果。"""
    matched_tags: List[TagInfo] = field(default_factory=list)
    involved_tables: Set[str] = field(default_factory=set)
    method: str = "keyword"  # keyword / rag / fallback
    map_enum_hits: List[Dict[str, str]] = field(default_factory=list)  # map表枚举值命中


# ──────────────────────────────────────────────
# 表分类与 Schema Card
# ──────────────────────────────────────────────

TABLE_CATEGORIES = {
    "horizontal_event": {
        "range_tag": "场景标签片段（变道/切入/急刹/路口等 60+ 种），时间单位秒",
        "intersection_info": "路口信息（EgoIntoIntersection 事件的路口车道详情）",
    },
    "vertical_timeseries": {
        "ego": "自车状态（速度/方向盘/车道/红绿灯），10Hz，时间单位纳秒",
        "dynamic_obj": "动态目标（车辆/行人/骑行者），10Hz，时间单位纳秒",
        "static_obj": "静态目标（交通标志/红绿灯/锥桶），时间单位纳秒",
    },
    "dynamic_ref": {
        "dynamic_lane": "动态车道信息（车道类型/边界/前后驱）",
        "dynamic_link": "动态道路 link 信息（道路类型/限速）",
    },
    "static_ref": {
        "static_lane": "车道 ID（仅 lane_id）",
        "static_link": "道路类型/转向/限速/前后驱 link",
    },
}

# 表 → 默认 Domain（用于 few-shot 模板检索）
TABLE_DOMAIN_MAP = {
    "range_tag": "scenario",
    "intersection_info": "scenario",
    "ego": "ego_state",
    "dynamic_obj": "perception",
    "static_obj": "perception",
    "dynamic_lane": "road",
    "dynamic_link": "road",
    "static_lane": "road",
    "static_link": "road",
}
# 跨表关联映射到 cross / trajectory domain
CROSS_TABLE_COMBOS = {
    frozenset({"range_tag", "ego"}): "cross",
    frozenset({"range_tag", "dynamic_obj"}): "cross",
    frozenset({"range_tag", "ego", "dynamic_obj"}): "cross",
    frozenset({"ego", "dynamic_obj"}): "trajectory",
}


# ──────────────────────────────────────────────
# 关键词索引构建
# ──────────────────────────────────────────────

# 手工补充的关键词映射（覆盖自动提取覆盖不到的同义词/口语表达）
_MANUAL_KEYWORD_OVERRIDES: Dict[str, List[str]] = {
    "LaneChange": ["变道", "换道", "并线"],
    "AbnormalLaneChange": ["异常变道", "无理由变道"],
    "SolidLaneChange": ["实线变道", "压线变道", "违规变道"],
    "Cutin": ["切入", "加塞", "插队", "cut-in", "cutin", "Cutin"],
    "CloseFollow": ["跟车太近", "近距离跟车", "贴车", "紧跟"],
    "CrawlingFollow": ["蠕行跟车", "走走停停"],
    "Jerk": ["急顿挫", "急加速", "猛踩", "急减速", "顿挫感", "顿挫"],
    "Intersection": ["路口", "交叉路口", "十字路口"],
    "TrafficIntersection": ["红绿灯路口", "信号灯路口", "红绿灯"],
    "RunRedLight": ["闯红灯", "红灯"],
    "CrossStopLineOnYellowLight": ["黄灯过线", "抢黄灯", "黄灯"],
    "GreenLightNotProceeding": ["绿灯不走", "绿灯未起步", "绿灯"],
    "TrafficLightAbnormal": ["红绿灯异常", "信号灯异常", "红绿灯"],
    "STOPANDGO_FIRSTCARSTARTATGREENLIGHT": ["绿灯起步", "绿灯通行", "绿灯"],
    "STOPANDGO_FIRSTCARSTOPATREDLIGHT": ["红灯停车", "红灯停", "红灯"],
    "STOPANDGO_TRAFFICLIGHTGREENFLASHGO": ["绿灯闪烁通行"],
    "STOPANDGO_TRAFFICLIGHTREDSTART": ["红灯起步"],
    "STOPANDGO_TRAFFICLIGHTREDWAIT": ["红灯等待"],
    "STOPANDGO_STARTFOLLOWOBSTACLE": ["起步跟车", "起步", "停车后起步", "跟随起步"],
    "STOPANDGO_STARTNONFOLLOWOBSTACLE": ["自由起步", "无前车起步"],
    "STOPANDGO_STOP": ["停车", "刹停", "停住"],
    "CrossVRUV1": ["行人横穿", "VRU横穿", "骑行者横穿"],
    "CrossVehicle": ["车辆横穿", "侧方来车"],
    "LowTTC": ["碰撞风险", "TTC低", "低时距", "时距短", "即将碰撞"],
    "ObstacleCollision": ["碰撞", "撞障碍物"],
    "ObstacleNearMiss": ["近碰", "险碰", "差点撞"],
    "LaneKeep": ["保持车道", "车道保持", "车道居中"],
    "StraightDriving": ["直行", "直行行驶", "直线行驶", "直行通过"],
    "LaneMerging": ["车道合并", "合流"],
    "OnRamp": ["上匝道", "进匝道"],
    "OffRamp": ["下匝道", "出匝道"],
    "Roundabout": ["环岛", "转盘"],
    "Slope": ["坡道", "上坡", "下坡"],
    "AvoidanceBorrowLane": ["借道避让", "避让借道"],
    "Avoidance_InLane": ["车道内避让", "避障"],
    "OverSpeedLimit": ["超速", "超速行驶"],
    "RainLevel": ["下雨", "降雨", "雨量"],
    "ActiveWiperState": ["雨刮", "雨刷"],
    "SteeringWheelSlam": ["猛打方向盘", "急转向"],
    "SteeringSmallSwing": ["方向盘抖动", "方向盘摆动", "方向不稳", "转向不稳", "开车不稳"],
    "Jerk": ["急顿挫", "急加速", "猛踩", "急减速", "顿挫感", "顿挫", "开车不稳"],
    "HighSteeringWheelTorque": ["方向盘力矩大", "方向盘重", "转向力大", "开车不稳"],
    "Turning": ["转向", "左转", "右转", "掉头"],
    "NotCenter": ["偏中心", "不在车道中心"],
    "SolidLaneChange": ["实线变道", "压线变道"],
    "route_deviation": ["偏航", "路线偏离"],
    "hard_braking": ["急刹", "急刹车", "猛刹", "紧急制动"],
    "stationary": ["静止", "停车"],
    "creeping": ["蠕行", "缓慢移动"],
    "cruising": ["巡航", "匀速行驶"],
    "decelerating": ["减速"],
    "accelerating": ["加速"],
    "reversing": ["倒车"],
    # 车端行为标签
    "CRUISE_CURVE": ["弯道", "弯道巡航", "曲线行驶", "弯路", "转弯路段"],
    "CRUISE_CONGESTION": ["拥堵", "拥堵路段", "堵车", "塞车"],
    "CongestedFollow": ["拥堵跟车", "堵车跟车", "拥堵路段跟车"],
    "CRUISE_CARORTRUCKCUTOUT": ["前车切出", "切出", "cut-out", "cutout", "CutOut"],
    "CRUISE_CARORTRUCKCUTIN": ["前车切入", "巡航切入"],
    "CRUISE_FOLLOW": ["跟车巡航"],
    "CRUISE_STRAIGHT": ["直道巡航"],
    "INTERSECTION_RIGHTTURN": ["路口右转"],
    "INTERSECTION_STRAIGHT": ["路口直行"],
}

# 额外的"表级"关键词（不指向特定标签，但指向特定表）
_TABLE_KEYWORDS: Dict[str, List[str]] = {
    "ego": ["速度", "方向盘", "车道", "红绿灯", "自车", "加速度", "跟车",
            "航向", "UTM", "坐标", "边界距离", "曲率", "转向灯", "雨刮",
             "导航", "累计里程"],
    "dynamic_obj": ["行人", "车辆", "障碍物", "目标", "检测", "前方",
                    "前车", "侧方", "物体", "骑行者", "行人横穿", "bbox",
                    "切入车辆", "横穿"],
    "static_obj": ["交通标志", "红绿灯", "锥桶", "标牌", "静止物体"],
    "range_tag": ["标签", "场景", "片段", "时间段", "标签名"],
    "intersection_info": ["路口", "车道数"],
    "dynamic_lane": ["车道线", "车道边界", "车道类型"],
    "dynamic_link": ["link", "道路link", "限速"],
    "static_link": ["道路类型", "转向类型", "道路等级", "匝道", "主路", "辅路", "高速公路",
                    "国道", "省道", "隧道", "桥梁", "收费站", "环岛"],
    "static_lane": ["车道ID"],
}

# map表枚举值关键词 → (表名, 列名, 枚举值) 映射
# map表枚举值关键词 → (表名, 列名, 枚举值) 映射
# link_type枚举值包含："主路"(167 DBs)和"主干道"(459 DBs)，两者不同
_MAP_ENUM_KEYWORDS: List[Dict[str, str]] = [
    # static_link.link_type
    {"kw": "主路", "table": "static_link", "column": "link_type", "value": "主路"},
    {"kw": "主干道", "table": "static_link", "column": "link_type", "value": "主干道"},
    {"kw": "辅路", "table": "static_link", "column": "link_type", "value": "辅路"},
    {"kw": "入口匝道", "table": "static_link", "column": "link_type", "value": "入口匝道"},
    {"kw": "出口匝道", "table": "static_link", "column": "link_type", "value": "出口匝道"},
    {"kw": "上匝道", "table": "static_link", "column": "link_type", "value": "入口匝道"},
    {"kw": "下匝道", "table": "static_link", "column": "link_type", "value": "出口匝道"},
    {"kw": "进匝道", "table": "static_link", "column": "link_type", "value": "入口匝道"},
    {"kw": "出匝道", "table": "static_link", "column": "link_type", "value": "出口匝道"},
    {"kw": "匝道", "table": "static_link", "column": "link_type", "value": "匝道"},
    {"kw": "高速公路", "table": "static_link", "column": "link_class", "value": "高速公路"},
    {"kw": "国道", "table": "static_link", "column": "link_class", "value": "国道"},
    {"kw": "省道", "table": "static_link", "column": "link_class", "value": "省道"},
    {"kw": "隧道", "table": "static_link", "column": "link_attribute", "value": "隧道"},
    {"kw": "桥梁", "table": "static_link", "column": "link_attribute", "value": "桥梁"},
    {"kw": "收费站", "table": "static_link", "column": "link_attribute", "value": "收费站"},
    # static_lane.lane_type
    {"kw": "应急车道", "table": "static_lane", "column": "lane_type", "value": "应急车道"},
    {"kw": "公交专用道", "table": "static_lane", "column": "lane_type", "value": "公交专用道"},
]


def _load_dictionary(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _extract_chinese_keywords(text: str) -> List[str]:
    """从文本中提取中文关键词（2-4字连续中文片段）。"""
    segments = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
    # 去重，保持顺序
    seen = set()
    result = []
    for s in segments:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def build_tag_index(dictionary: Optional[Dict[str, Any]] = None) -> Dict[str, TagInfo]:
    """从 schema_dictionary.yaml 构建标签索引。

    Returns:
        Dict[tag_name, TagInfo]
    """
    if dictionary is None:
        dictionary = _load_dictionary(_SCHEMA_DICTIONARY_PATH)
    if dictionary is None:
        return {}

    tags_data = dictionary.get("tags", {})
    index: Dict[str, TagInfo] = {}

    for tag_name, info in tags_data.items():
        if not isinstance(info, dict):
            continue
        # 跳过 meta-entries 如 car_end_behavior_tags
        if "description" not in info:
            continue

        description = info.get("description", "")
        sub_tags = info.get("sub_tags", [])
        related_tables = info.get("related_tables", ["range_tag"])
        source = info.get("source", "")
        limitations = info.get("limitations", [])

        # 自动从 description 提取中文关键词
        auto_keywords = _extract_chinese_keywords(description)

        # 合并手工关键词
        manual_kws = _MANUAL_KEYWORD_OVERRIDES.get(tag_name, [])

        # 去重合并
        all_keywords = list(dict.fromkeys(manual_kws + auto_keywords))

        index[tag_name] = TagInfo(
            tag_name=tag_name,
            description=description,
            sub_tags=sub_tags,
            related_tables=related_tables,
            source=source,
            limitations=limitations,
            keywords=all_keywords,
        )

    return index


# ──────────────────────────────────────────────
# 路由逻辑
# ──────────────────────────────────────────────

class TagRouter:
    """关键词路由器：NL query → matched tags + involved tables。"""

    def __init__(self, dictionary: Optional[Dict[str, Any]] = None):
        self.tag_index = build_tag_index(dictionary)
        self._kw_to_tags: Dict[str, List[str]] = {}  # 关键词 → [tag_name, ...]
        self._build_reverse_index()

    def _build_reverse_index(self):
        """构建 关键词 → tag_names 反向索引。"""
        for tag_name, info in self.tag_index.items():
            for kw in info.keywords:
                self._kw_to_tags.setdefault(kw, []).append(tag_name)

    def route(self, query: str) -> RouteResult:
        """路由：从用户 query 中匹配标签和表。

        Returns:
            RouteResult with matched_tags, involved_tables, method
        """
        matched_tag_names: Set[str] = set()

        # Step 1: 标签关键词匹配
        for kw, tag_names in self._kw_to_tags.items():
            if kw in query:
                for tn in tag_names:
                    matched_tag_names.add(tn)

        # Step 2: 表级关键词匹配（不指向特定标签，但决定需要加载哪些表）
        table_hits: Set[str] = set()
        for table_name, keywords in _TABLE_KEYWORDS.items():
            for kw in keywords:
                if kw in query:
                    table_hits.add(table_name)
                    break

        # Step 2.5: map表枚举值关键词匹配
        map_enum_hits: List[Dict[str, str]] = []
        for entry in _MAP_ENUM_KEYWORDS:
            if entry["kw"] in query:
                table_hits.add(entry["table"])
                map_enum_hits.append(entry)

        # Step 3: 从匹配的标签中提取 involved_tables
        involved_tables: Set[str] = set(table_hits)
        for tag_name in matched_tag_names:
            info = self.tag_index.get(tag_name)
            if info:
                for t in info.related_tables:
                    involved_tables.add(t)

        # 如果完全没命中，fallback 到 range_tag（最常用的表）
        if not matched_tag_names and not involved_tables:
            involved_tables = {"range_tag"}
            method = "fallback"
        else:
            method = "keyword"

        # 构建 TagInfo 列表
        matched_tags = [self.tag_index[tn] for tn in sorted(matched_tag_names)
                        if tn in self.tag_index]

        return RouteResult(
            matched_tags=matched_tags,
            involved_tables=involved_tables,
            method=method,
            map_enum_hits=map_enum_hits,
        )


# ──────────────────────────────────────────────
# 格式化输出
# ──────────────────────────────────────────────

def format_schema_card() -> str:
    """生成 Layer 0 Schema Card（始终常驻，~200 token）。"""
    lines = ["## 数据库概览", ""]
    for category, tables in TABLE_CATEGORIES.items():
        cat_label = {
            "horizontal_event": "事件表",
            "vertical_timeseries": "时序表",
            "dynamic_ref": "动态拓扑表",
            "static_ref": "静态参考表",
        }.get(category, category)
        lines.append(f"### {cat_label}")
        lines.append("| 表名 | 描述 |")
        lines.append("|------|------|")
        for table_name, desc in tables.items():
            lines.append(f"| {table_name} | {desc} |")
        lines.append("")
    return "\n".join(lines)


def format_tag_semantics(matched_tags: List[TagInfo]) -> str:
    """生成 Layer 2 标签语义描述（仅命中标签）。
    
    包含：描述 + 关联表 + 查询方式提示。
    """
    if not matched_tags:
        return ""

    lines = ["## 相关标签语义", ""]
    for tag in matched_tags:
        lines.append(f"### {tag.tag_name}")
        lines.append(f"- 描述: {tag.description}")
        if tag.sub_tags:
            lines.append(f"- 子标签(sub_tag, 存储在 param JSON 中): {', '.join(tag.sub_tags)}")
            lines.append(f"- 查询子标签: `json_extract(param, '$.sub_tag')`")
        if tag.related_tables:
            lines.append(f"- 关联表: {', '.join(tag.related_tables)}")
            # 查询方式提示：告诉LLM该标签在哪个表、怎么查
            primary_table = tag.related_tables[0] if tag.related_tables else "range_tag"
            if primary_table == "range_tag":
                lines.append(f"- 查询方式: `SELECT * FROM range_tag WHERE tag_name = '{tag.tag_name}'`")
                if tag.sub_tags:
                    lines.append(f"- 含子标签时: `SELECT * FROM range_tag WHERE tag_name = '{tag.tag_name}' AND json_extract(param, '$.sub_tag') = '<子标签值>'`")
        if tag.limitations:
            lines.append(f"- 注意: {'; '.join(tag.limitations[:2])}")
        lines.append("")

    return "\n".join(lines)


def format_cross_table_join_hint(involved_tables: Set[str], matched_tags: List[TagInfo] = None, map_enum_hits: List[Dict[str, str]] = None) -> str:
    """根据涉及的表和标签，生成跨表 JOIN 提示 + 多标签组合提示 + map表条件提示。"""
    hints = []

    has_range_tag = "range_tag" in involved_tables
    has_ego = "ego" in involved_tables
    has_dynamic_obj = "dynamic_obj" in involved_tables

    # ── map表枚举条件提示 ──
    if map_enum_hits:
        for hit in map_enum_hits:
            hints.append(
                f"用户提到\"{hit['kw']}\"，对应 {hit['table']}.{hit['column']} = '{hit['value']}'"
            )
        # 给出range_tag × map表的JOIN示例
        # 注意：range_tag 没有 ego_link_id 列！桥接方式是通过 ego 表中转：
        # range_tag(start_ts/end_ts) ←时间JOIN→ ego(ts, ego_static_map_link_id) ←CAST→ static_link(link_id)
        # ⚠ ego.ego_link_id 是另一种编码（小整数），不是 static_link.link_id！
        #   JOIN static_link 必须用 ego.ego_static_map_link_id（大整数，需CAST为TEXT匹配link_id）
        #   且 ego_static_map_link_id = -1 表示无匹配link，需过滤
        if has_range_tag and has_ego and any(h["table"] in ("static_link", "static_lane") for h in map_enum_hits):
            sample = map_enum_hits[0]
            hints.append(
                f"range_tag → ego → {sample['table']} 三表桥接: "
                f"`SELECT r.* FROM range_tag r "
                f"JOIN ego e ON e.ts BETWEEN r.start_ts AND r.end_ts "
                f"JOIN {sample['table']} m ON CAST(e.ego_static_map_link_id AS TEXT) = m.link_id "
                f"WHERE e.ego_static_map_link_id != -1 AND m.{sample['column']} = '{sample['value']}'`"
            )
        elif has_range_tag and not has_ego and any(h["table"] in ("static_link", "static_lane") for h in map_enum_hits):
            # 仅range_tag + map，无ego → 需要提醒LLM必须引入ego做中转
            sample = map_enum_hits[0]
            hints.append(
                f"⚠ range_tag 与 {sample['table']} 不能直接JOIN！range_tag 没有 ego_link_id。"
                f"必须通过 ego 表中转: range_tag(时间) → ego(ego_static_map_link_id) → {sample['table']}(link_id)"
            )

    # ── 多标签组合提示（range_tag 自 JOIN） ──
    if matched_tags and len(matched_tags) >= 2:
        # 只取 primary 相关的标签（排除 fallback 命中的无关标签如 navi_other）
        range_tag_names = [t for t in matched_tags
                          if "range_tag" in t.related_tables
                          and t.tag_name not in ("navi_other",)]
        if len(range_tag_names) >= 2:
            tag_names = [t.tag_name for t in range_tag_names]
            aliases = [f"r{i+1}" for i in range(len(tag_names))]
            # 生成示例SQL
            join_clauses = []
            for i in range(1, len(tag_names)):
                join_clauses.append(
                    f"JOIN range_tag {aliases[i]} ON {aliases[0]}.start_ts < {aliases[i]}.end_ts "
                    f"AND {aliases[0]}.end_ts > {aliases[i]}.start_ts"
                )
            where_clauses = [
                f"{aliases[i]}.tag_name = '{tag_names[i]}'" for i in range(len(tag_names))
            ]
            example_sql = (
                f"SELECT DISTINCT {aliases[0]}.start_ts, {aliases[0]}.end_ts "
                f"FROM range_tag {aliases[0]} "
                + " ".join(join_clauses)
                + " WHERE " + " AND ".join(where_clauses)
            )
            hints.append(
                f"多标签时间交叉查询：用户需要同时满足 {len(tag_names)} 个标签，"
                f"使用 range_tag 自 JOIN（时间窗口交叉）: "
                f"r1.start_ts < r2.end_ts AND r1.end_ts > r2.start_ts"
            )
            hints.append(f"示例: `{example_sql}`")

    # ── range_tag × 时序表 JOIN ──
    if has_range_tag and (has_ego or has_dynamic_obj):
        target = "ego" if has_ego else "dynamic_obj"
        hints.append(
            f"跨表 JOIN 条件: range_tag.start_ts/end_ts 与 {target}.ts 都是秒级时间戳，"
            f"直接比较即可：`{target}.ts BETWEEN range_tag.start_ts AND range_tag.end_ts`"
        )

    if has_ego and has_dynamic_obj:
        hints.append(
            "ego 和 dynamic_obj 的时间对齐: "
            "直接比较 ts（秒级时间戳），或使用 ts_ms（毫秒）对齐"
        )

    if not hints:
        return ""

    return "## 跨表 JOIN 规则\n\n" + "\n".join(f"- {h}" for h in hints) + "\n"


# ──────────────────────────────────────────────
# 模板检索（P0: 关键词匹配，P2: 升级为 RAG）
# ──────────────────────────────────────────────

_TEMPLATES_JSONL_PATH = _SCHEMA_DIR / "templates.jsonl"


def _load_templates_from_jsonl() -> List[Dict[str, str]]:
    """从 templates.jsonl 加载模板列表。"""
    if not _TEMPLATES_JSONL_PATH.exists():
        return []
    templates = []
    with _TEMPLATES_JSONL_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            import json
            try:
                t = json.loads(line)
                if "id" in t and "domain" in t and "nl" in t and "sql" in t:
                    templates.append(t)
            except json.JSONDecodeError:
                continue
    return templates


# 启动时一次性加载，避免每次请求读文件
BUILTIN_TEMPLATES = _load_templates_from_jsonl()


def retrieve_templates(query: str, involved_tables: Set[str], top_k: int = 5) -> str:
    """检索 few-shot 模板（P0: 关键词匹配 + 表名/Domain匹配 + CTE偏好）。"""
    scored = []
    for tmpl in BUILTIN_TEMPLATES:
        score = 0
        # NL 关键词重叠（用2-4字中文片段匹配，比逐字好）
        for seg in re.findall(r'[\u4e00-\u9fff]{2,4}', query):
            if seg in tmpl["nl"]:
                score += 2
        # 表名匹配
        for t in involved_tables:
            if t in tmpl["sql"].lower():
                score += 1
        # Domain 匹配
        for t in involved_tables:
            domain = TABLE_DOMAIN_MAP.get(t, "")
            if domain and domain == tmpl.get("domain", ""):
                score += 1.5
        # CTE偏好：如果query涉及复杂分析（多表、博弈、趋势），优先返回CTE模板
        if "CTE" in tmpl.get("nl", ""):
            complex_keywords = ["博弈", "趋势", "变化", "延迟", "反应", "分类",
                                "严重程度", "最低", "最高", "CTE", "逐帧", "前车"]
            if any(kw in query for kw in complex_keywords):
                score += 2
        if score > 0:
            scored.append((tmpl, score))

    scored.sort(key=lambda x: -x[1])
    top = scored[:top_k]

    if not top:
        # 没有匹配模板，返回一个通用示例
        return (
            "## 参考示例\n\n"
            "```sql\n"
            "-- 示例：查某种标签的片段\n"
            "SELECT start_ts, end_ts, tag_name FROM range_tag "
            "WHERE tag_name = '<标签名>' ORDER BY start_ts;\n"
            "```\n"
        )

    lines = ["## 参考示例", ""]
    for tmpl, _ in top:
        lines.append(f"**Q: {tmpl['nl']}**")
        lines.append(f"```sql\n{tmpl['sql']}\n```")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# 完整 Prompt 组装
# ──────────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """你是一个 ROS Bag 数据查询助手。根据用户的问题生成 SQLite SQL。

### 数据库概览
{schema_card}

### 当前涉及的表结构
{schema_detail}

{tag_semantics}

{join_hints}

### 重要规则
1. 只使用上方 Schema 中存在的表和字段
2. range_tag.start_ts/end_ts 与 ego.ts/dynamic_obj.ts 都是秒级时间戳(BIGINT)，直接比较即可
3. 跨表 JOIN 时: `ego.ts BETWEEN range_tag.start_ts AND range_tag.end_ts`
4. range_tag.param 是 JSON 字符串，提取子标签用: `json_extract(param, '$.sub_tag')`
5. 输出必须是纯 SQL，不要包含 markdown 代码块标记，不要包含任何解释文字
6. SQL 必须完整，必须包含 SELECT、FROM，必要时包含 WHERE
7. 不要生成 LIMIT 子句，LIMIT 由系统自动注入
8. 涉及 dynamic_obj 时，X 轴向前为正，Y 轴向左为正
9. ⚠ range_tag 表只有 start_ts, end_ts, tag_name, param 四个列，没有 ego_link_id！range_tag 与 map 表(static_link/static_lane)的 JOIN 必须通过 ego 表中转: range_tag(时间) → ego(ego_static_map_link_id) → map表(link_id)。⚠ ego.ego_link_id 是另一种编码（小整数1,2,3...），不是 static_link.link_id！JOIN static_link 必须用 ego.ego_static_map_link_id，且 ego_static_map_link_id = -1 表示无匹配link需过滤。ego_static_map_link_id 是int，static_link.link_id 是text，需要 CAST(e.ego_static_map_link_id AS TEXT) = static_link.link_id
10. ⚠ ego.speed 单位是 m/s（米/秒），不是 km/h！例如 speed > 5 表示超过 5m/s（约18km/h）
11. ⚠ SQLite 兼容性约束：不要使用 GREATEST()/LEAST()（用 MAX/MIN 子查询或 CASE WHEN 替代）；不要使用 ->> 运算符（用 json_extract() 替代）；不支持 FULL OUTER JOIN；不支持窗口函数 FILTER 子句
"""

USER_PROMPT_TEMPLATE = """{few_shot}

用户问题：{question}

请生成 SQLite SQL（只输出纯 SQL，不要解释，不要带 LIMIT）："""


def build_prompt(
    question: str,
    schema_text: str,
    route: RouteResult,
) -> tuple[str, str]:
    """组装完整的 system_prompt 和 user_prompt。

    Args:
        question: 用户自然语言问题
        schema_text: 当前涉及表的 Schema 文本（由 schema_reader.format_schema_for_prompt 生成）
        route: 路由结果

    Returns:
        (system_prompt, user_prompt)
    """
    tag_semantics = format_tag_semantics(route.matched_tags)
    join_hints = format_cross_table_join_hint(route.involved_tables, route.matched_tags, route.map_enum_hits)
    few_shot = retrieve_templates(question, route.involved_tables)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        schema_card=format_schema_card(),
        schema_detail=schema_text,
        tag_semantics=tag_semantics if tag_semantics else "",
        join_hints=join_hints if join_hints else "",
    )

    user_prompt = USER_PROMPT_TEMPLATE.format(
        few_shot=few_shot,
        question=question,
    )

    return system_prompt, user_prompt


# ──────────────────────────────────────────────
# CLI 测试
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    router = TagRouter()

    if len(sys.argv) > 1:
        queries = sys.argv[1:]
    else:
        queries = [
            "找出变道的片段",
            "变道时前方有什么目标",
            "急刹车时自车速度",
            "找出闯红灯的片段",
            "检测到行人的时刻",
            "速度超过120的时刻",
        ]

    for q in queries:
        result = router.route(q)
        print(f"\n{'='*60}")
        print(f"Query: {q}")
        print(f"Method: {result.method}")
        print(f"Matched tags: {[t.tag_name for t in result.matched_tags]}")
        print(f"Involved tables: {sorted(result.involved_tables)}")
