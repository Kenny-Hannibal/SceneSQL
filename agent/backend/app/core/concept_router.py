#!/usr/bin/env python3
"""概念路由器 — 两轮NL2SQL方案的Round 1核心模块。
负责：NL → 概念识别 → 组合方式判定 → Round 2上下文组装
"""

import json
import logging
import yaml
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CORE_DIR = Path(__file__).parent
CONCEPT_GROUPS_PATH = CORE_DIR / "concept_groups.yaml"
SCHEMA_DICT_PATH = CORE_DIR / "schema_dictionary.yaml"

# ─── 数据加载 ───
def load_concept_groups() -> dict:
    with open(CONCEPT_GROUPS_PATH) as f:
        data = yaml.safe_load(f)
    return data.get("concept_groups", {}), data.get("composition_rules", {})


def load_schema_dict_tags() -> dict:
    with open(SCHEMA_DICT_PATH) as f:
        data = yaml.safe_load(f)
    return data.get("tags", {})


# ─── Round 1 Prompt ───
ROUND1_SYSTEM_TEMPLATE = """你是自动驾驶场景查询的概念识别器。

任务：根据用户的自然语言问题，识别涉及的概念，并判断需要查询哪些额外表和字段。

## 可用概念
| 概念名 | 用户可能的说法 | 查询表 |
|--------|--------------|--------|
{concept_table}

## 额外数据需求说明
- 用户提到"自车速度/加速度/方向盘角/车道偏移"等 → 需要 ego 表字段
- 用户提到"前方目标/旁车/对向来车/行人/障碍物"等 → 需要 dynamic_obj 表
- 用户提到"车道信息/车道类型"等 → 需要 dynamic_lane 表
- 用户提到"路口属性/车道数"等 → 需要 intersection_info 表
- 用户没有提到以上任何额外信息 → extra_*=空

## 组合方式判断规则
- 只涉及1个概念，无额外表 → single_tag
- 涉及2+个概念（都在range_tag） → multi_tag
- 涉及概念 + ego字段 → tag_join_ego
- 涉及概念 + dynamic_obj → tag_join_dynamic_obj
- 涉及概念 + ego + dynamic_obj → cross_table
- 涉及概念 + dynamic_lane → tag_join_dynamic_lane
- 涉及概念 + intersection_info → tag_join_intersection_info
- 不涉及任何概念，只查ego → ego_only
- 涉及CTE/轨迹/聚合分析 → cte_analysis

## 可用Pipeline Recipe（如果问题匹配以下场景，填写recipe字段）
| 场景 | recipe | variant | 识别关键词 |
|------|--------|---------|-----------|
{recipe_table}

## 可用CTE Block（当没有匹配的recipe时，选择需要的block组合）
| Block名 | 功能 | 输入表 | 必需参数 |
|---------|------|--------|---------|
{block_catalog_table}

## 何时使用Block组合（Layer 3）
- 用户问题**没有**匹配任何recipe时，才需要指定required_blocks
- 如果有匹配的recipe，required_blocks留空
- Block组合示例：用户问"高速大曲率道路有障碍物时绕障"→ required_blocks=["continuous_segment", "obstacle_proximity", "steering_change_detect"]

## 输出格式（严格JSON，不要输出其他内容）
{{
  "concepts": ["概念1", "概念2"],
  "composition": "组合方式",
  "ego_fields": ["需要的ego字段名，如speed/steering_angle等，无则为空"],
  "need_dynamic_obj": false,
  "dynamic_obj_filters": "对dynamic_obj的过滤描述，无则为空",
  "need_dynamic_lane": false,
  "need_intersection_info": false,
  "analysis_description": "如果组合方式是cte_analysis，描述分析逻辑，否则为空",
  "recipe": "recipe名称或空字符串(无匹配recipe)",
  "recipe_variant": "variant名称或空字符串",
  "required_blocks": ["block名列表，仅当recipe为空时填写，否则留空"]
}}"""


def _build_recipe_table() -> str:
    """Dynamically build recipe table from recipe YAML files."""
    import yaml as _yaml
    recipe_dir = Path(__file__).parent / "recipes"
    rows = []
    for f in sorted(recipe_dir.glob("*.yaml")):
        try:
            data = _yaml.safe_load(f.read_text())
            if not data:
                continue
            name = f.stem
            desc = data.get("description", "")[:40]
            variants = data.get("variants", {})
            for vname, v in variants.items():
                tag = v.get("tag_name", v.get("output_tag_name", ""))
                kw = v.get("nl_keywords", desc)
                rows.append(f"| {desc or name} | {name} | {vname} | {kw} |")
        except Exception:
            pass
    return "\n".join(rows)

_RECIPE_TABLE_CACHE = None



