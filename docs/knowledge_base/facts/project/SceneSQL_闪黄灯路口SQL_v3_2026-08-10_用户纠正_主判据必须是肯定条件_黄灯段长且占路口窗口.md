---
category: project
tags: SceneSQL,flash-yellow,sql
---

SceneSQL 闪黄灯路口SQL v3(2026-08-10)：用户纠正——主判据必须是肯定条件"黄灯段长且占路口窗口主导"(max_yellow_dur>=3s + 黄占比>=60%)，直接表达"只有黄灯在闪"。禁止用排除法(红绿占比/自车行为)当主判据。右转箭头黄闪bad case靠时长形态(短促1-2s)排除，不靠ego行为。路口标签统一用 topology_intersection%。traffic_light_status是单标量无法区分主灯/箭头灯/方向灯。
