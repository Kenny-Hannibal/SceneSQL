#!/usr/bin/env python3
"""向量路由召回质量评测 — 评测 Phase 4a 的口语化查询召回率。

用法（在 DSW 上，仓库根目录）：
    .venv/bin/python tools/eval_vector_recall.py              # 评测当前索引
    # 重建索引后再跑一次对比（改完 vector_synonyms.json / templates.jsonl 后）：
    .venv/bin/python -c "
    from agent.backend.app.core.vector_router import load_from_templates
    load_from_templates(force=True)"
    .venv/bin/python tools/eval_vector_recall.py

指标：hit@1 / hit@3（期望 recipe 是否出现在 top1 / top3）。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from agent.backend.app.core import vector_router as vr  # noqa: E402

# (口语化查询, [可接受的 recipe 列表])
# 期望列表允许语义近邻（如 obj_cut_in 与 truck_cutin_ego 场景重叠）
EVAL_CASES = [
    ("旁边车道有车加塞插到我前面", ["obj_cut_in"]),
    ("前面的大货车突然别过来", ["truck_cutin_ego", "obj_cut_in"]),
    ("我自己强行变道切到别人前面", ["ego_cut_in"]),
    ("前车突然一脚急刹", ["front_hard_brake"]),
    ("跟车跟得太近了", ["close_following", "close_follow_analysis"]),
    ("绿灯亮了我还急刹车", ["greenlight_abnormalbrake", "greenLight_abnormalbrake"]),
    ("有人横穿马路鬼探头", ["Pedestrian_Crossing", "vru_cross_conflict"]),
    ("对向来车会车", ["meeting_oncoming"]),
    ("无保护左转跟对向车博弈", ["unprotected_left_turn", "left_turn_conflict"]),
    ("过环岛转盘", ["roundabout"]),
    ("路口掉头", ["u_turn_left_1", "u_turn_left_2", "u_turn_left_3",
               "u_turn_with_lanechange", "turn_back_with_1_lanechange",
               "turn_back_with_2_lanechange", "turn_back_with_lanechange",
               "turn_back_without_lanechange"]),
    ("下高速匝道", ["off_ramp", "off_ramp_new_use_link_type"]),
    ("上匝道汇入主路", ["on_ramp", "on_ramp_new_use_link_type", "convergence"]),
    ("车道马上结束了要并出去", ["lane_ending"]),
    ("倒车避障", ["reversing", "obstacle_avoidance"]),
    ("走错路偏航了", ["route_deviation"]),
    ("连续变了两条车道", ["continuous_lane_change", "lane_change"]),
    ("弯道很急", ["large_curvature_road", "lane_curvature"]),
    ("绿灯期间异常刹车", ["greenlight_abnormalbrake", "greenLight_abnormalbrake"]),
    ("借道超卡车", ["ego_nudge_overtake_truck"]),
]


def main():
    vr._ensure_loaded()
    if not vr.is_available():
        print("向量路由不可用（模型未加载）")
        sys.exit(1)
    print(f"模型: {vr.EMBED_MODEL} | 索引条目: {vr._collection.count()}\n")

    hit1, hit3, total = 0, 0, len(EVAL_CASES)
    misses = []
    for query, expected in EVAL_CASES:
        hits = vr.search(query, top_k=3)
        top = [h[0] for h in hits]
        h1 = bool(top) and top[0] in expected
        h3 = any(t in expected for t in top)
        hit1 += h1
        hit3 += h3
        mark = "✅" if h1 else ("🟡" if h3 else "❌")
        if not h3:
            misses.append(query)
        print(f"{mark} [{hit1 and h1 or ''}] {query}")
        print(f"    top3: {', '.join(f'{n}({d:.3f})' for n, d in hits)}")

    print(f"\n===== hit@1: {hit1}/{total} ({hit1/total:.0%}) | hit@3: {hit3}/{total} ({hit3/total:.0%}) =====")
    if misses:
        print(f"完全未召回（hit@3 也失败）: {misses}")


if __name__ == "__main__":
    main()
