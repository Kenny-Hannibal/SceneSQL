# 验证集可视化 + Mage-VL 评测 API

**日期**: 2026-08-09
**涉及文件**:
- `visualizer/backend/app/api/mage_vl.py` (新增)
- `visualizer/backend/app/main.py` (注册路由)
- `visualizer/frontend/src/components/AgentPanel.jsx` (验证集弹窗 + 按钮)

## 变更内容

### Feature 1: 验证集可视化 (验证集列表 + 可视化 + 覆盖标注)

- 策略列表面板每条策略新增 **「验证集」** 按钮（青色 #13c2c2）
- 点击后弹出验证集列表，显示：
  - Bag ID
  - 时间范围 (start_ts ~ end_ts)
  - 标注状态 (✅ 通过 / ❌ 不通过)
  - 操作按钮
- **「📹 可视化」** 按钮 → 复用 `startVisualization` 打开播放器
- **「✅」/「❌」** 按钮 → 覆盖标注（调 `POST /api/eval-labels`）
- 列表顶部显示统计：共 N 条标注（通过 X，不通过 Y）

### Feature 2: Mage-VL 评测 API (流式 MP4 传输)

- 新增 `POST /api/mage-vl/evaluate` 端点
- 流程：
  1. 接收 `bag_id` + `start_ts` + `end_ts`（+ 可选 topic / prompt）
  2. 解析 bag 本地路径（dm_sdk / 本地 / OSS 下载）
  3. 调用 `extract_topic_to_mp4` 提取 H.264 mp4 到临时文件
  4. 读取 mp4 bytes → base64 编码
  5. POST 到 Mage-VL 服务 (`http://localhost:31000/v1/chat/completions`)
  6. 返回评测结果
- 新增 `GET /api/mage-vl/health` 端点检查服务状态
- 环境变量配置：`MAGE_VL_BASE_URL` (默认 `http://localhost:31000`), `MAGE_VL_TIMEOUT` (默认 120s)

## 测试验证

- [x] `py_compile` 验证通过
- [x] 前端 `npm run build` 通过
- [ ] DSW 部署 + 前端 E2E 测试
