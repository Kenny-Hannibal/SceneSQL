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
