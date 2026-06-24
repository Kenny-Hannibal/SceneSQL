# SceneSQL — 功能变更日志

> 每次功能修改 commit 后，在此记录变更内容，方便回溯。

## [2026-06-24] 3级Recipe匹配 + 产线SQL透传 + CONCEPT_RECIPE_MAP（v0.3-beta）

**Commit**: cf5d707
**Tag**: v0.3-beta

### 新增

1. **3级 Recipe 匹配策略**（`concept_router.py`）
   - Phase 1: Round1 concepts 精确匹配 CONCEPT_RECIPE_MAP
   - Phase 2: 子串匹配（concepts 子串包含 map key）
   - Phase 3: NL 原文匹配（NL 关键词直接命中 map key）
   - 解决 Round1 LLM 返回 concepts 与 map key 不对齐的问题

2. **10个产线 SQL 透传 Recipe**（raw_sql 模式）
   - `turn_bypass_overtake` — 绕行超车
   - `ego_decel_during_lanechange` — 变道减速
   - `greenlight_abnormalbrake` — 绿灯异常刹车
   - `truck_safe_cutin_ego` — 卡车安全切入
   - `meeting_oncoming` — 会车
   - `nudge_borrowlane` — 借道避让
   - `ego_overtake_catin_truck` — 超车切入卡车
   - `redlight_slowmoving` — 红灯缓行
   - `front_hard_brake` — 前车急刹
   - `reversing` — 倒车

3. **CONCEPT_RECIPE_MAP 扩展**（14个中文概念映射）
   - 绕行超车/变道减速/绿灯刹车/卡车切入/会车/借道避让/超车切入卡车/红灯缓行/前车急刹/倒车 等

4. **Block Assembler raw_sql 快速路径**
   - `block_assembler.py` assemble() 检测 variant 含 `raw_sql` 时直接返回，不经过 Block 组装
   - 产线 300+ 行 CTE 链无需拆 Block，直接透传执行

5. **`parse_round1_output` 新增 `nl` 参数**
   - Phase 3 NL 原文匹配需要原始 NL 问题
   - `agent_engine.py` 调用处已传入 `question`

### 修复

- SQLite 3.37.2 兼容性：`json_extract_string` → `json_extract`；CASE 比较值去掉多余引号

### 测试验证

- "前车急刹" → Recipe 命中 → 188行产线 SQL → 85 DB 命中 → 100条记录 ✅
- "绿灯异常刹车"/"会车"/"绕行超车"/"借道避让" → 全部 sql_source=recipe ✅
- "找出变道场景" → Block 组装 Recipe → 69 DB 命中 ✅

### 涉及文件

- `agent/backend/app/core/concept_router.py` — CONCEPT_RECIPE_MAP + 3级匹配 + nl参数
- `agent/backend/app/services/agent_engine.py` — parse_round1_output 传入 question
- `agent/backend/app/core/block_assembler.py` — raw_sql 快速路径
- `agent/backend/app/core/recipes/` — 10个 raw_sql Recipe YAML
- `agent/backend/app/core/blocks/` — 2个 Block 修复(lane_change_detail, close_follow_detail)
- `visualizer/backend/app/api/agent.py` — /query-stream SSE 两轮路径

## [2026-06-23] Pipeline Recipe + EXPLAIN 试编译 + Round 3 自纠错（v0.2-beta）

**Commit**: e030bcf
**Tag**: v0.2-beta

### 新增

- **Pipeline Recipe + CTE Block Assembly 架构**
  - `block_assembler.py` — BlockLibrary + RecipeLibrary + BlockAssembler 引擎
  - 7 个 Block YAML 模板：event_extraction, ego_speed_analysis, proximity_analysis, conflict_classification (vehicle/vru variants), duration_filter (vehicle/vru variants), event_merge, time_calc
  - 2 个 Recipe YAML：conflict_pipeline (vehicle/vru), turn_conflict_pipeline (left_turn/right_turn)
  - 代码组装 CTE 骨架，LLM 只写最终 SELECT/WHERE
  - Round 1 LLM 同时输出 `concepts` + `recipe`/`recipe_variant`
  - 匹配 Recipe 时 BlockAssembler 直接产出 SQL，跳过 Round 2 LLM

- **EXPLAIN 试编译 + Round 3 LLM 自纠错循环**
  - `_dry_run(sql)` — 在 sample DB 上 EXPLAIN 预检，捕获语法错误
  - `_build_correction_prompt()` — 将报错信息 + 原始 SQL + schema 回传 LLM 修复
  - 纠错循环：最多 3 次（`max_corrections=3`），日志记录每次失败及 SQL 来源（recipe/llm/llm_fallback）
  - 超限处理：返回 failed SQL + `max_corrections_exceeded=True`
  - `AgentResult` 新增 `correction_rounds` / `max_corrections_exceeded` 字段
  - 两轮引擎和旧流程 fallback 均包含纠错循环

