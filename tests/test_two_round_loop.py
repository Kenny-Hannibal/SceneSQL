#!/usr/bin/env python3
"""
两轮NL2SQL引擎测试 — 方案B验证
Round 1: NL → 概念识别 (轻量LLM)
Round 2: 概念详情 + 组合规则 → SQL生成 (精准LLM)
"""

import json
import os
import sys
import time
import yaml
from pathlib import Path
from typing import Any

# ─── 配置 ───
CORE_DIR = Path(__file__).parent.parent / "agent" / "backend" / "app" / "core"
CONCEPT_GROUPS_PATH = CORE_DIR / "concept_groups.yaml"
SCHEMA_DICT_PATH = CORE_DIR / "schema_dictionary.yaml"
SCHEMA_STRUCT_PATH = CORE_DIR / "schema_structure.yaml"

# LLM配置 — 优先从环境变量读取
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL_R1 = os.getenv("LLM_MODEL_R1", "deepseek-chat")      # Round 1用便宜模型
LLM_MODEL_R2 = os.getenv("LLM_MODEL_R2", "deepseek-chat")      # Round 2可以用更强的模型
LLM_MAX_RETRIES = 2

# ─── 数据加载 ───
def load_yaml(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)

def load_concept_groups() -> dict:
    data = load_yaml(CONCEPT_GROUPS_PATH)
    return data.get("concept_groups", {})

def load_schema_dictionary() -> dict:
    data = load_yaml(SCHEMA_DICT_PATH)
    return data.get("tags", {})

def load_schema_structure() -> dict:
    return load_yaml(SCHEMA_STRUCT_PATH)

