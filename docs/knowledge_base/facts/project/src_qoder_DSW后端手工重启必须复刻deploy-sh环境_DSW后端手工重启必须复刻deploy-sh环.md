---
category: project
tags: qoder
---

[src=qoder:DSW后端手工重启必须复刻deploy-sh环境] DSW后端手工重启必须复刻deploy-sh环境
DSW text2sql 后端手工重启的坑 — 必须复刻 deploy.sh 完整环境

手工重启 `/root/data/text2sql` 后端（`uvicorn backend.app.main:app --port 30001`）时，必须完整复刻 `visualizer/deploy.sh` 的环境，否则出两类故障：

1. **漏 `source /root/data/text2sql/.env`** → `settings.SQLITE_DB_PATH=None` → 服务回落到只读镜像默认路径 `/mnt/gacrnd-oss/gac_liulian/common_data`，查不到写入 `/mnt/ubm_code_nas/gac_huangzijian/common_data` 的数据（且该镜像是只读，无法写标签）。
2. **漏 gsbag SDK 环境**（`GSBAG_SDK`、`HOBOT_COM_SDK`、`LD_LIBRARY_PATH` 四段导出）→ 视频抽帧报 "gsbag SDK not available"，`/api/video/extract-batch` 全部失败。

可直接用的重启脚本：DSW `/root/restart_backend.sh`（2026-08-28 建，含完整环境 + `</dev/null` 防 ssh 挂起）。

注意：重启会丢失内存中的 extract-batch 任务状态（轮询返回 404），抽帧任务运行期间不要重启服务。
