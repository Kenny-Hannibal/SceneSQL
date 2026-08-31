---
category: general
tags: SceneSQL,NL2SQL,专利,架构
---

SceneSQL NL2SQL核心架构(专利交底书级别): 两轮交互+三轮回退。Round1=ConceptRouter概念识别→组合方式(single_tag/multi_tag/tag_join_ego/cross_table/cte_analysis等)+recipe/required_blocks判定。Round2=多层上下文注入(L0 Schema Card常驻~200token + L1过滤Schema + L2标签语义 + L3跨表JOIN规则 + L4 Few-shot模板)→LLM生成SQL/Recipe组装。Round3=EXPLAIN试编译+字段完整性校验+LLM纠错循环(max 3次)。创新点: Pipeline Recipe声明式组装(零LLM成本)、Hybrid混合组装(auto blocks+LLM胶水CTE)、关键词路由+概念路由双层路由、批量并行查询(32并发+提前终止)。9张表(range_tag/ego/dynamic_obj/static_obj/dynamic_lane/dynamic_link/static_lane/static_link/intersection_info)。
