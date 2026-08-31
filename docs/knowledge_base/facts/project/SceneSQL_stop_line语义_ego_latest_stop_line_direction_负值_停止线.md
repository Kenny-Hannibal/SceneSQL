---
category: project
tags: SceneSQL,stop_line,intersection
---

SceneSQL stop_line语义: ego.latest_stop_line_direction, 负值=停止线前方(未过), 正值=已越过停止线, 0=无停止线信息。从负→正穿越零点=过停止线时刻。cumulative_distance配合使用可精确定位。Intersection标签的end_ts由op_intersection.py定义(基于EgoIntoIntersection事件)，不保证覆盖到完全驶出路口。
