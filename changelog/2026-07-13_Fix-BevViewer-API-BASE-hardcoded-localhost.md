## [2026-07-13] Fix BevViewer API_BASE hardcoded localhost:8000

**Commit**: 待提交

### 修复

1. **BevViewer.jsx `API_BASE` 硬编码 localhost:8000**
   - 原代码：`const API_BASE = 'http://localhost:8000';`
   - DSW 后端跑在 30001，前端通过浏览器访问时请求打到了不存在的 localhost:8000
   - 修复：改为 `process.env.REACT_APP_API_BASE || ''`（与其他组件一致，空字符串=同源）

### 涉及文件

- `visualizer/frontend/src/components/BevViewer.jsx` — API_BASE 改为环境变量

### 测试验证

- ✅ 后端 `/api/bag/fusion-map-info` 返回 200 + exists:true（已验证）
- ⚠️ 前端 E2E 待部署后验证
