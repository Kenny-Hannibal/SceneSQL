---
category: project
tags: text2sql,architecture,sceneSQL
---

SceneSQL Pipeline Recipe + CTE Block Assembly架构设计（已确认）：
- 两层确定性路由：NL→概念(LLM)→tag_names→Recipe(查表)，非语义匹配
- 混合组装：代码组装CTE骨架+参数(LLM写不好200行CTE)，LLM Round2只写最终SELECT+WHERE过滤(需语义理解)
- Block库来源：从db_py_rule/60个生产SQL中抽取，首批7个Block(event_extraction/ego_speed_analysis/proximity_analysis/conflict_classification/event_merge/duration_filter/final_output)
- 首批2个Recipe：conflict_pipeline(他车横穿)、turn_conflict_pipeline(左转/右转冲突)
- 5大原型：A_conflict_pipeline(6), B_track_pipeline(17), C_gaps_islands(15), D_range_tag_simple(5), E_complex_composite(17)
- db_py_rule/的SQL是同运行环境(秒级时间戳)的正确参考，templates.jsonl的34处*1e9是从Python operator(纳秒)错误移植
