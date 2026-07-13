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
