# SceneSQL / Rosbag Visualizer — Agent 工作指令

## 项目背景

- **项目名**: SceneSQL（Rosbag 多相机可视化 + NL2SQL Agent）
- **技术栈**: FastAPI 后端 + React 前端，替换旧版 Gradio 单体架构
- **核心功能**: 通过自然语言查询 rosbag 数据，可视化播包（HEVC 视频流式播放）
- **关键目录**:
  - `visualizer/backend/` — FastAPI 后端（bag 解析、视频提取、Agent API）
  - `visualizer/frontend/` — React 前端（AgentPanel、VideoPlayer、App.js）
  - `agent/backend/` — NL2SQL 引擎（SQLite / Parquet / DuckDB）
  - `tools/` — 共享 HEVC 解码工具

## ⚠️ 部署目标（务必看清）

- **统一部署到「大写 `DSW`」**：`ssh DSW` → `8.130.209.216:1025`，仓库路径 `/root/data/text2sql`
- **「小写 `dsw`」（`8.130.175.37:1021`）已废弃，不再部署、不再验证**
- 两台是不同机器；ssh 别名大小写敏感。每次部署/端到端测试一律用大写 `DSW`。
- 部署命令：`ssh DSW "cd /root/data/text2sql && git pull --ff-only && bash visualizer/deploy.sh -f"`

## 工作流（必须遵守）

### 1. 修改代码前 — 阅读历史
**每次开始代码修改前，必须先阅读 `CHANGELOG.md`**：
- 了解近期改了什么
- 避免重复造轮子或与近期改动冲突
- 掌握当前已知问题和待验证项（⚠️ 标记）

### 2. 修改代码 — 最小改动
- 只做用户要求的事，不要过度设计
- 复用现有逻辑，保持代码风格一致
- 修改后必须做语法验证：`python -m py_compile`（后端）、`babel` 或 `eslint`（前端）

### 3. 测试验证 — 用户确认
- 修改完成后，向用户说明改动内容，等待用户测试
- **禁止在用户未确认测试通过前直接 push 到远程**

### 4. 推送前 — 同步更新 CHANGELOG
**用户确认测试通过后，执行推送前必须：**
1. 在 `CHANGELOG.md` **顶部**新增条目（最新日期在前）
2. 条目格式严格遵循已有格式：
   - 标题：`## [YYYY-MM-DD] 简短描述`
   - Commit 行、变更内容（带编号）、涉及文件、测试验证
3. **CHANGELOG 的修改可以和代码放在同一个 commit，也可以单独一个 commit，但必须在 push 前完成**

### 5. 执行推送
```bash
git add -A
git commit -m "type: 描述"
git push origin master
```

## 编码规范

- **后端**: Python，遵循 PEP 8，已有代码用单引号字符串则保持单引号
- **前端**: React 函数组件，使用单引号字符串，缩进 2 空格
- **注释**: 关键逻辑必须加中文注释，特别是 HEVC/MSE 相关代码
- **错误处理**: 前端 MSE 错误必须输出 `[HEVC诊断]` 日志；后端 ffmpeg 错误必须打印 stderr

## 视频播放相关约束

- **HEVC 直传**是首选路径（`+default_base_moof` + `empty_moov`）
- **H.264 转码**作为 fallback 必须保留
- 任何 MSE / ffmpeg 修改后，必须在 CHANGELOG 测试验证项中注明浏览器兼容性测试结果

## 标签开发交接知识库（2026-08-31）

接到标签开发 / 策略 / 评测集 / Spark 打标 / schema 相关任务时：

1. **总交接手册**：`/data/var/workspace/projects/projects/docs/gac/LLM标签开发交接手册.md`（Schema 查看与更新、链路A: SQL→策略→评测集、链路B: Spark 批量打标→转数据集、18 条实证坑、交付纪律、fact_store 嵌入说明）
2. **深度知识库**：`docs/scene_tag_sql_dev_guide.md`（坑的完整版含案例与排查套路）
3. **fact_store**：前任积累的记忆已通过 `docs/knowledge_base/import_fact_store.py`
   导入本机 `/root/.hermes/memory_store.db`（若未导入先跑一次，幂等）。
   查询模板（中文必须用 LIKE）：
   ```bash
   python3 -c "
   import sqlite3
   c = sqlite3.connect('file:/root/.hermes/memory_store.db?mode=ro', uri=True).cursor()
   for r in c.execute(\"SELECT content FROM facts WHERE content LIKE '%关键词%'\"):
       print(r[0], '\n---')
   "
   ```
