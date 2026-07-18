# BEV并行解码 + 流式预加载

**日期**: 2026-07-18
**Commit**: (pending)

## 变更内容

1. **后端 `fusion_map_parser.py` — 并行解码**：
   - 将 `read_fusion_map_frames_range` 的"读帧+解码"流程拆成两步：
     - Step 1: 顺序读取原始字节（文件 I/O 必须 seek+read，不可并行）
     - Step 2: `ThreadPoolExecutor(max_workers=4)` 并行解码 protobuf
   - 新增 `_read_raw_frame(bin_path, offsets, idx)` — 从文件读取单帧原始字节
   - 新增 `_decode_raw_frame(item)` — 解码单帧 protobuf 字节
   - 小批量（≤10帧）时跳过并行，直接顺序解码（避免线程池开销）
   - 并行解码后按 frame_idx 排序，保证顺序一致
   - 预期效果：200帧批量解码从 ~4s 降到 ~1-1.5s（ParseFromString C扩展大概率释放GIL）

2. **前端 `BevViewer.jsx` — 流式预加载**：
   - **之前**：`await prefetchFrames(sIdx, eIdx)` 加载全部帧后才显示首帧（1154帧等23秒）
   - **现在**：首批50帧加载完即显示首帧并可播放，后台 `prefetchFramesInBackground` 继续加载
   - 新增 `prefetchFramesInBackground` 函数（不阻塞播放，带进度条）
   - `playNextFrame` 改为流式模式：缓存命中→正常10fps播放，缓存未命中→50ms后重试（不跳帧）

3. **dm_sdk 并行化 — 跳过**：
   - `_resolve_dual_paths_via_dm` 的两次 API 调用有**顺序依赖**：
     - `ProdDataClient.get_bag_metadata` → 返回 `origins`
     - `RawDataClient.get_bag_metadata(bag_id=origin_bag_id)` → 需要 `origins[0]`
   - 无法真正并行，保持现状

## 涉及文件

- `visualizer/backend/app/services/fusion_map_parser.py` — 并行解码逻辑
- `visualizer/frontend/src/components/BevViewer.jsx` — 流式预加载 + 后台加载

## 测试验证

- [ ] DSW 部署后浏览器 E2E：BEV 播放首个bag，50帧内开始播放
- [ ] 全bag播放：1154帧，后台加载进度条正常，播放不卡不跳帧
- [ ] 片段模式：正常播放
