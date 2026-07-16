# 双路径架构：em_bin_path(BEV) + rosbag_path(camera) 分离

## 日期
2026-07-16

## 问题
输入 em bin bag_id 后，摄像头视频黑屏。根因：代码把 em bin 路径当成 rosbag 路径去读 camera.bag，而 em bin 目录下没有 camera 数据。

## 方案
实现双路径架构：
- **em_bin_path** → BEV 3D（读 fusion_map_plus.bin）
- **rosbag_path** → camera 视频（读 metadata.yaml + camera.bag）

两个路径通过 dm_sdk 分别查询：
1. ProdDataClient → em bin 的 storage_prefix → OSS_MOUNT_MAP → 本地路径
2. origins → RawDataClient → rosbag 的 storage_prefix → OSS_MOUNT_MAP → 本地路径

## 变更

1. `bag_parser.py` — 重构 `get_bag_info`：输入 bag_id → `_resolve_dual_paths_via_dm` 分别返回 `em_bin_path` 和 `rosbag_path`
2. `video.py` — stream/extract 接口接收 rosbag_path，不再从 em bin 路径找 bag.bag；`_resolve_rosbag_for_stream` 作为兜底
3. `schemas.py` — `BagInfo` 新增 `bag_id`, `em_bin_path`, `rosbag_path`, `rosbag_oss_path`, `em_bin_oss_path` 字段
4. `App.js` — `bagPath` → `bagInput`(用户输入) + `emBinPath`(BEV用) + `rosbagPath`(camera用)；`loadBag` 后保存双路径；stream/extract 用 `rosbagPath`，BevViewer 用 `emBinPath`

## 验证
- API `/api/bag/info?bag_path=1002AePBU4WlfnBzNtDbBu202606` 返回 `em_bin_path` 和 `rosbag_path`
- `rosbag_path` → stream-hevc → 200MB MP4 文件，camera 流正常
- `em_bin_path` → fusion_map_plus.bin 存在，BEV 数据可读
- SQL结果可视化按钮（extract-batch）逻辑不变，仍走 `_resolve_bag_path_via_dm`
