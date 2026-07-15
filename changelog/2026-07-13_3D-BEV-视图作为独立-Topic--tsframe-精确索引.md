## [2026-07-13] 3D BEV 视图作为独立 Topic + ts→frame 精确索引

**Commit**: 6e6ba82

### 新增

1. **3D BEV 视图作为独立 Topic 选项**（`AgentPanel.jsx`）
   - Topic 选择弹窗新增 `🗺️ 3D BEV 视图 (Fusion Map)` 选项，与 camera topic 并列
   - 选中后点击"打开 BEV 视图"直接打开 BevViewer 弹窗，而非走视频提取流程
   - 自动传入 `startTsNs`/`endTsNs`，SQL 结果的时间戳锚定到对应帧

2. **ts→frame_idx 精确索引**（`fusion_map_parser.py`）
   - PB01 bin frame header 的 `pub_ts` 值不可靠（出现 `e-190` 量级垃圾值）
   - 改用 protobuf payload 内的 `timestamp_ns`（`timestamp.sec * 1e9 + timestamp.nsec`）做索引
   - `_get_ts_ns_index()` 解码全部帧的 protobuf 取时间戳，`find_frame_idx_by_ts()` 用 bisect 做最近帧查找
   - 新增 API `GET /api/bag/fusion-map-frame-by-ts?bag_path=&ts_ns=`

3. **BevViewer 重写为 3D 渲染**（`BevViewer.jsx`）
   - `OrthographicCamera` → `PerspectiveCamera` + `OrbitControls`
   - 左键旋转、右键平移、滚轮缩放
   - 支持 `startTsNs` prop，加载时自动调 `by-ts` API 定位起始帧

### 涉及文件

- `visualizer/frontend/src/components/BevViewer.jsx` — 重写：PerspectiveCamera + OrbitControls + startTsNs
- `visualizer/frontend/src/components/AgentPanel.jsx` — fusion_map_plus 作为独立 Topic 选项
- `visualizer/backend/app/services/fusion_map_parser.py` — ts_ns 索引 + find_frame_idx_by_ts
- `visualizer/backend/app/api/fusion_map.py` — 新增 /fusion-map-frame-by-ts 端点

### 测试验证

- ✅ `ts_ns=1773624480000000000` → frame_idx=500
- ✅ `ts_ns=1773624478900793088`（frame 0）→ frame_idx=0
- ✅ 中间 ts → 正确最近帧
- ✅ 前端 E2E：Topic 弹窗选择 3D BEV → 打开 BevViewer 弹窗 → 自动定位到 SQL 结果时间戳