- **前端纠错超限弹框**
  - Catppuccin 暗色主题，标题 `⚠️ SQL 纠错次数已达上限`
  - 显示纠错轮次和最大次数
  - 按钮：「关闭」+「复制 SQL 作为 Bad Case」（自动复制到剪贴板）
  - z-index 10000

- **API 字段透传**
  - `generate-sql` 响应新增 `correction_rounds` + `max_corrections_exceeded`
  - `/query` SSE stream completed 事件新增相同字段
  - `AgentQueryResponse` schema 新增相同字段

- **4 个 Recipe Variant 端到端验证通过**
  - conflict_pipeline/vehicle: 98/11878 DBs 命中
  - conflict_pipeline/vru: 90/11878 DBs 命中
  - turn_conflict_pipeline/left_turn: 77/11878 DBs 命中
  - turn_conflict_pipeline/right_turn: 0/11878（数据集无右转冲突，正常）

### 修复

- **turn_conflict_pipeline `near ")": syntax error`**：单元素 IN 列表多余尾逗号 `IN ('pedestrian',)` → `IN ('pedestrian')`
- **`_clean_sql` 截断多行 SQL**：首行 SELECT/WITH 则返回全文
- **`build_prompt(query_mode=...)` 签名不匹配**：移除不存在的 `query_mode` 参数
- **`_validate_sql` DROP 误报**：word boundary 匹配替代简单 `in` 检查
- **`_validate_sql` 表名校验死代码**：循环体只 continue 无任何校验，已删除
- **FALLBACK_SYSTEM_PROMPT "ego.ts是纳秒"**：改为"所有时间字段秒级，直接比较"（`*1e9` bug 根源）
- **`_clean_sql` 硬修正误伤**：`WHERE speed > 1000` 被匹配 → 改为 `r'(\w+)\s*\*\s*(?:1e9|...)\b' → r'\1'`，只匹配字段名*数值
- **concept_groups.yaml 5 处 `*1e9`**：`r.start_ts * 1e9 AND r.end_ts * 1e9` → `r.start_ts AND r.end_ts`

### Round 3 纠错实测

- 10 个测试场景中 1 个触发 Round 3（DR 轨迹查询，`e.position_x` 列名不存在）
- 纠错 1 次后自动修复（`e.position_x` → `e.utm_x AS position_x`）
- 0 个场景触发超限弹框

### 已知局限性（v0.2-beta）

1. **Recipe SQL 试编译失败不应走 LLM 纠错**：Recipe 语法错 = 模板 bug，LLM 纠错可能引入错误逻辑，应直接报错给开发者
2. **`max_corrections` 硬编码为 3**：应提取到配置文件或环境变量
3. **Recipe 覆盖范围有限**：仅冲突+转弯，Cutin/LaneChange/CloseFollow 等尚未有 Recipe
4. **`_validate_sql` 无语义校验**：仅 4 步静态检查 + EXPLAIN，无 JOIN 条件缺失等语义检查
5. **concept_groups.yaml 冲突/段检测概念组缺失**：Phase 0 的 composition_rules 骨架修复尚未完成

## [2026-06-23] 两轮NL2SQL引擎初版（v0.1-alpha）

**Commit**: db4e018 (后续硬修正待提交)

### 新增
- 两轮LLM架构：Round 1 概念识别 + Round 2 SQL生成
- `concept_groups.yaml` — 概念→tag_name映射（~25个概念组）
- `concept_router.py` — Round 1识别+Round 2上下文组装
- 7种组合模式：single_tag / multi_tag / tag_join_ego / tag_join_dynamic_obj / tag_join_dynamic_lane / cross_table / cte_analysis
- `schema_dictionary.yaml` 增强 DR trajectory 列描述（json_extract漂移值计算方法）

### 修复
- 移除错误的时间戳转换 `* 1e9`：`range_tag.start_ts/end_ts` 与 `ego.ts`/`dynamic_obj.ts` 单位相同（秒级），无需乘1e9
- `_clean_sql` 增加硬修正：自动剥离 LLM 仍输出的 `* 1e9`
- 修复 YAML ScannerError（schema_dictionary 中 json_extract 示例含冒号需加引号）

