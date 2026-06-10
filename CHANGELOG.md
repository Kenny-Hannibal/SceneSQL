# SceneSQL — 功能变更日志

> 每次功能修改 commit 后，在此记录变更内容，方便回溯。

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
