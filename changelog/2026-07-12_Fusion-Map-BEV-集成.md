## [2026-07-12] Fusion Map BEV 集成

**Commit**: 待提交

### 新增

1. **Fusion Map BEV 渲染器**（`BevViewer.jsx`）
   - Three.js 渲染 EFusionMap protobuf 数据（车道线、障碍物、道路边界等）
   - OrthographicCamera 俯视图，帧滑块 + 播放/暂停动画
   - 自动检测 bag 目录下 `fusion_map_plus.bin` 文件

2. **Fusion Map 后端解析器**（`fusion_map_parser.py`）
   - PB01 bin 格式读取（8-byte header: payload_len + seq_num）
   - EFusionMap protobuf 解码，缓存 frame offset 加速随机访问
   - 3 个 API 端点：info / frame / frames-range

3. **bag_parser 扩展** — 自动检测 fusion_map_topic，支持无 metadata.yaml 的 bag

### 涉及文件

- `visualizer/backend/app/services/fusion_map_parser.py` — 新建
- `visualizer/backend/app/api/fusion_map.py` — 新建
- `visualizer/backend/app/services/bag_parser.py` — fusion_map_topic 检测
- `visualizer/backend/app/models/schemas.py` — BagInfo 新增 fusion_map_topic 字段
- `visualizer/frontend/src/components/BevViewer.jsx` — 新建

---