### 已知局限性（v0.1-alpha）
1. **复杂CTE/轨迹交叉SQL质量差**：#17"无保护左转轨迹交叉"只生成简单range_tag查询，无轨迹交叉计算逻辑
2. **LLM偶尔仍输出 `* 1e9`**：DeepSeek-v4-flash 从训练数据"记住"了错误模式，需 `_clean_sql` 硬修正兜底
3. **复合标签空结果**：部分场景（如#10闯红灯+ego速度）SQL正确但数据中可能无匹配
4. **行人类型字段值不确定**：`d.type = 'PEDESTRIAN'` 可能不匹配实际数据值（需确认是 pedestrian/PEDESTRIAN/行人）
5. **cte_analysis 模式过于宽泛**：当前只有占位模板，无法引导LLM生成精确CTE
6. **生产模板(templates.jsonl)中的 `* 1e9` 尚未修复**：历史遗留问题，同事写的模板可能也有此bug

### 0616数据集测试结果（20场景，v0.1-alpha 硬修正后）
- 20/20 SQL正确且有结果（含硬修正兜底）
- 0/20 时间戳转换残留
- 复杂CTE场景（#17）SQL质量仍需改进（虽能返回结果，但非精确轨迹交叉计算）

## [2026-06-09] SQLite 批量模式取消 DB 数量限制 + 结果数量输入优化 + 修复可视化连接泄漏

**Commit**: `待提交` — fix: SQLite 批量查询不再限制 DB 数量；结果数量支持无限制；修复可视化后连接卡死

### 变更内容
- **① agent_engine.py — SQLite 批量查询取消 `db_limit` 限制，改为按需并发**
  - `_query_batch()` 不再截断 `db_files[:db_limit]`，默认扫描目录下全部 `.db`
  - `db_limit` 参数保留以兼容旧 API，但实际不再生效
  - 改为**按 `max_workers` 批次启动任务**，而非一次性提交所有 DB，避免连接/线程爆炸
  - 收集够 `result_limit` 后立即停止启动新任务；剩余任务 `cancel()` + 2 秒超时清理
- **② agent_engine.py — `result_limit <= 0` 表示不限制结果数量**
  - `_inject_limit()` 在 `limit <= 0` 时不注入 `LIMIT` 子句
  - `_query_batch()` 在 `result_limit <= 0` 时遍历全部 DB，不提前退出
  - `_query_parquet()` 同样支持 `result_limit <= 0` 不限制
- **③ agent_engine.py — bag_path 改为按需解析**
  - 移除「预解析全部 bag_path」步骤，避免 `result_limit` 较小时白白解析剩余 DB
  - 只有真正命中结果的 DB 才调用 `resolver.resolve()` 获取 `bag_id` / `bag_path`
- **③ AgentPanel.jsx — 结果数量输入框体验优化**
  - 移除已无意义的「DB 数量限制」输入框
  - `resultLimit` 改为字符串状态，聚焦时允许清空，方便用户重新输入
  - 仅在 `onBlur`（失焦）且值为空或非法时，才恢复默认值 `100`
  - 新增「不限制结果数量」复选框，选中时后端返回全部匹配结果
- **④ AgentPanel.jsx — 修复多次可视化后「正在加载 bag 信息」卡死**
  - `startVisualization()` 开始时 abort 旧的 bag info SSE 请求和旧的 video stream fetch
  - Topic modal 关闭时自动 abort 未完成的 `/api/bag/info-stream`
  - Player modal 关闭时自动 abort 未完成的 `/api/video/stream-hevc`
  - MSE 播放 effect 内使用 `AbortController` 发起 fetch，cleanup 时 `abort()` 彻底释放底层 TCP 连接
- **⑤ video_extractor.py — stream finally 块增加超时保护**
  - 避免客户端断开后，`feeder.join()` / `process.wait()` 无限等待占用线程池
  - `feeder.join(timeout=5)`，仍未结束则关闭 stdin 再 join(timeout=2)
  - `process.terminate()` + `wait(timeout=5)`，超时则 `process.kill()`
- **⑥ 后端日志机制增强**
  - `config.py` 新增 `LOG_DIR`、`LOG_FILE_MAX_BYTES`、`LOG_FILE_BACKUP_COUNT` 配置
  - `logging.py` 在原有 stdout 日志基础上，增加 `RotatingFileHandler` 写入 `logs/app.log`
  - 默认单文件 50MB，保留 7 个备份，自动轮转
  - `main.py` 新增 HTTP 请求中间件，记录每个接口的 `method | path | status | duration`
  - 状态码 >= 500 或耗时 > 2s 的请求自动记为 WARNING，方便定位卡死/慢请求