def _build_block_catalog_table() -> str:
    """Build block catalog table by scanning blocks/ directory.
    
    Automatically reads all block YAML files from the blocks/ subdirectory
    and generates a markdown table for Round1 LLM consumption.
    No manual catalog maintenance needed — always in sync with actual blocks.
    """
    import yaml as _yaml
    blocks_dir = Path(__file__).parent / "blocks"
    if not blocks_dir.exists():
        return "(block catalog not available)"
    
    rows = []
    for bfile in sorted(blocks_dir.glob("*.yaml")):
        if bfile.name == "block_catalog.yaml":
            continue  # skip self if exists
        try:
            data = _yaml.safe_load(bfile.read_text())
        except Exception:
            continue
        if not data or not isinstance(data, dict):
            continue
        
        bname = data.get("name", bfile.stem)
        desc = data.get("description", "") or ""
        # Take first line of description, max 60 chars
        first_line = desc.split("\n")[0].strip()[:60]
        
        # Extract input from description or parameters
        input_tables = data.get("input", "")
        if not input_tables:
            # Infer from sql_template
            sql_tmpl = data.get("sql_template", "")
            if "FROM ego" in sql_tmpl and "FROM range_tag" in sql_tmpl:
                input_tables = "ego, range_tag"
            elif "FROM ego" in sql_tmpl:
                input_tables = "ego"
            elif "FROM range_tag" in sql_tmpl:
                input_tables = "range_tag"
            elif "FROM dynamic_obj" in sql_tmpl:
                input_tables = "dynamic_obj"
            elif "FROM static_obj" in sql_tmpl:
                input_tables = "static_obj"
            else:
                input_tables = "upstream CTE"
        
        # Extract required params
        params = data.get("parameters", {})
        if isinstance(params, dict):
            required = [k for k, v in params.items()
                        if isinstance(v, dict) and v.get("required", False)]
            req_str = ", ".join(required) if required else "(all optional)"
        else:
            req_str = "(see block definition)"
        
        rows.append(f"| {bname} | {first_line} | {input_tables} | {req_str} |")
    
    if not rows:
        return "(block catalog not available)"
    
    header = "| Block | Description | Input | Required Params |\n|-------|-------------|-------|-----------------|\n"
    return header + "\n".join(rows)


_BLOCK_CATALOG_CACHE = None

def build_round1_messages(nl: str, concept_groups: dict) -> list[dict]:
    """构建Round 1的prompt"""
    global _RECIPE_TABLE_CACHE
    rows = []
    for name, info in concept_groups.items():
        variants = "、".join(info.get("nl_variants", []))
        rows.append(f"| {name} | {variants} | {info.get('query_table', 'range_tag')} |")

    concept_table = "\n".join(rows)
    global _BLOCK_CATALOG_CACHE
    if _RECIPE_TABLE_CACHE is None:
        _RECIPE_TABLE_CACHE = _build_recipe_table()
    if _BLOCK_CATALOG_CACHE is None:
        _BLOCK_CATALOG_CACHE = _build_block_catalog_table()
    system = ROUND1_SYSTEM_TEMPLATE.format(concept_table=concept_table, recipe_table=_RECIPE_TABLE_CACHE, block_catalog_table=_BLOCK_CATALOG_CACHE)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"用户问题：{nl}"},
    ]


