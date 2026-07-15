## [2026-06-23] Schema Dictionary 增强：DR Trajectory 列描述

- **修改**: `schema_dictionary.yaml` 中 `ego.ego_dr_trajectory` 和 `dynamic_obj.obs_dr_trajectory` 描述增强
- **新增**: DR trajectory JSON 结构说明 (`{x:[5], y:[5], theta:[5], speed:[5], exists:[5]}`)
- **新增**: 漂移值计算方法 (`json_extract` 提取 `$.x[4]-$.x[0]`)
- **修复**: 防止LLM生成不存在的 `dr_trajectory_drift` / `obs_dr_trajectory_drift` 列名
- **测试**: 0616数据集 19/20 通过（#20 DR轨迹漂移待此修复后验证）