- **⑦ agent_engine.py — 查询结果字段精简**
  - SQLite 模式下 `bag_id` 改为直接使用 `.db` 文件名（去掉后缀），不再调用 resolver 倒查原始 bag id
  - 返回结果中移除 `db_file` 和 `bag_path` 字段
  - `bag_id` 始终放在返回字典的第一列，表格最左侧显示
  - Parquet 模式同样只保留 `bag_id`，移除 `bag_path`/`db_file` 注入
- **⑧ AgentPanel.jsx — SQL 执行进度弹窗**
  - 点击「执行 SQL」后弹出进度窗口，显示已耗时秒数
  - 进度条随时间增长；超过 5 秒提示"执行时间较长，请耐心等待"
  - 超过 15 秒提示"可能已卡住，请检查后端日志"
  - 提供「取消执行」按钮，可中断当前请求
- **⑨ 修复连续播放几个包后再次播放卡住**
  - `AgentPanel.jsx`：`startVisualization()` 开头强制关闭旧播放器弹窗并清空 video rows，避免多个 video stream 连接并发占满浏览器连接槽
  - `video_extractor.py`：`extract_topic_hevc_stream()` 改为真正流式：边读 bag 边喂给 ffmpeg，不再先把所有帧读入内存
  - 客户端断开时，ffmpeg 会更快结束，后端线程更快释放
- **⑩ AgentPanel.jsx — 可视化按钮列固定在最右侧**
  - Action 列使用 `position: sticky; right: 0` 固定
  - 表格横向滚动时，「播包可视化」按钮始终可见，无需拖到底部
- **⑪ 修复播包可视化卡死（全局串行锁 + 非 daemon 线程 + SQL 弹窗状态）**
  - `video_extractor.py`：stream 的 feed 线程改为 daemon，并增加 `threading.Event` 停止标志
  - 客户端断开后，立即设置停止标志、关闭 stdin、kill ffmpeg， feeder 在后台自行结束
  - `finally` 块最多阻塞 0.5~1 秒，避免 FastAPI 线程池被长时间占用
  - `AgentPanel.jsx`：当 topic/player modal 打开时，禁用表格中的「播包可视化」按钮，避免并发点击
  - `AgentPanel.jsx`：MSE cleanup 全面加固：`video.pause()` / `removeAttribute('src')` / `load()` / `sourceBuffer.abort()` / `mediaSource.endOfStream()` / `streamController.abort()` / `reader.cancel()`
  - `AgentPanel.jsx`：MSE 错误回调增加 `if (aborted) return`，避免组件卸载后 setState
  - `AgentPanel.jsx`：MSE 所有事件监听器（MediaSource / SourceBuffer / Video）在 cleanup 时显式移除
  - `AgentPanel.jsx`：新增 `mseCleanupRef`，`startVisualization()` 可同步调用旧 cleanup，避免旧 TCP 连接延迟释放
  - `video_extractor.py`：改用 producer-consumer 带缓冲模式（60 帧 ≈ 2 秒缓冲），producer 边读 bag 边入队，consumer 写入 ffmpeg stdin
  - 避免全部帧读入内存，同时保证 ffmpeg 有持续输入，播放更流畅
  - `finally` 里清空队列、关闭 stdin、kill ffmpeg，尝试 `reader.close()` 强制 feed 线程退出
  - `finally` 里 `del reader` + `gc.collect()`，尽可能释放 gsbag reader 资源
  - `AgentPanel.jsx`：新增「播包可视化」按钮冷却机制，播放器关闭后 1.5 秒内按钮禁用，避免旧资源未释放时快速切换
  - `AgentPanel.jsx`：`startVisualization()` 同步 cleanup 后等待 300ms 再发请求，给浏览器/后端释放连接的时间
  - `AgentPanel.jsx`：SQL 执行弹窗增加多级状态：`pending` / `slow`（>5s 未响应） / `stuck`（>15s 未响应） / `loading_body`（已响应） / `error`
  - 用户可直观区分「后端正在慢查询」和「后端已卡住」
- **⑫ 修复播完第一个包后点第二个马上卡死**
  - `video.py`：新增 `_hevc_stream_lock` 全局串行锁，`/api/video/stream-hevc` 同时只允许一个 stream 在处理
  - `_locked_generator()` 用 `with _hevc_stream_lock:` 包裹 `extract_topic_hevc_stream()`，确保上一个 stream 的 finally 完全结束、锁释放后，下一个请求才能开始
  - `video_extractor.py`：feed / writer 线程改为**非 daemon**，finally 块中等待线程退出（最多 2 秒），确保 gsbag reader / ffmpeg 资源完全释放后才释放全局锁
  - 避免多个 gsbag_reader / ffmpeg 并发导致资源冲突和假死
  - `video.py` / `bag.py`：stream-hevc 和 info-stream 响应头增加 `Connection: close`，避免浏览器保持长连接占用连接槽
  - `AgentPanel.jsx`：`startVisualization()` 里 `mseCleanupRef.current()` 加 try-catch，cleanup 抛异常不阻塞新请求
  - `AgentPanel.jsx`：同步 cleanup 后等待时间从 300ms 延长到 800ms，给 TCP 完全释放留时间
  - `AgentPanel.jsx`：MSE cleanup 移除 `reader.cancel()`，避免未捕获的 `AbortError` promise rejection
  - `AgentPanel.jsx`：新增全局 `unhandledrejection` 监听器，忽略 HEVC 相关的 `AbortError`，防止前端崩溃