# ─── 上下文组装 ───
def assemble_round2_context(
    r1_result: dict,
    concept_groups: dict,
    schema_dict_tags: dict,
    composition_rules: dict,
    schema_text: str,
) -> str:
    """根据Round 1结果组装Round 2的prompt上下文"""
    concepts = r1_result.get("concepts", [])
    composition = r1_result.get("composition", "single_tag")
    ego_fields = r1_result.get("ego_fields", [])
    need_dynamic_obj = r1_result.get("need_dynamic_obj", False)
    dynamic_obj_filters = r1_result.get("dynamic_obj_filters", "")
    need_dynamic_lane = r1_result.get("need_dynamic_lane", False)
    need_intersection_info = r1_result.get("need_intersection_info", False)
    analysis_desc = r1_result.get("analysis_description", "")

    parts = []

    # ── 概念详情 ──
    parts.append("## 命中概念详情\n")
    for c in concepts:
        c_info = concept_groups.get(c, {})
        tag_names = c_info.get("tag_names", [])
        tag_patterns = c_info.get("tag_patterns", [])
        query_table = c_info.get("query_table", "range_tag")
        tag_where_extra = c_info.get("tag_where_extra", "")

        parts.append(f"### 概念: {c}")
        parts.append(f"查询表: {query_table}")
        if tag_names:
            parts.append(f"tag_name值: {', '.join(tag_names)}")
        if tag_patterns:
            parts.append(f"tag_name模式(LIKE): {', '.join(tag_patterns)}")
        if tag_where_extra:
            parts.append(f"额外WHERE条件: {tag_where_extra}")

        # 每个tag_name的字典详情
        parts.append("")
        for tn in tag_names:
            detail = schema_dict_tags.get(tn, {})
            desc = detail.get("description", "（字典中无详细信息）") if isinstance(detail, dict) else ""
            sub_tags = detail.get("sub_tags", []) if isinstance(detail, dict) else []
            limitations = detail.get("limitations", []) if isinstance(detail, dict) else []
            parts.append(f"  - {tn}: {desc}")
            if sub_tags:
                parts.append(f"    子标签: {', '.join(sub_tags)}")
            if limitations:
                parts.append(f"    局限性: {'; '.join(limitations)}")
        parts.append("")

    # ── 组合规则 ──
    parts.append("## 组合规则\n")
    rule = composition_rules.get(composition, {})
    if rule:
        parts.append(f"当前组合方式: **{composition}**")
        parts.append(f"说明: {rule.get('description', '')}")
        parts.append(f"SQL骨架:\n```sql\n{rule.get('sql_template', '')}\n```")
    else:
        parts.append(f"当前组合方式: **{composition}**（无预定义骨架，LLM自行生成）")
    parts.append("")

    # ── 额外数据需求 ──
    if ego_fields:
        parts.append(f"## ego表查询字段\n{', '.join(ego_fields)}\n")
    if need_dynamic_obj:
        parts.append(f"## dynamic_obj表查询\n需要查询dynamic_obj表。过滤条件: {dynamic_obj_filters or '非静止目标'}\n")
    if need_dynamic_lane:
        parts.append("## dynamic_lane表查询\n需要查询dynamic_lane表获取车道级信息\n")
    if need_intersection_info:
        parts.append("## intersection_info表查询\n需要查询intersection_info表获取路口属性\n")
    if analysis_desc:
        parts.append(f"## 分析需求\n{analysis_desc}\n")

    # ── 表结构 ──
    parts.append("## 相关表结构\n")
    parts.append(schema_text)
    parts.append("")

    # ── 关键规则 ──
    parts.append("""## 关键规则提醒
1. range_tag.start_ts/end_ts 与 ego.ts/dynamic_obj.ts/dynamic_lane.ts 单位相同（秒级Unix时间戳），直接比较，不要乘1e9
2. 时间对齐JOIN: `e.ts BETWEEN r.start_ts AND r.end_ts`（不加任何转换）
3. range_tag自连接(两个概念): `r1.start_ts < r2.end_ts AND r1.end_ts > r2.start_ts`
4. tag_name LIKE 'INTERSECTION_%' 可匹配所有INTERSECTION_开头的标签
5. param列是JSON字符串，用 json_extract(param, '$.key') 提取子字段
6. ego_dr_trajectory/obs_dr_trajectory是JSON字符串，漂移值用json_extract提取：dx_08=json_extract(ego_dr_trajectory,'$.x[4]')-json_extract(ego_dr_trajectory,'$.x[0]')
7. 只输出纯SQL，不要解释，不要markdown代码块标记
""")

    return "\n".join(parts)


# ─── Round 2 Prompt ───
ROUND2_SYSTEM_TEMPLATE = """你是自动驾驶场景挖掘的SQL生成专家。

根据以下概念详情和组合规则，为用户问题生成精确的SQLite SQL。

{context}"""


def build_round2_messages(nl: str, context: str) -> list[dict]:
    system = ROUND2_SYSTEM_TEMPLATE.format(context=context)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"用户问题：{nl}\n\n请生成SQL（只输出纯SQL，不要解释）："},
    ]




def build_hybrid_round2_messages(nl: str, required_blocks: list, auto_blocks: list,
                                  schema_text: str, assembler) -> list[dict]:
    """构建 Layer 3 Hybrid Round2 prompt — LLM 需要生成胶水 CTE + final SELECT。

    已知 block 会自动拼装，LLM 只需要：
    1. 为 block 设置具体参数（如 where_condition, tag_name 等）
    2. 生成 block 之间/之后的胶水 CTE（如有必要）
    3. 生成最终 SELECT 语句
    """
    # 构建已知 block 信息
    block_details = []
    for bname in required_blocks:
        block = assembler.block_lib.get(bname)
        if block:
            params_info = []
            for pname, pdef in block.get("parameters", {}).items():
                if pdef.get("required", False):
                    params_info.append(f"  - {pname}: (REQUIRED) {pdef.get('description', '')}")
                else:
                    params_info.append(f"  - {pname}: default={pdef.get('default', 'N/A')} — {pdef.get('description', '')}")
            block_details.append(f"### Block: {bname}")
            block_details.append(f"功能: {block.get('description', '')}")
            block_details.append(f"参数:")
            block_details.extend(params_info)
            block_details.append("")

    system = f"""你是自动驾驶场景挖掘的SQL生成专家。

## 任务
用户查询没有完全匹配的recipe，但可以用以下Block组合 + 胶水CTE来拼装SQL。

## 已知Block（会自动拼装，你只需指定参数值）
{chr(10).join(block_details)}

## 你需要做的
1. 为每个Block指定具体参数值（根据用户查询语义）
2. 如果Block之间需要连接CTE（如一个block的输出是另一个的输入），写胶水CTE
3. 写最终SELECT语句

## 输出格式
---BLOCK_PARAMS---
block_name: param1=value1, param2=value2
---CUSTOM_CTE---
<cte_name> AS (
    ... SQL ...
)
---END_CTE---
---FINAL_SELECT---
SELECT ... FROM ...

## 表结构
{schema_text}

## 关键规则
1. range_tag.start_ts/end_ts 与 ego.ts 单位相同（秒级Unix时间戳），直接比较
2. tag_name LIKE 'XXX_%' 可匹配前缀
3. param列是JSON字符串，用 json_extract(param, '$.key') 提取
4. 只输出纯SQL，不要解释

## 严格禁止（违反将导致SQL执行失败）
1. 禁止使用花括号占位符，如 {{variable}}、{{param}} — 所有值必须直接写死
2. 禁止在SQL中使用Python/Jinja语法，只允许标准SQLite语法
3. CTE命名只允许字母、数字、下划线，禁止特殊字符
4. 字符串常量必须用单引号，禁止双引号
5. 最终SQL必须能被SQLite直接执行，不能依赖任何外部变量
6. 如果Block已经自动拼装了CTE，胶水CTE和final SELECT中引用这些CTE名即可
"""

    user = f"用户问题：{nl}\n\n请生成Block参数 + 胶水CTE + 最终SELECT："

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


