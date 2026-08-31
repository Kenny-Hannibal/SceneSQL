---
category: project
tags: SceneSQL,合流,convergence,link_turn_type,SQL
---

SceneSQL合流SQL模式：是分流SQL的镜像。分流=当前link(successor>=2)→辅路；合流=辅路→下个link(predecessor>=2)的主路。关键过滤：next_link_turn_type='进入'，排除辅路自然变主路和正常转弯。link_turn_type枚举含'进入'(合入主路)和'退出'(分流出主路)。不用range_tag+lane_trans_type='合流'方案（0命中，因为合流标签时间与ego所在link不匹配）
