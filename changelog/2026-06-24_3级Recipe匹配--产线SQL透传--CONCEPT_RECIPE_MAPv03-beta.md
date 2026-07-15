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
