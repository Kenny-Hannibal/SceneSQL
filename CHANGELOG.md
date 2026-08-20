# SceneSQL — 功能变更日志

> 每次功能修改 commit 后，在此记录变更内容，方便回溯。

## 索引

| 日期 | 标题 | 文件 |
|------|------|------|
| 2026-08-19 | NL2SQL 路由过拟合修复：recipe 命中改参考 SQL 模式 + user_strategies 目录 bug（0→13 关键词） | `changelog/2026-08-19_NL2SQL路由过拟合修复-recipe命中改参考SQL模式.md` |
| 2026-08-18 | 播包可视化卡顿根因修复：resolve-bag-path TTL 缓存 + asyncio.to_thread + 弹窗先行 | `changelog/2026-08-18_播包可视化卡顿根因修复-resolve缓存-线程池-弹窗先行.md` |
| 2026-08-18 | 前端重构与交互优化：AgentPanel 拆分（2715→700行）+ Toast 替代 alert + 历史查询面板 + 主题统一 | `changelog/2026-08-18_前端重构与交互优化-组件拆分-Toast-历史查询面板.md` |
| 2026-08-18 | Y型路口策略切换 v5 标签（Round3 剔除右转专用道，评测 53/67，全量 1552 正样本） | `changelog/2026-08-18_Y型路口策略切换v5标签-Round3剔除右转专用道.md` |
| 2026-08-17 | Y型路口策略切换 v4 标签（Round2 消除四类 badcase，评测 46/55） | `changelog/2026-08-17_Y型路口策略切换v4标签-Round2消除四类badcase.md` |
| 2026-08-14 | Y型路口策略切换 v3 标签（Loop 第一轮调优，评测集 4/20→19/20） | `changelog/2026-08-14_Y型路口策略切换v3标签-loop第一轮调优.md` |
| 2026-08-13 | Y型路口策略切换 v2 标签（地图分叉点 + 轨迹匹配） | `changelog/2026-08-13_Y型路口策略切换v2标签-地图分叉点轨迹匹配.md` |
| 2026-08-11 | generate-sql 两轮路由试点（feature/gen-sql-two-round） | `changelog/2026-08-11_generate-sql两轮路由试点.md` |
| 2026-08-09 | 验证集可视化 + Mage-VL 评测 API | `changelog/2026-08-09_验证集可视化-Mage-VL评测API.md` |
| 2026-08-04 | 评测标注 + 评测集/策略同步 DataMining 产线 | `changelog/2026-08-04_评测标注-评测集与策略同步DataMining产线.md` |
| 2026-08-04 | 进度条跳动根因修复：endOfStream 前用实际缓冲终点校正 duration | `changelog/2026-08-04_进度条跳动根因修复-endOfStream前校正duration.md` |
| 2026-08-03 | 视频进度条时长预计算 + 容器化 + API 契约 v1（DataMining 集成准备） | `changelog/2026-08-03_视频进度条时长预计算-容器化-API契约v1.md` |
|| 2026-07-18 | BEV并行解码+流式预加载：ThreadPoolExecutor 4线程解码 + 首批50帧即播 | `changelog/2026-07-18_BEV并行解码-流式预加载ThreadPoolExecutor-4线程解码-首批50帧即播.md` |
|| 2026-07-18 | 多摄像头宫格：共享Reader+N路ffmpeg+复用协议 | `changelog/2026-07-18_多摄像头宫格播放-共享Reader-多路ffmpeg-复用协议.md` |
|| 2026-07-18 | 多摄像头宫格UX重构：动态宫格+拖拽+滚动+多选 | `changelog/2026-07-18_多摄像头宫格播放器UX重构.md` |
|| 2026-07-16 | 双路径架构：em_bin_path(BEV) + rosbag_path(camera) 分离 | `changelog/2026-07-16_双路径架构em_bin_path-BEV-rosbag_path-camera分离.md` |
| 2026-07-16 | BEV片段模式：只播SQL片段而非整个bag | `changelog/2026-07-16_BEV片段模式只播SQL片段而非整个bag.md` |
|| 2026-07-13 | Fix BevViewer API_BASE hardcoded localhost:8000 | `changelog/2026-07-13_Fix-BevViewer-API-BASE-hardcoded-localhost.md` |
|| 2026-07-13 | Changelog 重构：拆分为索引+条目模式 | `changelog/2026-07-13_Changlog-重构拆分为索引条目模式.md` |
|| 2026-07-13 | 3D BEV 视图作为独立 Topic + ts→frame 精确索引 | `changelog/2026-07-13_3D-BEV-视图作为独立-Topic--tsframe-精确索引.md` |
| 2026-07-12 | Fusion Map BEV 集成 | `changelog/2026-07-12_Fusion-Map-BEV-集成.md` |
| 2026-07-08 | 进程隔离根治stream卡死 + 去掉冷却期 + 可视化行标记 | `changelog/2026-07-08_进程隔离根治stream卡死--去掉冷却期--可视化行标记.md` |
| 2026-07-06 | H.264流式播放(MSE) + JWT认证 + stream-hevc卡死修复 | `changelog/2026-07-06_H264流式播放MSE--JWT认证--stream-hevc卡死修复.md` |
| 2026-06-25 | P0超时保护 + P1全量Recipe扩展 + Phase4模糊匹配（v0.4-beta） | `changelog/2026-06-25_P0超时保护--P1全量Recipe扩展--Phase4模糊匹配v04-beta.md` |
| 2026-06-24 | 3级Recipe匹配 + 产线SQL透传 + CONCEPT_RECIPE_MAP（v0.3-beta） | `changelog/2026-06-24_3级Recipe匹配--产线SQL透传--CONCEPT_RECIPE_MAPv03-beta.md` |
| 2026-06-23 | Pipeline Recipe + EXPLAIN 试编译 + Round 3 自纠错（v0.2-beta） | `changelog/2026-06-23_Pipeline-Recipe--EXPLAIN-试编译--Round-3-自纠错v02-beta.md` |
| 2026-06-23 | 两轮NL2SQL引擎初版（v0.1-alpha） | `changelog/2026-06-23_两轮NL2SQL引擎初版v01-alpha.md` |
| 2026-06-09 | SQLite 批量模式取消 DB 数量限制 + 结果数量输入优化 + 修复可视化连接泄漏 | `changelog/2026-06-09_SQLite-批量模式取消-DB-数量限制--结果数量输入优化--修复可视化连接泄漏.md` |
| 2026-06-09 | SqlEditor 替换为 CodeMirror 6 + HEVC 流式直传播放（方案 1.5） | `changelog/2026-06-09_SqlEditor-替换为-CodeMirror-6--HEVC-流式直传播放方案-15.md` |
| 2026-06-09 | HEVC 流式直传播放（方案 1.5） | `changelog/2026-06-09_HEVC-流式直传播放方案-15.md` |
| 2026-06-04 | DuckDB 方言适配 + 前端UX改进 | `changelog/2026-06-04_DuckDB-方言适配--前端UX改进.md` |
| 2026-05-27 | 客户端分页重构 | `changelog/2026-05-27_客户端分页重构.md` |
| 2026-05-27 | 分页翻页功能修复 | `changelog/2026-05-27_分页翻页功能修复.md` |
| 2026-05-22 | f8 deploy.sh --force 逻辑修复 | `changelog/2026-05-22_f8-deploysh---force-逻辑修复.md` |
| 2026-05-22 | DSW 前端 bug 修复 | `changelog/2026-05-22_DSW-前端-bug-修复.md` |
| 2026-05-22 | UI 修改 — SQL 编辑器 + 分页控件 | `changelog/2026-05-22_UI-修改--SQL-编辑器--分页控件.md` |
| 2026-05-22 | gsbag 降级处理 | `changelog/2026-05-22_gsbag-降级处理.md` |
| 2026-05-22 | 本机环境搭建 | `changelog/2026-05-22_本机环境搭建.md` |
| 2026-06-23 | Schema Dictionary 增强：DR Trajectory 列描述 | `changelog/2026-06-23_Schema-Dictionary-增强DR-Trajectory-列描述.md` |