class ConceptRouter:
    """概念路由器 — 两轮NL2SQL方案的Round 1 + 上下文组装"""

    # 概念名 → (recipe, variant) 自动匹配表
    # 当Round 1 LLM未返回recipe但concept匹配时，代码层自动补上
    CONCEPT_RECIPE_MAP = {
        "变道": ("lane_change_analysis", "default"),
        "左变道": ("lane_change_analysis", "left"),
        "右变道": ("lane_change_analysis", "right"),
        "切入": ("cutin_analysis", "default"),
        "拥堵跟车": ("cutin_analysis", "congested"),
        "跟车太近": ("close_follow_analysis", "default"),
        "拥堵跟车风险": ("close_follow_analysis", "congested"),
        "他车横穿冲突": ("conflict_pipeline", "vehicle"),
        "VRU横穿冲突": ("conflict_pipeline", "vru"),
        "左转冲突": ("turn_conflict_pipeline", "left_turn"),
        "右转冲突": ("turn_conflict_pipeline", "right_turn"),
        # ── 产线SQL直通Recipe (raw_sql) ──
        "绕行超车": ("turn_bypass_overtake", "default"),
        "变道减速": ("ego_decel_during_lanechange", "default"),
        "绿灯刹车": ("greenlight_abnormalbrake", "default"),
        "绿灯异常刹车": ("greenlight_abnormalbrake", "default"),
        "卡车切入": ("truck_safe_cutin_ego", "default"),
        "会车": ("meeting_oncoming", "default"),
        "对向来车": ("meeting_oncoming", "default"),
        "借道": ("nudge_borrowlane", "default"),
        "借道避让": ("nudge_borrowlane", "default"),
        "超车切入卡车": ("ego_overtake_catin_truck", "default"),
        "红灯缓行": ("redlight_slowmoving", "default"),
        "前车急刹": ("front_hard_brake", "default"),
        "避障": ("reversing", "default"),
        "倒车": ("reversing", "default"),
        # ── 产线SQL直通Recipe (raw_sql) — 扩展 ──
        "路口横穿": ("Intersection_Crossing", "default"),
        "路口停车": ("Intersection_Stop", "default"),
        "行人横穿": ("Pedestrian_Crossing", "default"),
        "行人穿越": ("Pedestrian_Crossing", "default"),
        "横穿冲突": ("agent_cross_conflict", "default"),
        "近距离跟车": ("close_following", "default"),
        "分流": ("divergence", "default"),
        "车道分流": ("divergence", "default"),
        "自车切入": ("ego_cut_in", "default"),
        "绿灯通行": ("ego_greenlight_action", "default"),
        "绿灯行为": ("ego_greenlight_action", "default"),
        "无红绿灯": ("ego_no_traffic_light", "default"),
        "无灯路口": ("ego_no_traffic_light", "default"),
        "借道超车卡车": ("ego_nudge_overtake_truck", "default"),
        "红灯行为": ("ego_redlight_action", "default"),
        "闯红灯": ("ego_redlight_action", "default"),
        "小间距切入": ("ego_smallgap_cutin", "default"),
        "速度跟踪": ("ego_speed_track", "default"),
        "未知灯态行为": ("ego_unknownlight_action", "default"),
        "黄灯行为": ("ego_yellowlight_action", "default"),
        "黄灯通过": ("ego_yellowlight_action", "default"),
        "路口直行": ("intersection_straight", "default"),
        "路口直行简单": ("intersection_straight_simple", "default"),
        "左转": ("intersection_turn_left", "default"),
        "右转": ("intersection_turn_right", "default"),
        "急动度": ("jerk_too_high", "default"),
        "急变道jerk": ("jerk_too_high", "default"),
        "车道曲率": ("lane_curvature", "default"),
        "车道结束": ("lane_ending", "default"),
        "前驱车道数": ("lane_predecessor_count", "default"),
        "后继车道数": ("lane_successor_count", "default"),
        "车道宽度": ("lane_width", "default"),
        "道路属性": ("link_attribute_track", "default"),
        "道路等级": ("link_class_track", "default"),
        "道路类型": ("link_type_track", "default"),
        "导航指令": ("navi_command_track", "default"),
        "近红绿灯": ("near_traffic_light", "default"),
        "他车切入": ("obj_cut_in", "default"),
        "倒车避障": ("obstacle_avoidance", "default"),
        "下匝道": ("off_ramp", "default"),
        "驶出匝道": ("off_ramp", "default"),
        "上匝道": ("on_ramp", "default"),
        "驶入匝道": ("on_ramp", "default"),
        "他车切入自车让行": ("other_cutin_ego_yield", "default"),
        "右转专用": ("right_turn_only", "default"),
        "环岛": ("roundabout", "default"),
        "偏航": ("route_deviation", "default"),
        "路线偏离": ("route_deviation", "default"),
        "拓扑约束": ("specify_topology_track", "default"),
        "限速": ("speed_limit_track", "default"),
        "合流分流": ("split_merge_track", "default"),
        "拓扑约束跟踪": ("topology_constraint_track", "default"),
        "红绿灯状态": ("traffic_light_state", "default"),
        "掉头1": ("u_turn_left_1", "default"),
        "掉头2": ("u_turn_left_2", "default"),
        "掉头3": ("u_turn_left_3", "default"),
        "变道掉头": ("u_turn_with_lanechange", "default"),
        "弱势道路使用者横穿": ("vru_cross_conflict", "default"),
        # ── Layer1 扩展：产线SQL直通补全 ──
        "大曲率道路": ("large_curvature_road", "default"),
        "大曲率弯道": ("large_curvature_road", "default"),
        "急弯": ("large_curvature_road", "default"),
        "合流": ("convergence", "default"),
        "汇入主路": ("convergence", "default"),
        "匝道合流": ("convergence", "default"),
        "连续变道": ("continuous_lane_change", "default"),
        "多次变道": ("continuous_lane_change", "default"),
        "Y型路口": ("intersection_y_junction", "default"),
        "Y字路口": ("intersection_y_junction", "default"),
        "直行红绿灯路口": ("straight_intersection_with_trafficlight", "default"),
        "路口停车等灯": ("intersection_with_trafficlight", "default"),
        "红绿灯前停车": ("intersection_with_trafficlight", "default"),
        "路口等红绿灯": ("intersection_with_trafficlight", "default"),
        "binok": ("binok", "default"),
        "导航左转": ("ego_navigation_turn_left", "default"),
        "导航右转": ("ego_navigation_turn_right", "default"),
        "导航掉头": ("ego_navigation_uturn", "default"),
        "掉头不变道": ("turn_back_without_lanechange", "default"),
        "掉头一次变道": ("trun_back_with_1_lanechange", "default"),
        "掉头两次变道": ("turn_back_with_2_lanechange", "default"),
        "掉头变道": ("turn_back_with_lanechange", "default"),
        "绕障": ("obstacle_avoidance", "default"),
        "绕行避障": ("obstacle_avoidance", "default"),
        "避障绕行": ("obstacle_avoidance", "default"),
        "锥桶绕行": ("obstacle_avoidance", "default"),
        "下匝道新版": ("off_ramp_new_use_link_type", "default"),
        "上匝道新版": ("on_ramp_new_use_link_type", "default"),
        # ── Concept→Recipe 补全：覆盖concept_groups中所有有recipe的概念 ──
        "侧方横穿": ("vru_cross_conflict", "default"),
        "偏离车道中心": ("lane_ending", "default"),
        "右转A": ("right_turn_only", "default"),
        "多障碍物绕行": ("obstacle_avoidance", "default"),
        "急刹": ("front_hard_brake", "default"),
        "掉头": ("intersection_u_turn", "default"),
        "红灯": ("ego_redlight_action", "default"),
        "红绿灯": ("traffic_light_state", "default"),
        "绿灯": ("ego_greenlight_action", "default"),
        "蠕行跟车": ("close_follow_analysis", "default"),
        "超速": ("speed_limit_track", "default"),
        "路口": ("intersection_with_trafficlight", "default"),
        "路口右转": ("intersection_turn_right", "default"),
        "路口左转": ("intersection_turn_left", "default"),
        "车道内避障": ("obstacle_avoidance", "default"),
        "车道合并": ("split_merge_track", "default"),
        "转向": ("ego_navigation_turn_left", "default"),
        "近碰": ("conflict_pipeline", "default"),
        "避障借道": ("nudge_borrowlane", "default"),
        "黄灯过线": ("ego_yellowlight_action", "default"),

        # ===== 12个孤儿recipe映射 =====
        "拓扑左转": ("intersection_turn_left_topology", "default"),
        "拓扑右转": ("intersection_turn_right_topology", "default"),
        "左转v2": ("intersection_turn_left_2", "default"),
        "右转v1": ("intersection_turn_right_1", "default"),
        "右转v2": ("intersection_turn_right_2", "default"),
        "右转其他": ("intersection_turn_right_other", "default"),
        "红绿灯路口v2": ("intersection_with_trafficlight_new", "default"),
        "基础变道": ("lane_change", "default"),
        "简单变道": ("lane_change", "default"),
        "左转冲突": ("left_turn_conflict", "default"),
        "右转冲突": ("right_turn_conflict", "default"),
        "卡车切入自车": ("truck_cutin_ego", "default"),

    }

    def __init__(self):
        self.concept_groups, self.composition_rules = load_concept_groups()
        self.schema_dict_tags = load_schema_dict_tags()
        self._recipe_descriptions = self._load_recipe_descriptions()
        # 用户策略映射（优先于系统 CONCEPT_RECIPE_MAP）
        self._user_strategy_map: dict = {}
        self.load_user_strategies()
        # 向量语义索引（懒加载，首次查询时生效）
        self._init_vector_index()

    def load_user_strategies(self):
        """加载用户策略目录，构建 _user_strategy_map。
        用户策略优先于系统 recipe：同一个 keyword，用户策略覆盖系统映射。
        """
        try:
            from .user_strategy import UserStrategyManager
            mgr = UserStrategyManager()
            self._user_strategy_map = mgr.get_concept_recipe_map_entries()
            if self._user_strategy_map:
                logger.info(f"Loaded {len(self._user_strategy_map)} user strategy keywords: "
                            f"{list(self._user_strategy_map.keys())}")
        except Exception as e:
            logger.warning(f"Failed to load user strategies: {e}")
            self._user_strategy_map = {}

    def _init_vector_index(self):
        """初始化向量语义索引（ChromaDB + MiniLM）。
        懒加载：如果依赖未安装则静默跳过，不影响 Phase 1-3 + Phase 4 逻辑。
        """
        try:
            from .vector_router import load_from_templates, is_available
            load_from_templates()
            if is_available():
                logger.info("Vector semantic routing enabled (ChromaDB + MiniLM)")
        except Exception as e:
            logger.debug(f"Vector index init skipped: {e}")

    @staticmethod
    def _load_recipe_descriptions() -> dict:
        """加载所有 Recipe YAML 的 description，供 Phase 4 模糊匹配使用"""
        descs = {}
        recipe_dir = Path(__file__).parent / "recipes"
        if not recipe_dir.exists():
            return descs
        for f in recipe_dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(f.read_text())
                name = data.get("name", f.stem)
                desc = data.get("description", "")
                if desc:
                    descs[name] = desc
            except Exception:
                pass
        return descs

    def get_round1_messages(self, nl: str) -> list[dict]:
        return build_round1_messages(nl, self.concept_groups)

    def parse_round1_output(self, raw: str, nl: str = "") -> dict:
        """解析Round 1的JSON输出，自动补全recipe字段。
        
        nl: 原始自然语言查询，用于当concepts不匹配时做NL原文匹配。
        """
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                result = json.loads(m.group())
            else:
                raise ValueError(f"Round 1 输出无法解析为JSON: {raw[:200]}")

        # ── 代码层 recipe 自动匹配：如果LLM未返回recipe但concept匹配 ──
        # 合并映射：用户策略优先于系统 recipe
        combined_map = {**self.CONCEPT_RECIPE_MAP, **self._user_strategy_map}
        if not result.get("recipe"):
            concepts = result.get("concepts", [])
            # ── Pre-check: 检测concepts是否命中2+个不同recipe → compound场景 ──
            # 先精确匹配，再子串匹配（concept包含map key的情况）
            concept_recipes = []
            for concept in concepts:
                if concept in combined_map:
                    recipe, variant = combined_map[concept]
                    concept_recipes.append((concept, recipe, variant))
                else:
                    # 子串匹配：concept包含map中的key，取最长匹配
                    sub_matches = [(key, *combined_map[key]) for key in combined_map if key in concept]
                    if sub_matches:
                        sub_matches.sort(key=lambda x: len(x[0]), reverse=True)
                        key, recipe, variant = sub_matches[0]
                        concept_recipes.append((f"{concept}→{key}", recipe, variant))
            unique_concept_recipes = set(r for _, r, _ in concept_recipes)
            if len(unique_concept_recipes) >= 2:
                # compound: 多个concept命中不同recipe → hybrid
                result["recipe"] = ""
                result["recipe_variant"] = ""
                result["required_blocks"] = self._infer_required_blocks(
                    [r for _, r, _ in concept_recipes]
                )
                result["sql_source"] = "hybrid"
                result["composition"] = "hybrid_blocks"
                result["_routed"] = True  # 标记路由完成，跳过后续Phase
                logger.info(f"Compound concepts: {concept_recipes} -> blocks={result['required_blocks']}")
            else:
                # Phase 1: exact match (单一场景)
                for concept in concepts:
                    if concept in combined_map:
                        recipe, variant = combined_map[concept]
                        result["recipe"] = recipe
                        result["recipe_variant"] = result.get("recipe_variant") or variant
                        result["sql_source"] = "user_strategy" if concept in self._user_strategy_map else "recipe"
                        result["_routed"] = True
                        break
            # Phase 2: substring match — if concept contains a map key
            if not result.get("recipe") and not result.get("_routed"):
                for concept in concepts:
                    for key, (recipe, variant) in combined_map.items():
                        if key in concept or concept in key:
                            result["recipe"] = recipe
                            result["recipe_variant"] = result.get("recipe_variant") or variant
                            result["sql_source"] = "user_strategy" if key in self._user_strategy_map else "recipe"
                            break
                    if result.get("recipe"):
                        break
            # Phase 3: NL原文匹配 — 复合场景检测 + 最长匹配
            if not result.get("recipe") and not result.get("_routed") and nl:
                matches = [(key, recipe, variant) for key, (recipe, variant) in combined_map.items() if key in nl]
                if matches:
                    # 先过滤：如果一个key是另一个key的子串，且较短key的recipe不同，
                    # 只保留最长key（"变道"被"掉头不变道"包含则过滤掉）
                    filtered = []
                    for i, (k1, r1, v1) in enumerate(matches):
                        is_substring = False
                        for j, (k2, r2, v2) in enumerate(matches):
                            if i != j and k1 in k2 and len(k1) < len(k2):
                                is_substring = True
                                break
                        if not is_substring:
                            filtered.append((k1, r1, v1))
                    
                    unique_recipes = set(r for _, r, _ in filtered)
                    if len(unique_recipes) >= 2:
                        # ── 复合场景：NL匹配到2+个不同recipe → 走hybrid路径 ──
                        result["recipe"] = ""
                        result["recipe_variant"] = ""
                        result["required_blocks"] = self._infer_required_blocks(
                            [r for _, r, _ in filtered]
                        )
                        result["sql_source"] = "hybrid"
                        result["composition"] = "hybrid_blocks"
                        logger.info(f"Phase3 compound: '{nl}' -> recipes={unique_recipes}, blocks={result['required_blocks']}")
                    else:
                        # 单一场景：按key长度降序，选最长的匹配（更具体）
                        filtered.sort(key=lambda x: len(x[0]), reverse=True)
                        key, recipe, variant = filtered[0]
                        result["recipe"] = recipe
                        result["recipe_variant"] = result.get("recipe_variant") or variant
                        result["sql_source"] = "user_strategy" if key in self._user_strategy_map else "recipe"
                        logger.info(f"Phase3 NL match: '{nl}' -> '{key}' (len={len(key)})")
            # Phase 4a: 向量语义搜索（ChromaDB + MiniLM）
            if not result.get("recipe") and not result.get("_routed") and nl:
                try:
                    from .vector_router import search as vector_search, is_available as vector_available
                    if vector_available():
                        hits = vector_search(nl, top_k=1)
                        if hits and hits[0][1] < 0.35:  # cosine distance < 0.35 ≈ 高度相似
                            recipe_name = hits[0][0]
                            # 在 combined_map 中查找 recipe_name 对应的 key
                            matched_key = None
                            for k, (rn, _v) in combined_map.items():
                                if rn == recipe_name:
                                    matched_key = k
                                    break
                            if matched_key:
                                recipe, variant = combined_map[matched_key]
                                result["recipe"] = recipe
                                result["recipe_variant"] = result.get("recipe_variant") or variant
                                result["sql_source"] = "user_strategy" if matched_key in self._user_strategy_map else "recipe_vector"
                                logger.info(f"Phase4a vector match: '{nl}' → '{recipe_name}' (dist={hits[0][1]:.3f})")
                except Exception as e:
                    logger.debug(f"Vector search failed: {e}")
            # Phase 4: n-gram 余弦相似度兜底 — 语义模糊匹配
            if not result.get("recipe") and not result.get("_routed") and nl:
                best_key, best_score = self._fuzzy_match(nl)
                if best_key and best_score >= 0.4:  # LCS 占比阈值
                    recipe, variant = combined_map[best_key]
                    result["recipe"] = recipe
                    result["recipe_variant"] = result.get("recipe_variant") or variant
                    result["sql_source"] = "user_strategy" if best_key in self._user_strategy_map else "recipe"
                    logger.info(f"Phase4 fuzzy match: '{nl}' → '{best_key}' (score={best_score:.3f})")

        # 清理内部flag，不暴露给调用方
        result.pop("_routed", None)
        return result

    # ── Phase 4: n-gram 余弦相似度模糊匹配 ──
    @staticmethod
    def _char_ngrams(text: str, n: int = 2) -> set:
        """生成字符级 n-gram 集合"""
        text = text.strip()
        if len(text) < n:
            return {text} if text else set()
        return {text[i:i+n] for i in range(len(text) - n + 1)}

    @classmethod
    def _cosine_sim(cls, a: set, b: set) -> float:
        """字符级重叠度：|交集|/min(|a|,|b|)，对短-长文本对更友好"""
        if not a or not b:
            return 0.0
        inter = len(a & b)
        return inter / min(len(a), len(b)) if min(len(a), len(b)) else 0.0

    # ── Recipe → Block 映射表 ──
    # 从recipe名反推需要的block（复合场景时使用）
    RECIPE_BLOCK_MAP = {
        "obstacle_avoidance": ["obstacle_proximity", "steering_change_detect"],
        "large_curvature_road": ["continuous_segment"],
        "intersection_y_junction": ["tag_gap_merge"],
        "off_ramp": ["static_link_segment"],
        "on_ramp": ["static_link_segment"],
        "convergence": ["static_link_segment", "ego_field_condition"],
        "intersection_stop": ["continuous_segment", "obstacle_proximity"],
        "right_turn_only": ["static_link_segment", "continuous_segment"],
        "turn_back_with_lanechange": ["continuous_segment"],
        "turn_back_without_lanechange": ["continuous_segment"],
        "continuous_lane_change": ["continuous_segment"],
        "intersection_with_trafficlight": ["tag_gap_merge"],
        "near_traffic_light": ["tag_gap_merge"],
        "nudge_borrowlane": ["steering_change_detect", "continuous_segment"],
        "reversing": ["continuous_segment"],
    }

    def _infer_required_blocks(self, recipe_names: list) -> list:
        """从匹配的recipe名列表反推需要的block（去重、保序）。"""
        blocks = []
        seen = set()
        for rn in recipe_names:
            for b in self.RECIPE_BLOCK_MAP.get(rn, []):
                if b not in seen:
                    blocks.append(b)
                    seen.add(b)
        return blocks

    def _fuzzy_match(self, nl: str) -> tuple:
        """Phase 4 兜底：从 NL 中提取关键词片段，与 CONCEPT_RECIPE_MAP key 做权重匹配。
        策略：对 NL 做滑动窗口提取 2-4 字片段，与每个 key 计算最长公共子串占比。
        """
        # 从 NL 提取 2-4 字的关键片段（跳过停用词）
        stop_chars = {'的', '了', '在', '是', '有', '和', '找', '出', '看', '一', '个', '些', '这', '那'}
        nl_clean = ''.join(c for c in nl if c not in stop_chars)
        
        best_key = None
        best_score = 0.0
        combined_map = {**self.CONCEPT_RECIPE_MAP, **self._user_strategy_map}
        for key, (recipe_name, _variant) in combined_map.items():
            # 用 recipe_name (tag_name) 也做匹配源
            source = key + " " + recipe_name
            score = self._substring_overlap(nl_clean, source)
            if score > best_score:
                best_score = score
                best_key = key
        return best_key, best_score

    @staticmethod
    def _substring_overlap(s1: str, s2: str) -> float:
        """最长公共子串占比：len(LCS) / min(len(s1), len(s2))
        对中文短文本效果远好于 n-gram Jaccard。
        """
        if not s1 or not s2:
            return 0.0
        m, n = len(s1), len(s2)
        # O(m*n) DP，但 m,n 都很短（<20），所以没问题
        max_len = 0
        prev = [0] * (n + 1)
        for i in range(1, m + 1):
            curr = [0] * (n + 1)
            for j in range(1, n + 1):
                if s1[i-1] == s2[j-1]:
                    curr[j] = prev[j-1] + 1
                    if curr[j] > max_len:
                        max_len = curr[j]
            prev = curr
        return max_len / min(m, n)

    def build_round2_context(self, r1_result: dict, schema_text: str) -> str:
        return assemble_round2_context(
            r1_result, self.concept_groups, self.schema_dict_tags,
            self.composition_rules, schema_text,
        )

    def get_round2_messages(self, nl: str, r1_result: dict, schema_text: str) -> list[dict]:
        context = self.build_round2_context(r1_result, schema_text)
        return build_round2_messages(nl, context)


# ── 全局单例 ──
_instance = None

def get_concept_router() -> ConceptRouter:
    global _instance
    if _instance is None:
        _instance = ConceptRouter()
    return _instance