### 涉及文件
- `agent/backend/app/services/agent_engine.py`
- `visualizer/frontend/src/components/AgentPanel.jsx`
- `visualizer/backend/app/services/video_extractor.py`

### 测试验证
- ✅ `agent_engine.py` Python 语法检查通过
- ✅ `video_extractor.py` Python 语法检查通过
- ✅ 前端 `npm run build` 编译通过
- ⚠️ 需重新部署后验证：SQLite 大数量限制查询、无限制查询、连续点击多个结果可视化

---

## [2026-06-09] SqlEditor 替换为 CodeMirror 6 + HEVC 流式直传播放（方案 1.5）

**Commit**: `待提交` — feat: SqlEditor 替换为 CodeMirror 6；HEVC流式直传播放

### 变更内容
- **① SqlEditor.jsx — 替换为 `@uiw/react-codemirror` (CodeMirror 6)**
  - 彻底移除自制行号 + react-simple-code-editor 方案，该方案存在滚动条缺失、行号同步错位、工具栏无法固定三大问题
  - CodeMirror 6 原生支持行号、SQL 语法高亮、垂直/水平滚动、Tab 缩进、快捷键映射
  - 新增依赖：`@uiw/react-codemirror`、`@codemirror/lang-sql`、`@codemirror/view`、`@codemirror/state`
  - 工具栏使用 `position: sticky` 固定在编辑器顶部，滚动时不消失
  - 状态栏保留（行数统计 | DuckDB | UTF-8）
  - Ctrl+Enter 快捷键绑定到执行回调
- **② video_extractor.py — 新增 `extract_topic_hevc_stream()` 生成器**
  - 复用原有 rosbag 读取逻辑（`gsbag_reader` → `deserialize_image` → HEVC payload）
  - ffmpeg 只做 remux，**不解码、不重编码**：`-c:v copy -movflags frag_keyframe+empty_moov+default_base_moof -f mp4 pipe:1`
  - 通过 `subprocess.PIPE` + `threading` 边写 stdin 边读 stdout，**不生成任何本地 MP4 文件**
  - chunk 大小 256KB，避免 init segment 被截断导致 MSE 解析失败
- **③ video.py — 新增 `GET /api/video/stream-hevc` StreamingResponse 端点**
  - 参数：`bag_path`, `topic`, `start_ts`, `end_ts`, `fps`
  - 直接流式传输 fMP4 chunk，无后台任务、无文件写入
- **④ AgentPanel.jsx — HEVC MSE 流式播放 + H.264 自动降级**
  - 点击"确认提取"时检测浏览器 HEVC 支持：`MediaSource.isTypeSupported('video/mp4; codecs="hvc1.1.6.L120.B0"')`
  - **支持**：直接打开播放器弹窗，创建 `MediaSource` → `SourceBuffer`，`fetch` 流式接口边收边 `appendBuffer`
  - **不支持**：alert 明确提示后自动降级到 `/api/video/extract` H.264 后台转码
  - MSE 初始化时预设 `mediaSource.duration = (endTs - startTs) / 1e9`，进度条从第一秒起稳定
  - 播放器弹窗顶部显示模式标签：🟢 "HEVC 直传" / 🟠 "H.264 转码"
  - MSE 报错时显示红色错误卡片 + "🔄 改用 H.264 转码重试"按钮
  - Topic 选择弹窗新增 "⚙️ 强制 H.264 转码" 复选框，方便调试时绕过自动检测
  - Console 输出 `[HEVC诊断]` 详细日志（canPlayType、MSE 支持、错误码、mediaSourceState 等）
- **⑤ App.js — Bag Loader 同步支持 HEVC 直传**
  - `extractVideo()` 复用同样的 HEVC 检测 + MSE 逻辑
  - 视频时长由 `message_count / freq` 预计算，无需等流式传输完成
  - 播放区域条件渲染：H.264 用 `<video src>`，HEVC 用 `<video ref>` + MSE
