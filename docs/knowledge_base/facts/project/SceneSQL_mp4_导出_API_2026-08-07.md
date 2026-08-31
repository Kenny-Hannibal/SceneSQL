---
category: project
tags: SceneSQL,mp4,export,API,video
---

SceneSQL mp4 导出 API (2026-08-07):
- 用途: bag 视频片段 → mp4 文件, 供 Mage-VL 推理
- 路径: /data/var/workspace/projects/projects/SceneSQL/visualizer/backend/app/api/video.py
- 端点:
  1. POST /api/video/extract — bag → mp4
     输入: ExtractRequest(bag_path, topic, start_ts, end_ts, fps)
     输出: ExtractResponse(task_id, status, message)
     后台执行: extract_topic_to_mp4()
  2. GET /api/video/status/{task_id} — 查询状态
     输出: VideoStatus(task_id, status, video_url, progress, message)
  3. GET /api/video/file/{task_id} — 下载 mp4
     返回: FileResponse(video_path, media_type="video/mp4")
  4. GET /api/video/stream-hevc — HEVC remux fMP4 (流式)
  5. GET /api/video/stream-h264 — H.264 transcoded fMP4 (流式)
- 认证: POST /api/auth/login (username=gac, password=gac_data) → access_token
- 时间戳: 纳秒 (×10^9)
- Topic: 新bag=/gac/cam/orig_fw120_encoded (10Hz), 老bag=/gac/cam/fw120_encoded (28Hz)
