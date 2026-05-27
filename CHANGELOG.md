# SceneSQL — 功能变更日志

> 每次功能修改 commit 后，在此记录变更内容，方便回溯。

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