- **⑥ MSE 兼容性关键修复**
  - `+default_base_moof`：解决 Chrome MSE `TFHD base-data-offset not allowed by MSE` 错误
  - 去掉 `sourceBuffer.mode = 'sequence'`（改为默认 `segments` 模式），避免 fMP4 fragment 时间戳冲突

### 涉及文件
- `visualizer/frontend/src/components/SqlEditor.jsx`
- `visualizer/backend/app/services/video_extractor.py`
- `visualizer/backend/app/api/video.py`
- `visualizer/frontend/src/components/AgentPanel.jsx`
- `visualizer/frontend/src/App.js`
- `visualizer/frontend/package.json` / `package-lock.json`

### 测试验证
- ✅ HEVC 直传在 Safari / Chrome(Mac) 正常播放
- ✅ 不支持的浏览器自动降级 H.264
- ✅ 强制 H.264 复选框工作正常
- ✅ 进度条从打开起稳定，不跳动
- ✅ 后端 ffmpeg stderr 日志输出正常
- ✅ 前端 build 编译通过
- **② AgentPanel.jsx — CodeMirror 6 文本居中修复**
  - 覆盖 `.App { text-align: center }` 全局样式，确保 SQL 代码左对齐
- **③ agent_engine.py — `_validate_sql()` 去掉注释后再判断语句类型**
  - 原逻辑直接对原始 SQL 做 `strip().startswith("WITH")`，导致以 `-- 注释` 开头的 CTE 查询被误判为非 SELECT
  - 现先通过正则去掉 `--` 行注释和 `/* */` 块注释，再判断首词是否为 SELECT/WITH
- **④ agent_engine.py — `_ensure_bag_id_in_select()` 升级为 AST 注入（sqlglot）**
  - 原正则注入无法处理 CTE 链（如 `WITH a AS (...) b AS (...) SELECT ...`），导致 bag_id 列找不到或错位
  - 现使用 `sqlglot` 解析 AST，对**每个 CTE 的 SELECT** 和**最外层 SELECT** 都注入 `bag_id`
  - 同时自动把 `bag_id` 追加到 `GROUP BY` 列表，避免语法错误
  - 如果 AST 解析失败，回退到原始 SQL（安全降级）
  - 新增依赖：`sqlglot==30.10.0`
- ⚠️ Windows Chrome 的 HEVC 硬件解码支持因系统而异

---

## [2026-06-09] HEVC 流式直传播放（方案 1.5）

**Commit**: `8e82165` — feat: HEVC流式直传播放 — 服务器不解码，ffmpeg remux为fMP4，前端MSE播放

### 变更内容
- **① video_extractor.py — 新增 `extract_topic_hevc_stream()` 生成器**
  - 复用原有 rosbag 读取逻辑（`gsbag_reader` → `deserialize_image` → HEVC payload）
  - ffmpeg 只做 remux，**不解码、不重编码**：`-c:v copy -movflags frag_keyframe+empty_moov+default_base_moof -f mp4 pipe:1`
  - 通过 `subprocess.PIPE` + `threading` 边写 stdin 边读 stdout，**不生成任何本地 MP4 文件**
  - chunk 大小 256KB，避免 init segment 被截断导致 MSE 解析失败
- **② video.py — 新增 `GET /api/video/stream-hevc` StreamingResponse 端点**
  - 参数：`bag_path`, `topic`, `start_ts`, `end_ts`, `fps`
  - 直接流式传输 fMP4 chunk，无后台任务、无文件写入
- **③ AgentPanel.jsx — HEVC MSE 流式播放 + H.264 自动降级**
  - 点击"确认提取"时检测浏览器 HEVC 支持：`MediaSource.isTypeSupported('video/mp4; codecs="hvc1.1.6.L120.B0"')`
  - **支持**：直接打开播放器弹窗，创建 `MediaSource` → `SourceBuffer`，`fetch` 流式接口边收边 `appendBuffer`
  - **不支持**：alert 明确提示后自动降级到 `/api/video/extract` H.264 后台转码
  - MSE 初始化时预设 `mediaSource.duration = (endTs - startTs) / 1e9`，进度条从第一秒起稳定
  - 播放器弹窗顶部显示模式标签：🟢 "HEVC 直传" / 🟠 "H.264 转码"
  - MSE 报错时显示红色错误卡片 + "🔄 改用 H.264 转码重试"按钮
  - Topic 选择弹窗新增 "⚙️ 强制 H.264 转码" 复选框，方便调试时绕过自动检测
  - Console 输出 `[HEVC诊断]` 详细日志（canPlayType、MSE 支持、错误码、mediaSourceState 等）
