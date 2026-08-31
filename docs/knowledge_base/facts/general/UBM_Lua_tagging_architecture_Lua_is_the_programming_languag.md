---
category: general
tags: ubm,lua,tagging,topology,scene-sql
---

UBM Lua tagging architecture: Lua is the programming language, the tagging engine/framework is company-built (C++ engine running Lua scripts). Lua rules produce range_tag/point_tag tables in SQLite .db files. topology_constraint tags use topology_constraint_feature.py + specify_topology_feature.py; sub_tag classification (t_junction, cross_road) requires lmc_fusion_map_raw ROS topic data. Without it, all intersections get generic sub_tag. See nl2sql-optimization skill references/topology-constraint-tags.md
