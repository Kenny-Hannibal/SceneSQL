# 多摄像头宫格播放（共享Reader + 多路ffmpeg + 复用协议）

**日期**: 2026-07-18
**Commit**: (pending)

## 变更内容

1. **后端 `multi_stream_worker.py`**：
   - 1个gsbag_reader读bag（`set_topic_filter([全部topic])`）
   - 按topic路由帧到N个ffmpeg进程（HEVC直推 `-c copy`，H.264转码 `-c libx264`）
   - 所有ffmpeg输出复用为单条二进制流：`[topic_idx:1byte][data_len:4bytes LE][fMP4_data]`

2. **后端 `video.py`**：
   - 新增 `GET /api/video/stream-multi` endpoint
   - 参数：`bag_path`, `topics`(逗号分隔), `mode`, `start_ts`, `end_ts`, `fps`
   - 返回 `application/octet-stream` 复用流

3. **前端 `MultiCameraPlayer.jsx`**：
   - 新组件：多摄像头2列宫格布局
   - fetch /stream-multi → demux二进制流 → 按topic_idx分发到N个MSE SourceBuffer
   - 全局播放/暂停/速度同步

4. **前端 `AgentPanel.jsx`**：
   - topic选择弹窗新增"📷 多摄像头宫格"按钮（当camera topics > 1时显示）
   - 点击后打开MultiCameraPlayer全屏宫格

## 技术要点

- gsbag消息有 `m.topic_name` 属性，用于帧路由
- 复用协议开销仅5字节/帧（1字节topic_idx + 4字节length）
- 同一bag只读1遍，N个topic共享1次反序列化 → I/O降为1/N
- 4个ffmpeg并发时DSW CPU可承受（HEVC直推CPU开销极低）