- **④ App.js — Bag Loader 同步支持 HEVC 直传**
  - `extractVideo()` 复用同样的 HEVC 检测 + MSE 逻辑
  - 视频时长由 `message_count / freq` 预计算，无需等流式传输完成
  - 播放区域条件渲染：H.264 用 `<video src>`，HEVC 用 `<video ref>` + MSE
- **⑤ MSE 兼容性关键修复**
  - `+default_base_moof`：解决 Chrome MSE `TFHD base-data-offset not allowed by MSE` 错误
  - 去掉 `sourceBuffer.mode = 'sequence'`（改为默认 `segments` 模式），避免 fMP4 fragment 时间戳冲突

### 涉及文件
- `visualizer/backend/app/services/video_extractor.py`
- `visualizer/backend/app/api/video.py`
- `visualizer/frontend/src/components/AgentPanel.jsx`
- `visualizer/frontend/src/App.js`

### 测试验证
- ✅ HEVC 直传在 Safari / Chrome(Mac) 正常播放
- ✅ 不支持的浏览器自动降级 H.264
- ✅ 强制 H.264 复选框工作正常
- ✅ 进度条从打开起稳定，不跳动
- ✅ 后端 ffmpeg stderr 日志输出正常
- ✅ 前端 build 编译通过
- ⚠️ Windows Chrome 的 HEVC 硬件解码支持因系统而异

---

## [2026-06-04] DuckDB 方言适配 + 前端UX改进

**Commit**: 待提交 — feat: DuckDB方言适配 + CTE放行 + 前端关闭按钮 + topic记忆

### 变更内容
- **① agent_engine.py — `_adapt_sql_for_duckdb()` + `_execute_parquet()` 入口调用**
  运行时自动适配，即使 LLM 或用户手写的 SQL 用了 SQLite 方言也能正确执行：
  - `has_xxx = 1` → `has_xxx = true`（boolean 列命名模式匹配）
  - `has_xxx = 0` → `has_xxx = false`
  - `group_concat` → `string_agg`
  - `strftime('%Y', col)` → `strftime(col, '%Y')`（参数顺序交换）
  - `json_extract` → `json_extract_string`（DuckDB json_extract 返回 JSON 类型，比较时右值被当 JSON 解析报错 Malformed JSON）
  - `speed = 1` 这类普通数值比较不受影响
- **④ agent_engine.py — `_validate_sql()` 放行 WITH CTE**
  原逻辑只允许 `SELECT` 开头，导致 `WITH ... SELECT` 形式的 CTE 查询被拒。现允许 `WITH` 开头。
- **② tag_router.py — `build_prompt()` 新增 `query_mode` 参数**
  Parquet 模式下自动在 system prompt 追加 DuckDB 方言提示，让 LLM 从源头就生成正确语法，减少适配层的触发
- **③ agent.py — 两处 `build_prompt` 调用传入 `query_mode=engine.query_mode`**
  流式和非流式生成 SQL 的接口都会收到正确的方言提示
- **⑤ AgentPanel.jsx — 弹窗关闭机制**
  Topic选择弹窗添加 ✕ 关闭按钮（加载/错误状态均可关闭）+ 点击遮罩层关闭
- **⑥ AgentPanel.jsx — 可视化topic记忆**
  用户选择的topic存入 `localStorage('lastSelectedTopic')`，下次打开弹窗时自动回填
  若记忆的topic不在当前可用列表中则回退到第一个
- **⑦ AgentPanel.jsx — 视频提取进度弹窗替代底部堆积面板**
  删除底部播包可视化堆积面板。选择topic确认后弹出进度弹窗（显示排队/提取百分比/完成），
  提取完成自动切换到视频播放弹窗，关闭播放弹窗自动清除记录不再堆积
  提取失败时弹窗显示错误信息，可关闭
- **⑧ AgentPanel.jsx — 搜索结果双向滚动条**
  结果表格顶部和底部各有一个水平滚动条，同步滚动，解决宽表只能底部滚动的痛点

### 涉及文件
- `agent/backend/app/services/agent_engine.py`
- `agent/backend/app/core/tag_router.py`
- `visualizer/backend/app/api/agent.py`
- `visualizer/frontend/src/components/AgentPanel.jsx`

### 测试验证
- ✅ `_adapt_sql_for_duckdb()` 单元测试：boolean列、group_concat、strftime参数交换、json_extract
- ✅ 前端 build 编译通过
- ⚠️ 端到端测试待 DSW 环境 Parquet 数据验证

---

## [2026-05-27] 客户端分页重构

