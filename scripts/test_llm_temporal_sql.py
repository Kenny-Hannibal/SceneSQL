#!/usr/bin/env python3
"""
LLM 时序SQL能力基线测试
测试目标：在LLM已知标签名+描述的情况下，能否正确识别标签间的时序关系并生成正确SQL

测试设计：
1. 给LLM提供 range_tag 表结构 + 标签列表+描述
2. 不给任何SQL few-shot示例（测试纯推理能力）
3. 给出不同类型的时序需求
4. 评估LLM生成的SQL中时序关系是否正确

时序关系类型：
T1: 同时存在（两标签时间窗口重叠）
T2: A在B之前发生（顺序）
T3: A在B之前N秒内发生（顺序+间隙）
T4: A在B期间发生（包含）
T5: 多标签链式时序（A→B→C）
"""

import json
import os
import sys

# ============== 测试用例定义 ==============

# range_tag 表结构说明（提供给LLM）
RANGE_TAG_SCHEMA = """
表: range_tag
列:
  bag_id TEXT -- 数据包ID
  tag_name TEXT -- 标签名称
  start_ts BIGINT -- 标签生效开始时间（秒级Unix时间戳）
  end_ts BIGINT -- 标签生效结束时间（秒级Unix时间戳）
  param TEXT -- 标签参数（JSON字符串）

注意: 同一个bag_id下可以有多个不同tag_name的记录，每个记录代表一个事件片段。
      同一个tag_name在同一个bag_id下也可能有多条记录（多个事件片段）。
      start_ts和end_ts是秒级Unix时间戳，量级约1.78e9。
"""

# 可用标签列表（提供给LLM）
TAG_LIST = """
可用标签（tag_name值）:
- Intersection: 路口（泛指）
- INTERSECTION_LEFTTURN: 路口左转
- INTERSECTION_RIGHTTURN: 路口右转
- INTERSECTION_STRAIGHT: 路口直行
- TrafficIntersection: 有红绿灯的路口
- LaneChange: 变道
- UTURN: 掉头
- GreenLightNotProceeding: 绿灯未起步
- RunRedLight: 闯红灯
- HardBrake: 急刹车
- Cutin: 他车切入
- CloseFollow: 紧跟
- Pedestrian_Crossing: 行人横穿
- OffRamp: 下匝道
- OnRamp: 上匝道
- Reversing: 倒车
"""

# 5个测试用例
TEST_CASES = [
    {
        "id": "T1",
        "type": "同时存在",
        "nl": "找出路口且有红绿灯的场景",
        "expected_pattern": "时间窗口重叠: r1.start_ts < r2.end_ts AND r1.end_ts > r2.start_ts",
        "expected_tags": ["Intersection类", "TrafficIntersection"],
    },
    {
        "id": "T2",
        "type": "A在B之前发生",
        "nl": "自车在掉头路口发生掉头，并且在掉头前，自车发生了变道行为",
        "expected_pattern": "顺序: lc.end_ts < uturn.start_ts",
        "expected_tags": ["UTURN", "LaneChange"],
    },
    {
        "id": "T3",
        "type": "A在B之前N秒内",
        "nl": "自车在路口左转前5秒内发生了变道",
        "expected_pattern": "顺序+间隙: lc.end_ts < left.start_ts AND (left.start_ts - lc.end_ts) <= 5.0",
        "expected_tags": ["INTERSECTION_LEFTTURN", "LaneChange"],
    },
    {
        "id": "T4",
        "type": "A在B期间发生",
        "nl": "变道过程中发生了急刹车",
        "expected_pattern": "包含: hb.start_ts >= lc.start_ts AND hb.end_ts <= lc.end_ts",
        "expected_tags": ["LaneChange", "HardBrake"],
    },
    {
        "id": "T5",
        "type": "多标签链式时序",
        "nl": "自车先变道，然后进入路口左转，左转过程中有行人横穿",
        "expected_pattern": "链式: lc→left→ped, 顺序+包含",
        "expected_tags": ["LaneChange", "INTERSECTION_LEFTTURN", "Pedestrian_Crossing"],
    },
]

# 统一的系统提示
SYSTEM_PROMPT = """你是一个自动驾驶场景挖掘SQL专家。用户会描述一个场景需求，你需要根据需求生成SQLite SQL查询。

核心规则：
1. 所有查询基于 range_tag 表
2. start_ts 和 end_ts 是秒级Unix时间戳，直接比较，不要做任何单位转换
3. 不同标签在同一个bag_id下的时间关系通过 start_ts/end_ts 比较来表达
4. 结果必须包含 bag_id, start_ts, end_ts 列
5. 只输出纯SQL，不要解释"""

USER_PROMPT_TEMPLATE = """## 数据库结构
{schema}

## 可用标签
{tags}

## 用户需求
{nl}

请生成SQLite SQL查询。注意仔细分析需求中各标签之间的时间关系（同时、先后、包含、间隙等）。"""

if __name__ == "__main__":
    # 输出测试用例信息
    print("=" * 60)
    print("LLM 时序SQL能力基线测试")
    print("=" * 60)
    for tc in TEST_CASES:
        print(f"\n[{tc['id']}] 类型: {tc['type']}")
        print(f"  NL: {tc['nl']}")
        print(f"  预期模式: {tc['expected_pattern']}")
        print(f"  预期标签: {tc['expected_tags']}")

    # 生成每个测试用例的完整prompt（供后续API调用使用）
    prompts = []
    for tc in TEST_CASES:
        user_prompt = USER_PROMPT_TEMPLATE.format(
            schema=RANGE_TAG_SCHEMA,
            tags=TAG_LIST,
            nl=tc["nl"],
        )
        prompts.append({
            "test_id": tc["id"],
            "type": tc["type"],
            "nl": tc["nl"],
            "expected_pattern": tc["expected_pattern"],
            "system_prompt": SYSTEM_PROMPT,
            "user_prompt": user_prompt,
        })

    # 保存prompt到文件（供手动或API调用）
    output_path = "/data/var/workspace/projects/projects/SceneSQL/test_reports/llm-temporal-sql-baseline-prompts.json"
    with open(output_path, "w") as f:
        json.dump(prompts, f, ensure_ascii=False, indent=2)
    print(f"\nPrompts已保存到: {output_path}")
    print(f"共 {len(prompts)} 个测试用例")
