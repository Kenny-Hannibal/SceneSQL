---
category: project
tags: SceneSQL,抽帧,API,extract-batch,video
---

SceneSQL 抽帧 API (extract-batch):
- 路径: /data/var/workspace/projects/projects/SceneSQL/visualizer/backend/app/api/video.py
- 端点: POST /api/video/extract-batch (批量抽帧)
  - 输入: clips array, 每个 clip = {bag_id, start_ts, end_ts, topic, sample_fps, max_frames_per_clip, resolve_bag_path}
  - 输出: task_id
- 端点: GET /api/video/extract-batch/{task_id} (查询任务状态)
- 端点: GET /api/video/frames/{task_id}/{idx}/{filename} (下载JPEG)
- 认证: POST /api/auth/login (username=gac, password=gac_data) 获取 token
- 调用脚本: SceneSQL/v11_recall/extract_frames_v12.py
- topic: 新bag=/gac/cam/orig_fw120_encoded (10Hz), 老bag=/gac/cam/fw120_encoded (28Hz)
- 时间戳: 纳秒 (×10^9)
- 注意: 使用 Mage-VL 时直接送完整视频, 不走抽帧 API
