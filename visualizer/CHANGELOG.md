# Changelog

All notable changes to the SceneSQL Visualizer project will be documented in this file.

## [Unreleased]

### Added - 2026-07-13

- **3D BEV 视图作为独立 topic**：SQL 查询结果点"播包可视化"时，弹窗新增 `fusion_map_plus` 独立选项，选中后打开 3D BEV 渲染弹窗而非视频提取 (#65ec5a4)
  - 前端：BevViewer 从 `OrthographicCamera` 改为 `PerspectiveCamera` + `OrbitControls`（左键旋转、右键平移、滚轮缩放）
  - 前端：BevViewer 新增 `startTsNs`/`endTsNs` props，自动锚定到 SQL 结果的起始时间戳对应的帧
  - 前端：AgentPanel 话题选择弹窗区分 BEV topic 和 Camera topic，确认按钮文案动态切换
  - 后端：新增 `GET /api/bag/fusion-map-frame-by-ts?bag_path=&ts_ns=` API（二分查找 ts→frame_idx）
  - 后端：fusion_map_parser 新增 `_ts_ns_index` 从 protobuf `timestamp` 字段构建时间戳索引（PB01 帧头 pub_ts 不可靠）

### Added - 2026-07-12

- **Fusion Map BEV 集成**：参照 UBM_Data_IDE 项目，将 `/gac/enviro_model/fusion_map_plus` (EFusionMap) 数据集成到 SceneSQL 可视化
  - 后端：`fusion_map_parser.py` — PB01 bin 读取 + EFusionMap protobuf 解码 + 帧偏移缓存
  - 后端：`fusion_map.py` API — `/fusion-map-info`、`/fusion-map-frame`、`/fusion-map-frames-range` 三个端点
  - 后端：`bag_parser.py` — 自动检测 `fusion_map_topic`，无 metadata.yaml 时从 `bin/` 目录扫描
  - 前端：`BevViewer.jsx` — Three.js BEV 渲染组件（障碍物/车道线/边界线/路径/车辆位置）
  - 前端：`App.js` — BEV View / Camera tab 切换器
  - Proto：SceneSQL `j6/Comm/boleidl_pb2.py` 替换为 UBM 超集版本（兼容 image_encode + EnviroModeling）
  - Proto：新增 `j6/EnviroModeling/boleidl_pb2.py`（从 UBM 复制）
