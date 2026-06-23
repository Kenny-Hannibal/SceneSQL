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
| 他车横穿冲突 | conflict_pipeline | vehicle | 横穿冲突/他车交叉/CrossConflict |
| VRU横穿冲突 | conflict_pipeline | vru | 行人横穿/VRU冲突/VRUCrossConflict |
| 左转冲突 | turn_conflict_pipeline | left_turn | 左转冲突/对向冲突/left turn |
| 右转冲突 | turn_conflict_pipeline | right_turn | 右转冲突/right turn |
| 切入分析 | cutin_analysis | default | 切入/cutin/Cutin |
| 拥堵跟车分析 | cutin_analysis | congested | 拥堵跟车/CongestedFollow |
| 变道分析 | lane_change_analysis | default | 变道/LaneChange/换道/并道 |
| 左变道分析 | lane_change_analysis | left | 左变道/向左变道/LeftLaneChange |
| 右变道分析 | lane_change_analysis | right | 右变道/向右变道/RightLaneChange |
| 跟车过近分析 | close_follow_analysis | default | 跟车过近/CloseFollow/尾随/紧跟 |
| 拥堵跟车(风险)分析 | close_follow_analysis | congested | 拥堵跟车风险/CongestedFollow |

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
  "recipe_variant": "variant名称或空字符串"
}}"""


def build_round1_messages(nl: str, concept_groups: dict) -> list[dict]:
    """构建Round 1的prompt"""
    rows = []
    for name, info in concept_groups.items():
        variants = "、".join(info.get("nl_variants", []))
        rows.append(f"| {name} | {variants} | {info.get('query_table', 'range_tag')} |")

    concept_table = "\n".join(rows)
    system = ROUND1_SYSTEM_TEMPLATE.format(concept_table=concept_table)
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
    }

    def __init__(self):
        self.concept_groups, self.composition_rules = load_concept_groups()
        self.schema_dict_tags = load_schema_dict_tags()

    def get_round1_messages(self, nl: str) -> list[dict]:
        return build_round1_messages(nl, self.concept_groups)

    def parse_round1_output(self, raw: str) -> dict:
        """解析Round 1的JSON输出，自动补全recipe字段"""
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
        if not result.get("recipe"):
            for concept in result.get("concepts", []):
                if concept in self.CONCEPT_RECIPE_MAP:
                    recipe, variant = self.CONCEPT_RECIPE_MAP[concept]
                    result["recipe"] = recipe
                    result["recipe_variant"] = result.get("recipe_variant") or variant
                    break  # 取第一个匹配

        return result

    def build_round2_context(self, r1_result: dict, schema_text: str) -> str:
        return assemble_round2_context(
            r1_result, self.concept_groups, self.schema_dict_tags,
            self.composition_rules, schema_text,
        )

    def get_round2_messages(self, nl: str, r1_result: dict, schema_text: str) -> list[dict]:
        context = self.build_round2_context(r1_result, schema_text)
        return build_round2_messages(nl, context)
