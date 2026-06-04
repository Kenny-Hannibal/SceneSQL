# SceneSQL — 功能变更日志

> 每次功能修改 commit 后，在此记录变更内容，方便回溯。

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
