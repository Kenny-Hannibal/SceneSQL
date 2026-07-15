# 3D BEV 视图：em bin 路径解析

**日期**: 2026-07-15
**Commit**: 待提交

## 问题

3D BEV 视图需要读取 fusion_map_plus.bin，该文件仅存在于 em bin 目录（gacrnd-ali-pipeline-ubm-vehicle-module-bin），不在原始 rosbag 目录。
现有 resolve-bag-path 通过 origins 追溯到原始 rosbag 的 storage_prefix，无法定位 em bin 文件。

## 改动

1. `.env` - OSS_MOUNT_MAP 新增 `gacrnd-ali-pipeline-ubm-vehicle-module-bin:/mnt/gacrnd-ali-pipeline-ubm-vehicle-module-bin`
2. `tools/rosbag_path_resolver.py`:
   - BagPathInfo 新增 em_bin_oss_path、em_bin_local_path 字段
   - 新增 resolve_em_bin_path(data_id) 方法：直接查询产线表中 em bin 自身的 storage_prefix，不走 origins
3. `visualizer/backend/app/api/agent.py`:
   - resolve-bag-path 端点改用 resolve_em_bin_path，返回 em_bin_oss_path 和 em_bin_local_path

## 验证

- DM SDK 503 时 fallback 到原有 resolve 逻辑，不影响现有功能
- DM 服务恢复后 em_bin_local_path 将返回如 /mnt/gacrnd-ali-pipeline-ubm-vehicle-module-bin/bag/{data_id}/
