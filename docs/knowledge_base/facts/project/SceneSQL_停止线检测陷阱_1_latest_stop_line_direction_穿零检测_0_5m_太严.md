---
category: project
tags: SceneSQL,SQL,stop_line,traffic_light
---

SceneSQL 停止线检测陷阱：1) latest_stop_line_direction 穿零检测(±0.5m)太严格，帧率粒度不够导致大量漏检(6678→933)。2) 正确做法：找 direction<0 的帧，用 cumulative_distance + ABS(direction) 反推停止线位置。3) 红绿灯检测：tag期间latest_traffic_light_status多为"未知"，改为检测停止线附近(direction<0)时有红绿灯帧，识别率更高。
