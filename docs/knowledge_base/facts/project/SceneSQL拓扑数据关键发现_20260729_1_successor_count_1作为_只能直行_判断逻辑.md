---
category: project
tags: SceneSQL,topology,successor_count,lane_turn_type,intersection
---

SceneSQL拓扑数据关键发现(20260729): 1) successor_count=1作为"只能直行"判断逻辑错误——successor_count衡量"道路段后面连接几条路"非"路口出口方向"。2) 路口link(100000000等)是孤立占位节点，predecessor/successor全空，无法通过link_successor跳转。3) 正确条件是dynamic_lane.lane_turn_type：ego所在lane turn_type=直行→直行路口(P=0.853,R=0.734,F1=0.789)。4) 两套ID系统：static_link用大数字字符串，dynamic_link/lane用小整数，通过ego表映射。详见skill aidgo-scenario-platform references/topology-lane-turn-type-validation.md
