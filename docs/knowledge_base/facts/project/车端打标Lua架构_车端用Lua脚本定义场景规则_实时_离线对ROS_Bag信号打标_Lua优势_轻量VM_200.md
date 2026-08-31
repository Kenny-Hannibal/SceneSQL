---
category: project
tags: SceneSQL,UBM,lua,tagging,architecture
---

车端打标Lua架构: 车端用Lua脚本定义场景规则，实时/离线对ROS Bag信号打标。Lua优势: 轻量VM(~200KB)、热加载、沙箱隔离、汽车行业传统(Carla/ADTF)。两种标签: range_tag(持续场景,如then_brake持续3秒) + point_tag(瞬时事件)。打标引擎: ROS话题→信号总线→Lua VM规则→SQLite(.db)。993个db文件即Lua打标产物。
