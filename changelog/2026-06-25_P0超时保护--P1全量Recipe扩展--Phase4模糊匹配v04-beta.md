## [2026-06-25] P0超时保护 + P1全量Recipe扩展 + Phase4模糊匹配（v0.4-beta）

**Commit**: 67ce256 (P1 batch) + 7d647fb (Phase4)
**测试报告**: `test_reports/v0.4-beta-test-report.md`

### 新增

1. **查询超时保护**（`agent_engine.py`）
   - `DB_QUERY_TIMEOUT`（默认5s）— 单 DB SQLite 连接超时
   - `BATCH_TIMEOUT`（默认180s）— 整体批量查询超时，超时后返回已收集的结果 + 提示信息
   - 可通过环境变量 `DB_QUERY_TIMEOUT` / `BATCH_TIMEOUT` 配置

2. **LLM SQL start_ts/end_ts 校验**（`agent_engine.py`）
   - `_check_start_end_ts()` 方法：dry-run 通过后检查 SQL 是否包含 start_ts 和 end_ts
   - 仅对 sql_source="llm" 的 SQL 校验（Recipe SQL 已全量验证过）
   - 缺失时视为语法错误，触发 Round 3 LLM 纠错循环

3. **53 个新 raw_sql Recipe YAML**（`scripts/extract_recipes.py` 自动提取）
   - 从 60 个 db_py_rule .py 文件中自动提取 SQL → 生成 Recipe YAML
   - 自动校验：长度匹配、结尾分号、SQLite 兼容性（`->>`/`json_extract_string`/`EXTRACT`/`ILIKE`/`TRUE`/`FALSE`）
   - Recipe 总数：15 → 68（63 raw_sql + 5 Block 组装）

4. **CONCEPT_RECIPE_MAP 扩展**（`concept_router.py`）
   - 中文概念 key：14 → 72 个
   - 覆盖路口横穿/行人横穿/横穿冲突/左转/右转/掉头/匝道/环岛/限速/车道宽度/红绿灯状态等 45+ 新场景

5. **Phase4 模糊匹配兜底**（`concept_router.py`）
   - 当 Phase 1/2/3 全部未命中时，LCS 最长公共子串占比匹配
   - 停用词过滤 + key+recipe_name 双源匹配
   - 阈值 0.4，例如"车辆掉头"→"掉头1"、"行人过马路"→"行人横穿"

6. **6 个 Recipe 端到端验证通过**（含 start_ts/end_ts）
   - `redlight_slowmoving`（红灯缓行）— 36 rows
   - `reversing`（倒车避障）— 36 rows
   - `ego_decel_during_lanechange`（变道减速）— 100 rows
   - `ego_overtake_catin_truck`（超车后卡车切入）— 100 rows
   - `truck_safe_cutin_ego`（卡车安全切入）— 100 rows
   - `nudge_borrowlane`（借道避让）— 92 rows（修复截断后重测通过）

### 修复

1. **nudge_borrowlane Recipe SQL 截断** — 从 14769→15097 字符，重新提取完整 SQL
2. **53个 Recipe YAML 格式修复** — `raw_sql: |` 缩进不对导致 yaml.safe_load 失败，改用 yaml.dump 重新生成
3. **greenLight_abnormalbrake + obstacle_avoidance YAML 格式修复** — 同上

### 涉及文件

- `agent/backend/app/services/agent_engine.py` — 超时保护 + start_ts/end_ts 校验
- `agent/backend/app/core/concept_router.py` — CONCEPT_RECIPE_MAP 扩展 + Phase4 LCS 模糊匹配 + Recipe description 加载
- `agent/backend/app/core/recipes/` — 53 个新 YAML + 2 个格式修复
- `scripts/extract_recipes.py` — 自动提取脚本（新增）

### 测试验证

- DSW 端到端测试：左转/下匝道/环岛/闯红灯/车道宽度/车辆掉头 — 全部 sql_source=recipe, start_ts+end_ts ✅
- Phase4 模糊匹配本地验证：6/6 测试用例正确匹配
- 详见 `test_reports/v0.4-beta-test-report.md`
