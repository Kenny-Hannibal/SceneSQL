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
            # Phase 1: exact match
            for concept in concepts:
                if concept in combined_map:
                    recipe, variant = combined_map[concept]
                    result["recipe"] = recipe
                    result["recipe_variant"] = result.get("recipe_variant") or variant
                    result["sql_source"] = "user_strategy" if concept in self._user_strategy_map else "recipe"
                    break
            # Phase 2: substring match — if concept contains a map key
            if not result.get("recipe"):
                for concept in concepts:
                    for key, (recipe, variant) in combined_map.items():
                        if key in concept or concept in key:
                            result["recipe"] = recipe
                            result["recipe_variant"] = result.get("recipe_variant") or variant
                            result["sql_source"] = "user_strategy" if key in self._user_strategy_map else "recipe"
                            break
                    if result.get("recipe"):
                        break
            # Phase 3: NL原文匹配 — 直接从用户输入中检测关键词
            if not result.get("recipe") and nl:
                for key, (recipe, variant) in combined_map.items():
                    if key in nl:
                        result["recipe"] = recipe
                        result["recipe_variant"] = result.get("recipe_variant") or variant
                        result["sql_source"] = "user_strategy" if key in self._user_strategy_map else "recipe"
                        break
            # Phase 4a: 向量语义搜索（ChromaDB + MiniLM）
            if not result.get("recipe") and nl:
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
            if not result.get("recipe") and nl:
                best_key, best_score = self._fuzzy_match(nl)
                if best_key and best_score >= 0.4:  # LCS 占比阈值
                    recipe, variant = combined_map[best_key]
                    result["recipe"] = recipe
                    result["recipe_variant"] = result.get("recipe_variant") or variant
                    result["sql_source"] = "user_strategy" if best_key in self._user_strategy_map else "recipe"
                    logger.info(f"Phase4 fuzzy match: '{nl}' → '{best_key}' (score={best_score:.3f})")

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
