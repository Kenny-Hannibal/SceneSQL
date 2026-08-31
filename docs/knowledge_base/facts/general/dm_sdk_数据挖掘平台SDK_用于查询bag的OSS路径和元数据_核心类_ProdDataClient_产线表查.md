---
category: general
tags: SceneSQL,tool,bag-path
---

dm_sdk: 数据挖掘平台SDK，用于查询bag的OSS路径和元数据。核心类: ProdDataClient(产线表查询), RawDataClient(原始表查询)。SceneSQL项目封装在 tools/rosbag_path_resolver.py 的 RosbagPathResolver 类。关键方法: resolve_em_bin_path(data_id) → BagPathInfo(em_bin_oss_path, em_bin_local_path, origin_table, origin_bag_id, vin, vehicle_model)。DSW上有安装，本机pip不可用。access_token在代码中有默认值。