**Commit**: `3380ddf` — feat: 截断提示；`ca88f13` — refactor: 分页改为客户端缓存模式

### 变更内容
- **分页架构重构**：从服务端分页（每次翻页重新执行 SQL）改为客户端缓存分页
  - 前端新增 `allRows` state 缓存全量查询结果
  - 翻页纯 `allRows.slice()`，不发请求
  - `totalRows = allRows.length`
  - `displayRows = allRows.slice((page-1)*pageSize, page*pageSize)`
- **buildPayload 调整**：固定 `page: 1, page_size: result_limit`，取回全量数据
- **handleExecuteSql 简化**：不再接受 `targetPage` 参数，执行后缓存全量数据到 `allRows`
- **handleSubmit/handleSubmitStream**：结果写入 `allRows`，`setTotalRows(allRows.length)`
- **截断提示**：当 `totalRows >= resultLimit` 时显示 "may be truncated, increase result_limit"
- **翻页回调**：`onPageChange={(p) => setPage(p)}`，纯状态切换

### 涉及文件
- `visualizer/frontend/src/components/AgentPanel.jsx`

### 测试验证
- ✅ 纯逻辑测试：8 个分页切片场景（首页/中间页/末页/越界/不同 pageSize/空数据）
- ✅ 后端 `_paginate_rows` 验证：`page_size=result_limit` 时返回全量数据
- ✅ 前端 build 编译通过
- ⚠️ 本机端到端 API 测试跳过（缺 dm_sdk）

---

## [2026-05-27] 分页翻页功能修复

**Commit**: `1afdcc4` — fix: 分页翻页功能修复

### 变更内容
- 修复 `onPageChange` 闭包陷阱：`setPage(p)` 后立即调 `handleSubmit()` 会读到旧 `page` 值
- 修复翻页走 LLM 流程的问题：翻页应调 `handleExecuteSql` 而非 `handleSubmit`
- `handleExecuteSql` 添加 `totalRows` 和 `page` 状态回填
- 新查询时 `setPage(1)` 重置分页

### 涉及文件
- `visualizer/frontend/src/components/AgentPanel.jsx`

---

## [2026-05-22] f8 deploy.sh --force 逻辑修复

**Commit**: `dcd5d13`

### 变更内容
- `deploy.sh` 的 `--force` 标志接入 PID 检查逻辑

### 涉及文件
- `deploy.sh` → `sync-dev.sh`

---

## [2026-05-22] DSW 前端 bug 修复

### 变更内容
- AgentPanel.jsx 三元表达式语法错误修复
- JSX 容器未关闭 `)` 修复
- 未定义变量 `selectedDb`/`selectedBatch` → 改为 `dbPath`/`batchId`

### 涉及文件
- `visualizer/frontend/src/components/AgentPanel.jsx`

---

## [2026-05-22] UI 修改 — SQL 编辑器 + 分页控件

### 变更内容
- SqlEditor.jsx 改 light 主题
- AgentPanel.jsx 添加 pageSize 参数框(默认20，范围5-500)
- 新增 PaginationControls 组件(首页/尾页固定、当前页±2邻居、省略号跳转)

### 涉及文件
- `visualizer/frontend/src/components/SqlEditor.jsx`
- `visualizer/frontend/src/components/AgentPanel.jsx`

---

## [2026-05-22] gsbag 降级处理

### 变更内容
- bag_parser.py + video_extractor.py 添加 gsbag try/except 降级
- 本机缺 gsbag 时优雅降级，不影响其他功能

### 涉及文件
- `visualizer/backend/app/services/bag_parser.py`
- `visualizer/backend/app/services/video_extractor.py`

---

## [2026-05-22] 本机环境搭建

### 变更内容
- .venv (Python 3.10) 配置
- .env 配置(parquet数据路径、DeepSeek API、SOCKS代理)
- 本机端口从 30001 改为 30002（避免与 SSH 隧道冲突）

### 涉及文件
- `.env`

## [2026-06-23] Schema Dictionary 增强：DR Trajectory 列描述

- **修改**: `schema_dictionary.yaml` 中 `ego.ego_dr_trajectory` 和 `dynamic_obj.obs_dr_trajectory` 描述增强
- **新增**: DR trajectory JSON 结构说明 (`{x:[5], y:[5], theta:[5], speed:[5], exists:[5]}`)
- **新增**: 漂移值计算方法 (`json_extract` 提取 `$.x[4]-$.x[0]`)
- **修复**: 防止LLM生成不存在的 `dr_trajectory_drift` / `obs_dr_trajectory_drift` 列名
- **测试**: 0616数据集 19/20 通过（#20 DR轨迹漂移待此修复后验证）
