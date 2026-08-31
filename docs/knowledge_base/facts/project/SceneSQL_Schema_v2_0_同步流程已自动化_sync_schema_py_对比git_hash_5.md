---
category: project
tags: SceneSQL,schema,sync,validation
---

SceneSQL Schema v2.0 同步流程已自动化: sync_schema.py 对比git hash → 5策略从源码提取label_id → 自动更新母表 → 自动派生structure/dictionary → 自动同步ETL CORE_TABLES。验证方式: SQLite DB扫描找gap(互补非主流程)。当前range_tag.tag_name 192个枚举值，SQLite 94标签100%覆盖。排除规则: _OBJ_TYPE_VALUES(car/bus等dynamic_obj.type)不应进入range_tag。