# ─── LLM调用 ───
def call_llm(messages: list[dict], model: str = None, temperature: float = 0.1,
             max_tokens: int = 2048, response_format: dict = None) -> str:
    """调用OpenAI兼容API"""
    try:
        from openai import OpenAI
    except ImportError:
        print("[ERROR] openai package not installed. Run: pip install openai")
        sys.exit(1)

    client = OpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)
    kwargs = {
        "model": model or LLM_MODEL_R1,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt < LLM_MAX_RETRIES:
                print(f"  [RETRY {attempt+1}] LLM调用失败: {e}")
                time.sleep(2 ** attempt)
            else:
                raise

# ─── Round 1: 概念识别 ───
def build_round1_prompt(nl: str, concept_groups: dict) -> list[dict]:
    """构建Round 1的prompt — NL + 概念列表 → JSON"""

    # 生成概念表格（轻量，不含tag详情）
    concept_rows = []
    for name, info in concept_groups.items():
        variants = "、".join(info.get("nl_variants", []))
        concept_rows.append(f"| {name} | {variants} | {info.get('query_table', 'range_tag')} |")

    concept_table = "\n".join(concept_rows)

    system = f"""你是自动驾驶场景查询的概念识别器。

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

## 输出格式（严格JSON，不要输出其他内容）
{{
  "concepts": ["概念1", "概念2"],
  "composition": "组合方式",
  "ego_fields": ["需要的ego字段名，如speed/steering_angle等，无则为空"],
  "need_dynamic_obj": false,
  "dynamic_obj_filters": "对dynamic_obj的过滤描述，如'前方非静止目标/对向/行人'，无则为空",
  "need_dynamic_lane": false,
  "need_intersection_info": false,
  "analysis_description": "如果组合方式是cte_analysis，描述分析逻辑，否则为空"
}}"""

    user = f"用户问题：{nl}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def run_round1(nl: str, concept_groups: dict) -> dict:
    """Round 1: NL → 概念识别"""
    messages = build_round1_prompt(nl, concept_groups)
    raw = call_llm(messages, model=LLM_MODEL_R1, temperature=0.0, max_tokens=512,
                   response_format={"type": "json_object"})

    # 解析JSON
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # 尝试从文本中提取JSON
        import re
        m = re.search(r'\{[\s\S]*\}', raw)
        if m:
            result = json.loads(m.group())
        else:
            raise ValueError(f"Round 1 输出无法解析为JSON: {raw}")

    return result


# ─── 代码层: 上下文组装 ───
def expand_tag_names(concept_name: str, concept_groups: dict) -> tuple[list[str], list[str]]:
    """展开概念对应的tag_names和tag_patterns"""
    info = concept_groups.get(concept_name, {})
    return info.get("tag_names", []), info.get("tag_patterns", [])


def get_tag_details(tag_name: str, schema_dict: dict) -> dict:
    """从字典获取tag的详细信息"""
    info = schema_dict.get(tag_name, {})
    if not info:
        return {"tag_name": tag_name, "description": "（字典中无详细信息）", "sub_tags": [], "limitations": []}
    return {
        "tag_name": tag_name,
        "description": info.get("description", ""),
        "sub_tags": info.get("sub_tags", []),
        "limitations": info.get("limitations", []),
        "related_tables": info.get("related_tables", []),
    }


def assemble_round2_context(round1_result: dict, concept_groups: dict,
                            schema_dict: dict, schema_struct: dict) -> str:
    """根据Round 1结果组装Round 2的prompt上下文"""

    concepts = round1_result.get("concepts", [])
    composition = round1_result.get("composition", "single_tag")
    ego_fields = round1_result.get("ego_fields", [])
    need_dynamic_obj = round1_result.get("need_dynamic_obj", False)
    dynamic_obj_filters = round1_result.get("dynamic_obj_filters", "")
    need_dynamic_lane = round1_result.get("need_dynamic_lane", False)
    need_intersection_info = round1_result.get("need_intersection_info", False)
    analysis_desc = round1_result.get("analysis_description", "")

    parts = []

    # ── 概念详情 ──
    parts.append("## 命中概念详情\n")
    for c in concepts:
        c_info = concept_groups.get(c, {})
        tag_names, tag_patterns = expand_tag_names(c, concept_groups)
        query_table = c_info.get("query_table", "range_tag")

        parts.append(f"### 概念: {c}")
        parts.append(f"查询表: {query_table}")

        if tag_names:
            parts.append(f"tag_name值: {', '.join(tag_names)}")
        if tag_patterns:
            parts.append(f"tag_name模式(LIKE): {', '.join(tag_patterns)}")
        # tag_where_extra
        extra_where = c_info.get("tag_where_extra", "")
        if extra_where:
            parts.append(f"额外WHERE条件: {extra_where}")

        # 每个tag_name的字典详情
        parts.append("")
        for tn in tag_names:
            detail = get_tag_details(tn, schema_dict)
            parts.append(f"  - {tn}: {detail['description']}")
            if detail.get("sub_tags"):
                parts.append(f"    子标签: {', '.join(detail['sub_tags'])}")
            if detail.get("limitations"):
                parts.append(f"    局限性: {'; '.join(detail['limitations'])}")

        parts.append("")

    # ── 组合规则 ──
    # 加载组合规则模板
    cg_data = load_yaml(CONCEPT_GROUPS_PATH)
    rules = cg_data.get("composition_rules", {})

    parts.append("## 组合规则\n")
    rule = rules.get(composition, {})
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

    # ── 表结构(按需) ──
    parts.append("## 相关表结构\n")
    tables = schema_struct.get("database_schema", {}).get("tables", [])
    # 只输出当前查询需要的表
    needed_table_names = {"range_tag"}  # 总是包含range_tag
    if ego_fields or composition in ("tag_join_ego", "cross_table", "ego_only"):
        needed_table_names.add("ego")
    if need_dynamic_obj or composition in ("tag_join_dynamic_obj", "cross_table"):
        needed_table_names.add("dynamic_obj")
    if need_dynamic_lane or composition == "tag_join_dynamic_lane":
        needed_table_names.add("dynamic_lane")
    if need_intersection_info or composition == "tag_join_intersection_info":
        needed_table_names.add("intersection_info")

    for t in tables:
        if t["name"] in needed_table_names:
            cols = [f"  {c['name']} {c['type']}" for c in t.get("columns", [])]
            parts.append(f"### {t['name']} ({t.get('type', '')})")
            parts.append("\n".join(cols))
            if t.get("enum"):
                parts.append(f"\ntag_name枚举值: {', '.join(t['enum'][:30])}...")
            if t.get("notes"):
                parts.append(f"\n备注: {'; '.join(t['notes'])}")
            parts.append("")

    # ── 关键SQL模式提醒 ──
    parts.append("""## 关键规则提醒
1. range_tag.start_ts/end_ts 单位是秒(×1e9转纳秒)，ego/dynamic_obj.ts单位是纳秒
2. 时间对齐JOIN: `e.ts BETWEEN r.start_ts * 1e9 AND r.end_ts * 1e9`
3. range_tag自连接(两个概念): `r1.start_ts <= r2.end_ts AND r1.end_ts >= r2.start_ts`
4. tag_name LIKE 'INTERSECTION_%' 可匹配所有INTERSECTION_开头的标签
5. param列是JSON字符串，用 json_extract(param, '$.key') 提取子字段
6. 只输出纯SQL，不要解释，不要markdown代码块标记
""")

    return "\n".join(parts)


# ─── Round 2: SQL生成 ───
def build_round2_prompt(nl: str, context: str) -> list[dict]:
    system = f"""你是自动驾驶场景挖掘的SQL生成专家。

根据以下概念详情和组合规则，为用户问题生成精确的SQLite SQL。

{context}"""

    user = f"用户问题：{nl}\n\n请生成SQL（只输出纯SQL，不要解释）："
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def run_round2(nl: str, round1_result: dict, concept_groups: dict,
               schema_dict: dict, schema_struct: dict) -> str:
    """Round 2: 概念详情 + 组合规则 → SQL"""
    context = assemble_round2_context(round1_result, concept_groups, schema_dict, schema_struct)
    messages = build_round2_prompt(nl, context)
    sql = call_llm(messages, model=LLM_MODEL_R2, temperature=0.0, max_tokens=1024)
    # 清理SQL：LLM可能输出思考过程+SQL，提取最后一个SELECT/WITH开头的完整SQL
    sql = _extract_sql(sql)
    return sql


def _extract_sql(raw: str) -> str:
    """从LLM输出中提取纯SQL。策略：找到最后一个SELECT/WITH开头的行，拼接后续所有行"""
    raw = raw.strip()
    # 先去掉markdown代码块
    for prefix in ("```sql", "```"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    lines = raw.split('\n')
    sql_lines = []
    found_sql = False
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith(('SELECT', 'WITH')):
            found_sql = True
            sql_lines = [line]
        elif found_sql:
            sql_lines.append(line)

    if sql_lines:
        return '\n'.join(sql_lines).strip()
    # fallback: 原样返回（可能SQL本身就是单行）
    return raw


# ─── SQL验证 ───
def validate_sql(sql: str, round1_result: dict, concept_groups: dict) -> dict:
    """验证生成的SQL是否正确引用了概念和表"""
    issues = []

    concepts = round1_result.get("concepts", [])
    composition = round1_result.get("composition", "")

    # 检查1: 每个概念对应的tag_name是否出现在SQL中
    for c in concepts:
        tag_names, tag_patterns = expand_tag_names(c, concept_groups)
        found = False
        for tn in tag_names:
            if tn in sql:
                found = True
                break
        for tp in tag_patterns:
            # LIKE 'INTERSECTION_%' 形式
            pattern_short = tp.replace("%", "").replace("_", "")
            if pattern_short in sql or tp.replace("%", "") in sql:
                found = True
                break
        if not found and tag_names:
            issues.append(f"概念'{c}'的tag_name({', '.join(tag_names[:3])}...)未出现在SQL中")

    # 检查2: 组合方式对应的SQL模式
    if composition == "multi_tag" and "JOIN" not in sql.upper():
        issues.append("multi_tag组合应有JOIN(range_tag自连接)")
    if composition in ("tag_join_ego", "cross_table") and "ego" not in sql.lower():
        issues.append(f"{composition}组合应包含ego表")
    if composition in ("tag_join_dynamic_obj", "cross_table") and "dynamic_obj" not in sql.lower():
        issues.append(f"{composition}组合应包含dynamic_obj表")

    # 检查3: 基本SQL语法
    sql_upper = sql.upper().strip()
    if not sql_upper.startswith(("SELECT", "WITH")):
        issues.append("SQL应以SELECT或WITH开头")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
    }


# ─── 20个测试场景 ───
TEST_SCENARIOS = [
    # ── 简单: 单标签查询 ──
    {
        "id": 1, "difficulty": "easy",
        "nl": "找出变道的片段",
        "expected_concepts": ["变道"],
        "expected_composition": "single_tag",
        "expected_tag_names": ["LaneChange"],
    },
    {
        "id": 2, "difficulty": "easy",
        "nl": "找出急刹车的片段",
        "expected_concepts": ["急刹"],
        "expected_composition": "single_tag",
        "expected_tag_names": ["Jerk"],
    },
    {
        "id": 3, "difficulty": "easy",
        "nl": "找出路口直行的片段",
        "expected_concepts": ["路口直行"],
        "expected_composition": "single_tag",
        "expected_tag_names": ["INTERSECTION_STRAIGHT"],
    },
    {
        "id": 4, "difficulty": "easy",
        "nl": "找出闯红灯的片段",
        "expected_concepts": ["闯红灯"],
        "expected_composition": "single_tag",
        "expected_tag_names": ["RunRedLight"],
    },
    {
        "id": 5, "difficulty": "easy",
        "nl": "找出跟车太近的片段",
        "expected_concepts": ["跟车太近"],
        "expected_composition": "single_tag",
        "expected_tag_names": ["CloseFollow"],
    },

    # ── 中等: 多标签交叉 / 标签+ego ──
    {
        "id": 6, "difficulty": "medium",
        "nl": "找出路口且为绿灯的场景",
        "expected_concepts": ["路口", "绿灯"],
        "expected_composition": "multi_tag",
        "expected_tag_names": ["Intersection", "STOPANDGO_FIRSTCARSTARTATGREENLIGHT"],
    },
    {
        "id": 7, "difficulty": "medium",
        "nl": "找出路口且有红绿灯的场景",
        "expected_concepts": ["路口", "红绿灯"],
        "expected_composition": "multi_tag",
        "expected_tag_names": ["Intersection", "TrafficLightAbnormal"],
    },
    {
        "id": 8, "difficulty": "medium",
        "nl": "找出同时有切入和急刹的片段",
        "expected_concepts": ["切入", "急刹"],
        "expected_composition": "multi_tag",
        "expected_tag_names": ["Cutin", "Jerk"],
    },
    {
        "id": 9, "difficulty": "medium",
        "nl": "路口左转时自车速度",
        "expected_concepts": ["路口左转"],
        "expected_composition": "tag_join_ego",
        "expected_tag_names": ["INTERSECTION_LEFTTURN"],
        "expected_ego_fields": ["speed"],
    },
    {
        "id": 10, "difficulty": "medium",
        "nl": "闯红灯时自车速度",
        "expected_concepts": ["闯红灯"],
        "expected_composition": "tag_join_ego",
        "expected_tag_names": ["RunRedLight"],
        "expected_ego_fields": ["speed"],
    },

    # ── 较难: 标签+dynamic_obj / cross_table ──
    {
        "id": 11, "difficulty": "hard",
        "nl": "变道时前方有什么目标",
        "expected_concepts": ["变道"],
        "expected_composition": "tag_join_dynamic_obj",
        "expected_tag_names": ["LaneChange"],
        "expected_dynamic_obj": True,
    },
    {
        "id": 12, "difficulty": "hard",
        "nl": "切入时旁车的位置和速度",
        "expected_concepts": ["切入"],
        "expected_composition": "tag_join_dynamic_obj",
        "expected_tag_names": ["Cutin"],
        "expected_dynamic_obj": True,
    },
    {
        "id": 13, "difficulty": "hard",
        "nl": "路口左转时对向来车的位置",
        "expected_concepts": ["路口左转"],
        "expected_composition": "tag_join_dynamic_obj",
        "expected_tag_names": ["INTERSECTION_LEFTTURN"],
        "expected_dynamic_obj": True,
    },
    {
        "id": 14, "difficulty": "hard",
        "nl": "变道时自车速度和前方目标",
        "expected_concepts": ["变道"],
        "expected_composition": "cross_table",
        "expected_tag_names": ["LaneChange"],
        "expected_dynamic_obj": True,
        "expected_ego_fields": ["speed"],
    },
    {
        "id": 15, "difficulty": "hard",
        "nl": "黄灯过线时自车速度和红绿灯状态",
        "expected_concepts": ["黄灯过线"],
        "expected_composition": "tag_join_ego",
        "expected_tag_names": ["CrossStopLineOnYellowLight"],
        "expected_ego_fields": ["speed", "traffic_light_status"],
    },

    # ── 困难: CTE / 聚合 / 车道级 ──
    {
        "id": 16, "difficulty": "very_hard",
        "nl": "找出路口右转时距离最近的行人",
        "expected_concepts": ["路口右转", "行人横穿"],
        "expected_composition": "cte_analysis",
        "expected_tag_names": ["INTERSECTION_RIGHTTURN", "CrossVRUV1"],
    },
    {
        "id": 17, "difficulty": "very_hard",
        "nl": "找出无保护左转轨迹交叉场景",
        "expected_concepts": ["路口左转"],
        "expected_composition": "cte_analysis",
        "expected_tag_names": ["INTERSECTION_LEFTTURN"],
        "expected_dynamic_obj": True,
    },
    {
        "id": 18, "difficulty": "very_hard",
        "nl": "在第几车道进行掉头",
        "expected_concepts": ["掉头"],
        "expected_composition": "tag_join_dynamic_lane",
        "expected_tag_names": ["INTERSECTION_UTURN"],
    },
    {
        "id": 19, "difficulty": "very_hard",
        "nl": "避障时借道避让的车道信息",
        "expected_concepts": ["避障借道"],
        "expected_composition": "tag_join_dynamic_lane",
        "expected_tag_names": ["AvoidanceBorrowLane"],
    },
    {
        "id": 20, "difficulty": "very_hard",
        "nl": "路口左转时自车和障碍物的DR轨迹漂移值",
        "expected_concepts": ["路口左转"],
        "expected_composition": "cte_analysis",
        "expected_tag_names": ["INTERSECTION_LEFTTURN"],
        "expected_dynamic_obj": True,
    },
]


# ─── 主测试循环 ───
def run_test_loop(scenarios: list[dict] = None, stop_on_error: bool = False):
    """运行测试循环"""
    concept_groups = load_concept_groups()
    schema_dict = load_schema_dictionary()
    schema_struct = load_schema_structure()

    scenarios = scenarios or TEST_SCENARIOS
    results = []

    print("=" * 80)
    print("两轮NL2SQL引擎测试 — 方案B")
    print("=" * 80)
    print(f"概念数: {len(concept_groups)}")
    print(f"测试场景: {len(scenarios)}")
    print(f"LLM: {LLM_BASE_URL} | R1={LLM_MODEL_R1} | R2={LLM_MODEL_R2}")
    print("=" * 80)

    for s in scenarios:
        nl = s["nl"]
        sid = s["id"]
        diff = s["difficulty"]
        print(f"\n{'─' * 60}")
        print(f"[#{sid}] [{diff}] {nl}")
        print(f"{'─' * 60}")

        # ── Round 1 ──
        try:
            r1_result = run_round1(nl, concept_groups)
        except Exception as e:
            print(f"  ✗ Round 1 失败: {e}")
            results.append({"id": sid, "nl": nl, "status": "r1_error", "error": str(e)})
            if stop_on_error:
                break
            continue

        print(f"  Round 1 输出: {json.dumps(r1_result, ensure_ascii=False)}")

        # ── 验证 Round 1 ──
        r1_issues = []
        expected_concepts = set(s.get("expected_concepts", []))
        actual_concepts = set(r1_result.get("concepts", []))
        if expected_concepts and not expected_concepts.issubset(actual_concepts):
            missing = expected_concepts - actual_concepts
            r1_issues.append(f"缺失概念: {missing}")
        if expected_concepts and not actual_concepts.issubset(expected_concepts | {"路口", "红绿灯"}):
            # 允许一些冗余概念（如"路口"+"红绿灯"可能同时命中"路口"）
            extra = actual_concepts - expected_concepts
            if extra and extra != {"红绿灯"}:  # 红绿灯是路口的超集，允许
                r1_issues.append(f"多余概念: {extra}")

        expected_comp = s.get("expected_composition", "")
        actual_comp = r1_result.get("composition", "")
        if expected_comp and actual_comp != expected_comp:
            r1_issues.append(f"组合方式不符: 期望={expected_comp}, 实际={actual_comp}")

        if r1_issues:
            print(f"  ⚠ Round 1 问题: {'; '.join(r1_issues)}")
        else:
            print(f"  ✓ Round 1 概念识别正确")

        # ── Round 2 ──
        try:
            sql = run_round2(nl, r1_result, concept_groups, schema_dict, schema_struct)
        except Exception as e:
            print(f"  ✗ Round 2 夓败: {e}")
            results.append({"id": sid, "nl": nl, "status": "r2_error", "error": str(e),
                           "r1_result": r1_result, "r1_issues": r1_issues})
            if stop_on_error:
                break
            continue

        print(f"  SQL:\n    {sql[:200]}{'...' if len(sql) > 200 else ''}")

        # ── 验证 Round 2 ──
        validation = validate_sql(sql, r1_result, concept_groups)
        if validation["valid"]:
            print(f"  ✓ SQL验证通过")
        else:
            print(f"  ✗ SQL问题: {'; '.join(validation['issues'])}")

        results.append({
            "id": sid,
            "nl": nl,
            "difficulty": diff,
            "status": "ok" if (not r1_issues and validation["valid"]) else "issues",
            "r1_result": r1_result,
            "r1_issues": r1_issues,
            "sql": sql,
            "sql_issues": validation["issues"],
            "expected_concepts": list(expected_concepts),
            "expected_composition": expected_comp,
        })

    # ── 汇总 ──
    print("\n" + "=" * 80)
    print("测试汇总")
    print("=" * 80)
    ok_count = sum(1 for r in results if r.get("status") == "ok")
    issue_count = sum(1 for r in results if r.get("status") == "issues")
    error_count = sum(1 for r in results if "error" in r.get("status", ""))

    print(f"通过: {ok_count}/{len(results)}")
    print(f"有问题: {issue_count}")
    print(f"报错: {error_count}")

    if issue_count > 0 or error_count > 0:
        print("\n问题详情:")
        for r in results:
            if r.get("status") != "ok":
                print(f"  [#{r['id']}] {r['nl']}")
                if r.get("r1_issues"):
                    print(f"    R1: {'; '.join(r['r1_issues'])}")
                if r.get("sql_issues"):
                    print(f"    SQL: {'; '.join(r['sql_issues'])}")
                if "error" in r.get("status", ""):
                    print(f"    Error: {r.get('error', '')}")

    # 保存详细结果
    output_path = Path(__file__).parent / "test_two_round_results.json"
    with open(output_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存: {output_path}")

    return results


# ─── 单个场景调试 ───
def debug_scenario(scenario_id: int):
    """调试单个场景，输出完整的两轮prompt和结果"""
    s = TEST_SCENARIOS[scenario_id - 1]
    concept_groups = load_concept_groups()
    schema_dict = load_schema_dictionary()
    schema_struct = load_schema_structure()

    print(f"=== 调试场景 #{scenario_id}: {s['nl']} ===\n")

    # Round 1
    r1_messages = build_round1_prompt(s["nl"], concept_groups)
    print(f"--- Round 1 System Prompt (前500字) ---")
    print(r1_messages[0]["content"][:500] + "...")
    print(f"\n--- Round 1 User Prompt ---")
    print(r1_messages[1]["content"])

    r1_result = run_round1(s["nl"], concept_groups)
    print(f"\n--- Round 1 输出 ---")
    print(json.dumps(r1_result, ensure_ascii=False, indent=2))

    # 上下文组装
    context = assemble_round2_context(r1_result, concept_groups, schema_dict, schema_struct)
    print(f"\n--- Round 2 上下文 (前800字) ---")
    print(context[:800] + "...")

    # Round 2
    sql = run_round2(s["nl"], r1_result, concept_groups, schema_dict, schema_struct)
    print(f"\n--- Round 2 SQL ---")
    print(sql)

    validation = validate_sql(sql, r1_result, concept_groups)
    print(f"\n--- 验证 ---")
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="两轮NL2SQL引擎测试")
    parser.add_argument("--debug", type=int, help="调试单个场景(1-20)")
    parser.add_argument("--stop-on-error", action="store_true", help="遇到错误停止")
    parser.add_argument("--scenarios", type=str, help="指定场景ID，逗号分隔，如1,6,14")
    args = parser.parse_args()

    if not LLM_API_KEY:
        print("[ERROR] 请设置环境变量 LLM_API_KEY")
        print("  export LLM_API_KEY=your-key-here")
        print("  export LLM_BASE_URL=https://api.deepseek.com  # 可选")
        sys.exit(1)

    if args.debug:
        debug_scenario(args.debug)
    else:
        scenarios = TEST_SCENARIOS
        if args.scenarios:
            ids = [int(x) for x in args.scenarios.split(",")]
            scenarios = [s for s in TEST_SCENARIOS if s["id"] in ids]
        run_test_loop(scenarios, stop_on_error=args.stop_on_error)
