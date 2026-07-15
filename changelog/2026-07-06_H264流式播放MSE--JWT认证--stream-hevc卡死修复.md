## [2026-07-06] H.264流式播放(MSE) + JWT认证 + stream-hevc卡死修复

**Commit**: e7b37e0 (JWT认证) + 1b70d30 (H.264流式播放)

### 新增

1. **`/stream-h264` 流式API端点**（`video.py`, `video_extractor.py`）
   - gsbag帧→frame_queue(60帧)→ffmpeg libx264→fMP4 stdout→StreamingResponse
   - 复用HEVC stream架构(producer-consumer+selectors+stop_event)，仅ffmpeg编码不同
   - 不支持HEVC的浏览器也能MSE边转边播，无需等全片转码

2. **JWT登录认证**（`auth.py`, `main.py`, `LoginPage.jsx`, `App.js`）
   - JWT token认证，支持Authorization:Bearer和?token=查询参数
   - 前端LoginPage组件、authFetch wrapper、token存localStorage
   - `<video src>`和MSE fetch通过?token=参数传递认证

3. **前端MSE codec动态选择**
   - playerData.mse_codec字段，HEVC用hvc1.1.6.L120.B0，H.264用avc1.64001f
   - 播放优先级：HEVC MSE > H.264 MSE > H.264全量转码(仅MSE完全不支持的浏览器)

### 修复

1. **stream-hevc卡死** — process.stdout.read(262144)阻塞→客户端断开后finally执行不到→gsbag全局锁不释放→新请求无限等待。修复：selectors轮询+stop_event+Request.is_disconnected()
2. **H.264视频黑屏** — 登录认证后`<video src>`不带Authorization header→401→无数据。修复：video_url用addTokenParam()拼?token=
3. **auth:401事件** — 去掉window.fetch monkey-patch（竞态风险），改用auth:401自定义事件触发登出

### 重构

1. **video_extractor.py公共函数抽取** — `_get_fps_config()`, `_start_bag_reader()`, `_clamp_time_range()`, `_start_feed_and_writer()`, `_cleanup_stream()` 供HEVC stream和H.264 stream复用
2. **extract_topic_to_mp4保留** — 仍用于需要生成MP4文件下载的场景，日常播放改用stream

### 涉及文件

- `visualizer/backend/app/api/video.py` — 新增/stream-h264端点
- `visualizer/backend/app/services/video_extractor.py` — 新增extract_topic_h264_stream() + 公共函数重构
- `visualizer/backend/app/core/auth.py` — JWT认证模块（新建）
- `visualizer/backend/app/main.py` — auth_middleware接入
- `visualizer/frontend/src/components/LoginPage.jsx` — 登录页（新建）
- `visualizer/frontend/src/components/AgentPanel.jsx` — H.264流式MSE播放 + authFetch + addTokenParam
- `visualizer/frontend/src/App.js` — 登录状态管理 + authFetch

### 测试验证

- ⚠ H.264流式MSE播放待用户端验证（刚部署）
- ✅ stream-hevc卡死修复：关闭页面后新请求不再卡死
- ✅ JWT认证：登录/token/verify均正常，?token=参数认证生效
- ✅ H.264转码播放：带token参数后视频正常播放
