---
category: project
tags: qoder
---

[src=qoder:SceneSQL场景交付纪律-不注入标签走SQL打标评测集] SceneSQL场景交付纪律-不注入标签走SQL打标评测集
SceneSQL 场景交付纪律 — 不注入标签，走 SQL+打标+评测集流程

**用户明确规定（2026-08-28）：不要把自定义标签写入各 bag SQLite 的 `range_tag` 表，也不要注册 `user_strategies` 策略 YAML。**

原因：产线生产环境的 SQL DB 没有本地注入的标签，注册策略/注入标签后产线 SQL 会搜不到任何结果。

正确交付流程（与 自车左转/右转/合流被挤压 评测集同款）：
1. **编写高召回 SQL**（精度次要），经 DSW 部署的 SceneSQL `/api/agent/execute-sql` 执行（query_mode=sqlite，batch_id 如 20260702_T68_2471_c5afa57_100w）；
2. **可视化打标**：`/api/video/extract-batch` 抽帧 + 人工复核（Mage-VL 判正例不可信，抽样精度≈0，正例必须帧中可见证据才保留；负例可宽泛采信）；
3. **导入评测集**：`/api/eval-labels`（verdict pass→正例/fail→负例，时间戳秒）+ `/api/eval-labels/{strategy}/sync-evalset`（benchmark 名，标签 = {strategy}_positive/_negative），每集合 ~100 条正+负。

历史教训：2026-08-27 曾为「跟大车」注入标签+注册策略，次日被用户纠正，全部回滚（清除 4,920 行注入标签、删除策略 YAML、重启服务）。
