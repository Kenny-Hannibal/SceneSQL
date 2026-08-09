# SceneSQL V4 Agent Loop — 架构设计文档

> 状态：已实现（生产环境运行中）  
> 最后更新：2026-07-23  
> 前置文档：ARCHITECTURE.md (V1), ARCHITECTURE_V3_FEATURES.md (V3规划)  
> 本文档：V4聚焦**Agent Loop已实现的完整链路**，从NL输入到SQL结果的端到端数据流  
> 代码基准：`agent/backend/app/` 目录

---

## CHANGELOG 索引

| 日期 | 章节 | 变更 |
|------|------|------|
| 2026-07-22 | 全文 | 初始版本 |
| 2026-07-23 | §3-§7 | 补充Block/Recipe数据结构、向量路由完整流程、执行层代码级细节、错误处理矩阵 |

---

## 1. 概述

### 1.1 V4定位

V1定义了基础架构（分层Schema注入），V3规划了6个功能方向。V4是**现状文档**——记录从V3规划到生产落地后的实际实现状态，聚焦核心Agent Loop。

### 1.2 核心设计理念

**弱模型 + 强约束 + 多层路由**：85/92个recipe是raw_sql直通（查表搬运），不是AI生成SQL。LLM只负责少数Fallback场景。系统设计确保：即使LLM完全不可用，85%+的查询仍能返回结果。

### 1.3 Agent Loop全貌

```
用户 NL 输入
    │
    ▼
┌─────────────────────────────────────────────┐
│ Round 1: 概念识别 (ConceptRouter)            │
│   Phase 1: CONCEPT_RECIPE_MAP keyword命中    │
│   Phase 2: Compound concept分解              │
│   Phase 3: 用户策略覆盖                       │
│   Phase 4a: BGE-M3向量语义搜索               │
│   Phase 4: n-gram模糊匹配兜底                │
│   → 输出: {concepts, composition, recipe?}   │
└──────────────┬──────────────────────────────┘
               │
       ┌───────┴───────┐
       ▼               ▼
  recipe命中?      无recipe
       │               │
       ▼               ▼
  Layer1/2:        Layer3: Hybrid
  BlockAssembler   (已知block+LLM胶水CTE)
       │               │
       └───────┬───────┘
               ▼ 无SQL
        Fallback: Round2 LLM生成
               │
               ▼
        ┌──────────────┐
        │ 纠错循环       │
        │ dry-run验证    │
        │ → 失败→correction│
        │ → 重试(最多3轮) │
        └──────┬───────┘
               │ 通过
               ▼
        ┌──────────────┐
        │ 执行层         │
        │ SQLite批量查询  │
        │ bag_id注入      │
        │ 结果聚合+分页    │
        └──────────────┘
```

---

## 2. Round 1: 概念识别层

### 2.1 ConceptRouter 完整路由流程

入口：`concept_router.py` → `ConceptRouter.parse_round1_output(raw, nl)`

5个Phase按优先级顺序执行，任何一个Phase命中后标记`_routed=True`，后续Phase跳过。

#### 2.1.1 LLM Round 1 输出解析

Round 1的LLM返回JSON：
```json
{
  "concepts": ["大曲率道路"],
  "composition": "single_tag",
  "recipe": "",
  "recipe_variant": "",
  "required_blocks": [],
  "ego_fields": [],
  "need_dynamic_obj": false,
  "need_dynamic_lane": false,
  "need_intersection_info": false
}
```

`parse_round1_output()`先JSON解析，如果失败则用正则提取`{...}`块。解析后进入5层路由。

#### 2.1.2 Phase 1: CONCEPT_RECIPE_MAP keyword命中

```python
# concept_router.py CONCEPT_RECIPE_MAP (143条)
CONCEPT_RECIPE_MAP = {
    "切入": ("cutin_analysis", "default"),
    "加塞": ("cutin_analysis", "default"),
    "左转": ("intersection_turn_left_1", "default"),
    "大曲率": ("large_curvature_road", "default"),
    "cutin": ("cutin_analysis", "default"),
    # ... 143 entries
}
```

匹配逻辑：遍历LLM返回的`concepts`列表，逐个在combined_map中精确查找。combined_map = CONCEPT_RECIPE_MAP ∪ user_strategy_map（用户策略优先）。

**复合检测**：如果concepts命中2+个不同recipe → compound场景，设置`composition="hybrid_blocks"`，`required_blocks`由`RECIPE_BLOCK_MAP`推断。

```python
unique_concept_recipes = set(r for _, r, _ in concept_recipes)
if len(unique_concept_recipes) >= 2:
    result["recipe"] = ""
    result["required_blocks"] = self._infer_required_blocks([r for _, r, _ in concept_recipes])
    result["composition"] = "hybrid_blocks"
    result["_routed"] = True
```

#### 2.1.3 Phase 2: 子串匹配

当Phase 1精确匹配失败时，尝试concept是否包含map中的key（或key包含concept）。

```python
# 子串匹配：concept包含map中的key，取最长匹配
sub_matches = [(key, *combined_map[key]) for key in combined_map if key in concept]
if sub_matches:
    sub_matches.sort(key=lambda x: len(x[0]), reverse=True)
    key, recipe, variant = sub_matches[0]
```

#### 2.1.4 Phase 3: NL原文匹配 + 复合场景检测

直接在原始NL输入中搜索keyword，处理用户说"路口左转"但LLM只返回"左转"的情况。

**最长匹配优先**：如果"变道"和"掉头不变道"同时匹配，保留较长的key。

```python
matches = [(key, recipe, variant) for key, (recipe, variant) in combined_map.items() if key in nl]
# 过滤：如果一个key是另一个key的子串，且recipe不同，只保留最长key
filtered = []
for i, (k1, r1, v1) in enumerate(matches):
    is_substring = False
    for j, (k2, r2, v2) in enumerate(matches):
        if i != j and k1 in k2 and len(k1) < len(k2):
            is_substring = True
            break
    if not is_substring:
        filtered.append((k1, r1, v1))
```

复合场景：NL匹配到2+个不同recipe → hybrid路径。

#### 2.1.5 Phase 4a: BGE-M3向量语义搜索

当Phase 1-3都没命中时，用向量搜索。

```python
from .vector_router import search as vector_search, is_available as vector_available, EMBED_MODEL
if vector_available():
    hits = vector_search(nl, top_k=1)
    _vec_threshold = 0.40 if "bge-m3" in (EMBED_MODEL or "").lower() else 0.35
    if hits and hits[0][1] < _vec_threshold:
        recipe_name = hits[0][0]
        # 在 combined_map 中反查 recipe_name → key → (recipe, variant)
        for k, (rn, _v) in combined_map.items():
            if rn == recipe_name:
                matched_key = k
                break
```

**反查机制**：向量搜索返回recipe_name，需要在combined_map中找到对应的keyword才能获取(recipe, variant)。如果recipe_name不在combined_map中，静默跳过。

#### 2.1.6 Phase 4: n-gram模糊匹配兜底

当所有以上Phase都没命中时，用字符级2-gram余弦相似度做模糊匹配。

```python
@staticmethod
def _char_ngrams(text: str, n: int = 2) -> set:
    text = text.strip()
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i+n] for i in range(len(text) - n + 1)}

@classmethod
def _cosine_sim(cls, a: set, b: set) -> float:
    """|交集|/min(|a|,|b|)，对短-长文本对更友好"""
    inter = len(a & b)
    return inter / min(len(a), len(b)) if min(len(a), len(b)) else 0.0
```

阈值0.4：LCS占比 >= 0.4 视为匹配。

### 2.2 Round 1 输出数据结构

```python
r1_result = {
    "concepts": [("大曲率道路", "large_curvature_road", "default")],
    "composition": "single_tag",         # single_tag | multi_tag | tag_join_ego | ... | hybrid_blocks
    "recipe": "large_curvature_road",     # 可选，Phase 1-4 任一命中时填入
    "recipe_variant": "default",          # 可选
    "required_blocks": ["continuous_segment", "obstacle_proximity"],  # compound场景
    "ego_fields": [],                     # 需要的ego表字段
    "need_dynamic_obj": False,            # 是否需要dynamic_obj表
    "need_dynamic_lane": False,
    "need_intersection_info": False,
    "sql_source": "recipe",               # "recipe" | "recipe_vector" | "user_strategy" | "hybrid"
}
```

### 2.3 用户策略 (User Strategies)

用户在`user_strategies/`目录下放YAML文件，定义自己的keyword→recipe映射。优先级高于系统CONCEPT_RECIPE_MAP。

当前8个用户策略：
- Y型路口.yaml、分流.yaml、合流.yaml、直行路口.yaml
- 隧道.yaml、隧道入口.yaml、隧道入口_v2.yaml、隧道出口.yaml

```python
# ConceptRouter.__init__
self._user_strategy_map = {}
self.load_user_strategies()

def load_user_strategies(self):
    """扫描 user_strategies/ 目录，加载keywords → (recipe, variant) 映射"""
    # 用户策略同名时覆盖系统 CONCEPT_RECIPE_MAP
```

---

## 3. SQL生成层

### 3.1 BlockAssembler 架构

`BlockAssembler`是SQL组装引擎，由3个核心类组成：

```
BlockLibrary ← blocks/*.yaml (18个block模板)
RecipeLibrary ← recipes/*.yaml (92个recipe) + user_strategies/*.yaml (8个)
BlockAssembler ← 组装最终SQL
```

#### 3.1.1 BlockLibrary

18个通用CTE block模板，每个定义在`blocks/`目录下的YAML文件：

| Block | 输入表 | 输出列 | 用途 |
|-------|--------|--------|------|
| continuous_segment | ego | start_ts, end_ts, duration, frame_count | 提取满足条件的连续时间段 |
| simple_tag_extraction | range_tag | start_ts, end_ts, tag_name | 提取指定tag的时间段 |
| proximity_analysis | ego, dynamic_obj | start_ts, end_ts, object_id, min_dist_m | 目标接近度分析 |
| target_proximity | ego, dynamic_obj | start_ts, end_ts, object_id, min_dist_m | 目标接近度（简化版） |
| obstacle_proximity | ego, dynamic_obj | start_ts, end_ts, object_id, min_dist_m | 障碍物接近度 |
| ego_speed_simple | ego | start_ts, end_ts, pre_speed_kmh, during_min_speed_kmh | 自车速度分析 |
| ego_speed_analysis | ego | (详细速度指标) | 自车速度详细分析 |
| steering_change_detect | ego | start_ts, end_ts, direction | 转向变化检测 |
| conflict_classification | ego, dynamic_obj | start_ts, end_ts, conflict_type | 冲突分类 |
| close_follow_detail | ego, dynamic_obj | start_ts, end_ts, ttc_s, thw_s | 近跟详情 |
| lane_change_detail | ego, dynamic_obj | start_ts, end_ts, direction | 变道详情 |
| duration_filter | any | start_ts, end_ts | 时间段过滤 |
| ego_field_condition | ego | start_ts, end_ts | ego字段条件筛选 |
| event_extraction | range_tag | start_ts, end_ts, event_type | 事件提取 |
| event_merge | any | start_ts, end_ts | 事件合并 |
| tag_gap_merge | range_tag | start_ts, end_ts | 标签间隙合并 |
| time_calc | any | start_ts, end_ts, duration | 时间计算 |
| static_link_segment | ego, intersection_info | start_ts, end_ts, link_id | 静态link分段 |

**Block YAML 结构示例**（`blocks/continuous_segment.yaml`）：

```yaml
name: continuous_segment
version: "1.0"
description: |
  从ego表提取满足where_condition的连续时间段。使用ROW_NUMBER间隙与孤岛技术。

input_tables:
  - ego

output_columns:
  - start_ts
  - end_ts
  - duration
  - frame_count

parameters:
  where_condition:
    type: string
    required: true
    description: "SQL WHERE条件"
    examples: ["ABS(steering_angle) > 0.6 AND speed > 25", "speed > 30"]
  min_duration:
    type: float
    default: 3.0
    description: "最小持续时间(秒)"
  pre_pad:
    type: float
    default: 0.0
  post_pad:
    type: float
    default: 0.0
  ts_expr:
    type: string
    default: "ts"

sql_template: |
  {cte_name} AS (
      SELECT
          MIN(ts) - {pre_pad} AS start_ts,
          MAX(ts) + {post_pad} AS end_ts,
          (MAX(ts) + {post_pad}) - (MIN(ts) - {pre_pad}) AS duration,
          COUNT(*) AS frame_count
      FROM (
          SELECT
              {ts_expr} AS ts,
              {ts_expr} - ROW_NUMBER() OVER (ORDER BY {ts_expr}) AS grp
          FROM ego
          WHERE {where_condition}
      )
      GROUP BY grp
      HAVING (MAX(ts) - MIN(ts)) >= {min_duration}
  )
```

#### 3.1.2 RecipeLibrary

92个recipe定义 + 8个用户策略。每个recipe有两种模式：

**模式A：raw_sql直通**（85个recipe）
```yaml
# recipes/Intersection_Crossing.yaml
name: Intersection_Crossing
version: "1.0"
variants:
  default:
    tag_name: "Intersection_Crossing"
    output_tag_name: "Intersection_Crossing"
    raw_sql: |
      SELECT
        '{tag_name}' AS tag_name,
        rt.start_ts, rt.end_ts,
        ...
      FROM range_tag rt
      WHERE rt.tag_name = '{tag_name}'
```

**模式B：block组装**（7个recipe）
```yaml
# recipes/cutin_analysis.yaml
name: cutin_analysis
version: "1.0"
blocks:
  - name: simple_tag_extraction
    cte_name: "cutin_events"
    params:
      tag_name: "{tag_name}"
      pre_pad: 2.0
      post_pad: 2.0
  - name: ego_speed_simple
    cte_name: "EgoSpeedAnalysis"
    upstream: "cutin_events"
  - name: target_proximity
    cte_name: "TargetProximity"
    upstream: "EgoSpeedAnalysis"
    params:
      time_trim: 1.0

final_select_template: |
  SELECT
      'cutin_analysis' AS tag_name,
      esa.start_ts, esa.end_ts, ...
  FROM EgoSpeedAnalysis esa
  LEFT JOIN TargetProximity tp ON ...

variants:
  default:
    tag_name: "Cutin"
  congested:
    tag_name: "CongestedFollow"
```

### 3.2 SQL组装流程

#### 3.2.1 Layer 1/2: Recipe直通 + Block组装

```python
# agent_engine.py:828-839
if recipe_name and recipe_variant:
    assembler = BlockAssembler()
    sql = assembler.assemble(recipe_name, recipe_variant)
```

`assemble()`内部流程：
1. 加载recipe YAML
2. 如果有`blocks`定义 → block组装路径
3. 如果variant有`raw_sql` → 直通路径（85/92个recipe走这里）
4. 参数解析：`_resolve_str()`替换`{var}`占位符，最多5轮迭代（处理嵌套引用）
5. CTE拼接：`WITH\n` + `cte1,\n\ncte2,\n\n...` + `\n\n` + final_select

**覆盖率**：92/92个recipe有对应的SQL定义。CONCEPT_RECIPE_MAP 143条keyword覆盖大部分常见中文表述。

#### 3.2.2 Layer 3: Hybrid Block Assembly

当Round1识别出`required_blocks`但没有完整recipe时：

1. 用`BlockAssembler`组装已知block的CTE
2. LLM生成"胶水CTE"连接各block + final SELECT
3. `_parse_hybrid_llm_output()`解析LLM输出
4. `assembler.assemble_hybrid()`合并所有CTE

```python
# agent_engine.py:842-901
assembler = BlockAssembler()
auto_blocks = []
for bname in required_blocks:
    block_def = {"name": bname, "cte_name": bname}
    auto_blocks.append(block_def)

# LLM生成胶水CTE
r2_messages = build_hybrid_round2_messages(
    question, required_blocks, auto_blocks, schema_text, assembler
)
raw_sql = await self.llm.chat(...)
hybrid_result = _parse_hybrid_llm_output(raw_sql, required_blocks)

# 组装
sql = assembler.assemble_hybrid(
    recipe_name="hybrid_" + "_".join(required_blocks),
    variant_name="default",
    auto_blocks=auto_blocks + hybrid_result.get("extra_auto_blocks", []),
    custom_ctes_sql=sanitized_ctes,
    final_select_sql=sanitized_final,
)
```

**`assemble_hybrid()`流程**：
1. Step A: 自动拼装已知block → 从BlockLibrary加载模板，解析参数，生成CTE
2. Step B: 添加LLM生成的custom CTEs
3. Step C: 拼装 `WITH\n` + 所有CTE + `\n\n` + final_select

**Sanitization**：LLM输出中可能残留`{placeholder}`或`{{placeholder}}`标记，用正则清理：
```python
clean = re.sub(r'\{[a-zA-Z_]\w*\}', '', cte)
clean = re.sub(r'\{\{[a-zA-Z_]\w*\}\}', '', clean)
```

#### 3.2.3 Fallback: Round 2 LLM生成

当recipe和hybrid都无法生成SQL时，走完整LLM生成：

1. 代码层确定involved_tables（基于Round1的composition判断）
2. `format_schema_for_prompt(only_tables=involved_tables)` 构建精确schema
3. `concept_router.get_round2_messages()` 组装Round2 prompt
4. LLM生成SQL，支持流式（`on_token`回调）

```python
# agent_engine.py:906-947
involved_tables = {"range_tag"}
if ego_fields or composition in ("tag_join_ego", "cross_table", "ego_only"):
    involved_tables.add("ego")
if need_dynamic_obj or composition in ("tag_join_dynamic_obj", "cross_table"):
    involved_tables.add("dynamic_obj")
# ...

schema_text = format_schema_for_prompt(self.schema, only_tables=involved_tables)
r2_messages = concept_router.get_round2_messages(question, r1_result, schema_text)

# 流式生成
if on_token is not None:
    sql_chunks = []
    async for token in self.llm.chat_stream(...):
        sql_chunks.append(token)
        await on_token(token)
    raw_sql = "".join(sql_chunks)
else:
    raw_sql = await self.llm.chat(...)

sql = self._clean_sql(raw_sql)
```

---

## 4. 纠错循环

### 4.1 三级验证

```
SQL字符串
    │
    ▼ _validate_sql()
正则检查: SELECT/FROM必须存在，禁止DROP/DELETE/INSERT
    │
    ▼ _dry_run()
SQLite EXPLAIN 试编译: near "xxx": syntax error
    │
    ▼ _check_start_end_ts()
结果列检查: start_ts/end_ts是否存在于SELECT中
```

### 4.2 纠错流程

```python
# agent_engine.py:960-1020
max_corrections = self.MAX_CORRECTIONS  # 默认3

# Recipe SQL语法错误 → 不走LLM纠错，直接报错给开发者
if sql_source == "recipe":
    ok, err_msg = self._dry_run(sql)
    if not ok:
        return AgentResult(
            explanation=f"Recipe模板语法错误(recipe={recipe_name}), 需开发者修复: {err_msg}",
            error=err_msg,
        )

# LLM SQL：允许纠错循环
for attempt in range(max_corrections + 1):
    ok, err_msg = self._dry_run(sql)
    if ok:
        # 二次验证：start_ts/end_ts校验
        ts_err = self._check_start_end_ts(sql)
        if ts_err:
            ok = False
            err_msg = ts_err
        else:
            break  # 通过

    if correction_rounds >= max_corrections:
        return AgentResult(error=err_msg, max_corrections_exceeded=True)

    # Round 3: 纠错prompt回传LLM
    correction_messages = self._build_correction_prompt(sql, err_msg, schema_text)
    raw_sql = await self.llm.chat(
        correction_messages[0]["content"],
        correction_messages[1]["content"],
        temperature=0.0,
    )
    sql = self._clean_sql(raw_sql)
```

### 4.3 _validate_sql() 详细规则

```python
def _validate_sql(self, sql: str) -> Optional[str]:
    upper = sql.strip().upper()
    if not upper.startswith("SELECT") and not upper.startswith("WITH"):
        return "SQL必须以SELECT或WITH开头"
    if "SELECT" not in upper:
        return "SQL缺少SELECT"
    if "FROM" not in upper:
        return "SQL缺少FROM"
    # 禁止DDL/DML
    for kw in ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE"]:
        if re.search(rf'\b{kw}\b', upper):
            return f"SQL包含禁止的{kw}操作"
    return None
```

### 4.4 _dry_run() 实现

```python
def _dry_run(self, sql: str) -> tuple[bool, str]:
    """EXPLAIN试编译，返回(是否通过, 错误信息)"""
    try:
        conn = sqlite3.connect(self._sample_db)
        cursor = conn.cursor()
        cursor.execute(f"EXPLAIN {sql}")
        conn.close()
        return True, ""
    except Exception as e:
        return False, str(e)
```

### 4.5 _check_start_end_ts() 校验

```python
def _check_start_end_ts(self, sql: str) -> Optional[str]:
    """检查SQL的SELECT中是否包含start_ts和end_ts。
    这是业务硬约束：所有结果必须包含时间范围。
    """
    upper = sql.upper()
    select_part = upper.split("FROM")[0] if "FROM" in upper else upper
    has_start = "START_TS" in select_part
    has_end = "END_TS" in select_part
    if not has_start and not has_end:
        return "SQL的SELECT中缺少start_ts和end_ts"
    if not has_start:
        return "SQL的SELECT中缺少start_ts"
    if not has_end:
        return "SQL的SELECT中缺少end_ts"
    return None
```

### 4.6 _build_correction_prompt() 结构

```python
def _build_correction_prompt(self, sql, error_msg, schema_text):
    return [
        {"role": "system", "content": 
         f"你是SQL修复专家。根据错误信息修复SQL。\n\nSchema:\n{schema_text}\n\n"
         f"规则:\n1.只使用Schema中存在的表和字段\n"
         f"2.所有时间字段单位相同，直接比较\n"
         f"3.输出纯SQL，不含markdown"},
        {"role": "user", "content": 
         f"SQL:\n{sql}\n\n错误: {error_msg}\n\n请输出修复后的完整SQL。"},
    ]
```

### 4.7 错误处理矩阵

| 错误类型 | 来源 | 处理方式 | LLM纠错 |
|----------|------|----------|---------|
| 缺少SELECT/FROM | _validate_sql | 直接返回错误 | ❌ |
| 包含DDL关键字 | _validate_sql | 直接返回错误 | ❌ |
| SQLite语法错误 | _dry_run | LLM纠错循环 | ✅ 最多3轮 |
| 缺少start_ts/end_ts | _check_start_end_ts | LLM纠错循环 | ✅ 最多3轮 |
| Recipe SQL语法错 | _dry_run | 直接报错给开发者 | ❌ 不走LLM |
| Round1 JSON解析失败 | parse_round1_output | Fallback到旧流程 | — |
| Hybrid组装失败 | assemble_hybrid | Fallback到Round2 LLM | — |
| 向量搜索异常 | vector_search | 静默跳过,走Phase4 | — |
| 单个DB查询失败 | process_one | 跳过该DB,不影响整体 | — |

---

## 5. 向量路由架构

### 5.1 双模型分离

| 维度 | BGE-M3 | MiniLM-L6-v2 |
|------|--------|---------------|
| 维度 | 1024 | 384 |
| 大小 | 2.2GB | 80MB |
| 加载时间(DSW本地) | ~2秒 | ~30秒 |
| 中文top-1准确率 | 42.9% (6/14) | 28.6% (4/14) |
| Phase4a阈值 | 0.40 (cosine distance) | 0.35 |
| ChromaDB目录 | `vector_db_bge_m3/` | `vector_db/` |

**BGE-M3优先链**：
1. `SCENESQL_EMBED_MODEL` 环境变量（最高优先级）
2. `/root/models/bge-m3` 本地路径（2秒加载）
3. `BAAI/bge-m3` HF Hub（需下载5-10分钟）
4. `all-MiniLM-L6-v2` fallback

### 5.2 vector_router.py 核心API

```python
# vector_router.py (218行)

# 全局状态
_collection = None        # ChromaDB Collection
_embedding_model = None   # sentence-transformers模型
EMBED_MODEL = None        # 当前模型名

def is_available() -> bool:
    """向量路由是否可用（模型+ChromaDB已加载）"""

def search(query: str, top_k: int = 3) -> List[Tuple[str, float]]:
    """向量搜索，返回 [(recipe_name, cosine_distance), ...]"""

def load_from_templates(force: bool = False):
    """从templates.jsonl构建/加载索引"""

def index_recipes(entries: list):
    """编码recipe列表并写入ChromaDB"""
```

### 5.3 load_from_templates() 防覆盖保护

```python
def load_from_templates(force: bool = False):
    if not is_available():
        return
    if not force and _collection.count() > 0:
        logger.info(f"Vector DB already has {_collection.count()} entries, skip re-indexing")
        return
    entries = _collect_template_entries()  # 从templates.jsonl收集
    if entries:
        index_recipes(entries)
```

**为什么需要保护**：外部脚本（`scripts/index_bge_m3.py`）用BGE-M3编码92条recipe到`vector_db_bge_m3/`，耗时5-10分钟。如果不保护，每次`ConceptRouter.__init__()`触发`load_from_templates()`，用当前模型覆盖已有数据——如果当前模型不同（MiniLM vs BGE-M3），会写入维度不匹配的向量。

**force=True场景**：templates.jsonl新增recipe后，需要重建索引。

### 5.4 templates.jsonl 格式

92行JSONL，每行一个recipe的语义描述：

```json
{"id": "Intersection_Crossing", "nl": "路口横穿：产线SQL直通模式(raw_sql)", "domain": "", "tags": ["Intersection_Crossing"], "text_for_embedding": "路口横穿：产线SQL直通模式(raw_sql) Intersection_Crossing", "recipe_name": "Intersection_Crossing"}
```

**text_for_embedding字段** = nl描述 + recipe_name。这是向量编码的输入，质量直接决定搜索准确率。

### 5.5 BGE-M3索引构建脚本

`scripts/index_bge_m3.py`：
1. 加载本地`/root/models/bge-m3`
2. 编码92条recipe的`text_for_embedding`
3. 写入`vector_db_bge_m3/`
4. metadata含`recipe_name`

```bash
# 在DSW上执行
cd /root/data/text2sql
python scripts/index_bge_m3.py --force
```

### 5.6 Phase 4a 反查机制

向量搜索返回`(recipe_name, distance)`，需要在combined_map中反查：

```python
hits = vector_search(nl, top_k=1)
if hits and hits[0][1] < _vec_threshold:
    recipe_name = hits[0][0]
    matched_key = None
    for k, (rn, _v) in combined_map.items():
        if rn == recipe_name:
            matched_key = k
            break
    if matched_key:
        recipe, variant = combined_map[matched_key]
```

**⚠ 已知限制**：如果recipe_name不在combined_map中（向量搜索返回了一个没有keyword映射的recipe），反查失败，静默跳过。目前2个recipe缺少直接映射，但已有独立覆盖。

---

## 6. 执行层

### 6.1 查询模式

AgentEngine根据初始化参数决定查询模式：

| 模式 | 入口 | 场景 |
|------|------|------|
| `parquet` | `_query_parquet()` | DuckDB/Lance格式 |
| `is_dir=True` | `_query_batch()` | 目录下多个SQLite DB |
| `is_dir=False` | `_query_single()` | 单个SQLite DB |

### 6.2 SQLite批量查询流程

```python
# agent_engine.py:424-576
async def _query_batch(self, sql, result_limit=100, db_limit=30, max_workers=32):
    # 1. 收集DB文件列表
    db_files = [f for f in os.listdir(self.db_path) if f.endswith(".db")]
    db_files = db_files[:db_limit]  # 限制数量

    # 2. 注入bag_id
    sql = self._ensure_bag_id_in_select(sql)

    # 3. ThreadPoolExecutor并行查询
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        def process_one(db_file: str):
            bag_id = db_file.replace(".db", "")
            db_path = os.path.join(self.db_path, db_file)
            conn = sqlite3.connect(db_path)
            try:
                cursor = conn.execute(sql)
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                for row in rows:
                    row["bag_id"] = bag_id
                return rows
            except Exception as e:
                logger.warning(f"DB {db_file} query failed: {e}")
                return []
            finally:
                conn.close()

        futures = {pool.submit(process_one, f): f for f in db_files}
        # 收集结果
```

**关键优化**：每个SQLite DB只打开一次连接，在同一个连接上执行SQL。

### 6.3 bag_id注入

```python
# agent_engine.py:577-656
def _ensure_bag_id_in_select(self, sql: str) -> str:
    """确保SQL的SELECT中包含bag_id列。
    使用sqlglot解析SQL AST：
    1. 检查SELECT中是否已有bag_id
    2. 如果没有，在每个SELECT子句中注入 'substr(file_name, 1, -3) AS bag_id'
    3. bag_id从SQLite文件名提取: file.replace(".db", "")
    """
```

**实际注入方式**：在process_one()中，bag_id直接附加到每行结果的dict中：
```python
for row in rows:
    row["bag_id"] = bag_id  # bag_id = db_file.replace(".db", "")
```

### 6.4 结果聚合

- **去重**：按`(bag_id, start_ts, end_ts, tag_name)`去重
- **排序**：按`start_ts DESC`
- **分页**：`page` + `page_size`参数，默认50条/页

### 6.5 AgentResult 完整数据结构

```python
@dataclass
class AgentResult:
    sql: str                                    # 最终执行的SQL
    explanation: str                            # 结果解释
    rows: List[Dict[str, Any]] = field(default_factory=list)  # 结果行
    columns: List[str] = field(default_factory=list)          # 列名
    error: Optional[str] = None                # 错误信息
    scanned_dbs: int = 0                       # 扫描的DB数
    matched_dbs: int = 0                       # 有匹配结果的DB数
    total_rows: int = 0                        # 总结果行数
    page: int = 1                              # 当前页
    page_size: int = 50                        # 每页条数
    correction_rounds: int = 0                 # 纠错轮数
    max_corrections_exceeded: bool = False      # 是否超出最大纠错次数
    sql_source: str = ""                       # "recipe"|"hybrid"|"llm"|"user_strategy"|"recipe_vector"
```

---

## 7. DSW部署架构

### 7.1 服务启动

```bash
cd /root/data/text2sql
bash visualizer/deploy.sh -f
```

deploy.sh会：
1. 检查Python依赖
2. 构建前端（如需要）
3. `source ${PROJECT_ROOT}/.env` 加载环境变量
4. 启动uvicorn服务

### 7.2 关键环境变量

| 变量 | 值 | 作用 |
|------|----|------|
| `SCENESQL_EMBED_MODEL` | `/root/models/bge-m3` | 强制使用BGE-M3 |
| `LD_LIBRARY_PATH` | 排除PPU路径 | 避免CUDA .so洪泛 |
| `PROTOCOL_BUFFERS_PYTHON_IMPLEMENT` | `python` | protobuf 3.20.3兼容 |
| `HF_ENDPOINT` | `https://hf-mirror.com` | HuggingFace镜像 |

### 7.3 文件系统布局

```
/root/data/text2sql/
├── agent/backend/app/
│   ├── core/
│   │   ├── concept_router.py      # 855行, 5层路由
│   │   ├── vector_router.py       # 218行, ChromaDB向量搜索
│   │   ├── block_assembler.py     # 360行, SQL组装引擎
│   │   ├── llm_client.py          # ~60行, OpenAI兼容API客户端
│   │   ├── schema_reader.py       # Schema解析
│   │   ├── tag_router.py          # 旧路由(保留兼容)
│   │   ├── blocks/                # 18个block YAML模板
│   │   ├── recipes/               # 92个recipe YAML
│   │   ├── user_strategies/       # 8个用户策略YAML
│   │   ├── vector_db/             # MiniLM ChromaDB (384维)
│   │   ├── vector_db_bge_m3/      # BGE-M3 ChromaDB (1024维)
│   │   └── templates.jsonl        # 92条recipe语义描述
│   └── services/
│       └── agent_engine.py        # 1038行, Agent Loop核心
├── visualizer/                    # 前端+后端
│   ├── backend/app/main.py        # FastAPI入口
│   └── frontend/                  # Vue/React前端
├── scripts/
│   └── index_bge_m3.py            # BGE-M3索引构建脚本
├── .agents/skills/
│   ├── development-workflow/       # 开发流程skill
│   ├── ubm-schema-sync/           # Schema同步skill
│   └── agent-loop/                # Agent Loop skill
└── .env                           # 环境变量
```

---

## 8. 性能特征

### 8.1 延迟分布

| 路径 | 典型延迟 | LLM调用次数 |
|------|----------|-------------|
| Layer1 Recipe直通 | 2-5秒 | 0 (纯代码) |
| Layer2 Block组装 | 2-5秒 | 0 (纯代码) |
| Layer3 Hybrid | 30-60秒 | 1 |
| LLM Fallback + 0轮纠错 | 30-60秒 | 1 |
| LLM Fallback + 1轮纠错 | 60-120秒 | 2 |
| LLM Fallback + 3轮纠错 | 90-180秒 | 4 |

**注意**：85%+的查询走Layer1/2，延迟2-5秒。LLM调用是主要瓶颈。

### 8.2 向量路由准确率

BGE-M3 (14个中文query测试)：

| 阈值 | 通过数 | 正确数 | Precision | Recall |
|------|--------|--------|-----------|--------|
| 0.30 | 1 | 1 | 100% | 7% |
| 0.35 | 5 | 5 | 100% | 36% |
| 0.40 | 8 | 6 | 75% | 57% |
| 0.45 | 11 | 6 | 55% | 79% |

MiniLM: 28.6% top-1，不推荐用于中文。

**根本瓶颈**：templates.jsonl的`nl`描述太简陋（如"路口横穿：产线SQL直通模式(raw_sql)"），区分度不够。改进nl描述比换模型更有效。

---

## 9. 已知问题与改进方向

### 9.1 向量路由准确率上限

当前42.9% top-1，主要瓶颈是text_for_embedding质量。改进方案：
1. 为每个recipe补充同义词、场景上下文描述
2. 用户策略的keywords自动追加到text_for_embedding
3. 考虑BGE-M3的多语言特性，用英文+中文双语描述

### 9.2 Phase 4a反查覆盖率

向量搜索返回的recipe_name必须在combined_map中才能路由成功。当前有2个recipe没有直接的keyword映射（但已有独立映射覆盖）。

### 9.3 纠错循环效率

当前最多3轮纠错，每轮需要1次LLM调用。优化方向：
1. 纠错prompt中加入常见错误模式（`*1e9`缺失、sub_tag忘记json_extract等）
2. 对常见语法错误做代码层自动修复，不需要LLM
3. _check_start_end_ts()失败时，直接代码注入start_ts/end_ts，而非LLM纠错

### 9.4 DB schema不一致

不同SQLite DB可能有不同schema版本。某些DB缺少较新的列（如`dl.predecessors`）。当前处理：单个DB查询失败时跳过，不影响整体结果。

### 9.5 concept_groups.yaml与CONCEPT_RECIPE_MAP冗余

42个概念定义在concept_groups.yaml中，143条映射在CONCEPT_RECIPE_MAP中。两者存在语义重叠但数据结构不同。长期应统一为一个配置源。

---

## 10. 关键代码文件索引

| 文件 | 行数 | 职责 |
|------|------|------|
| `concept_router.py` | 855 | Round 1概念识别 + 5层路由 + Phase4a向量搜索集成 |
| `vector_router.py` | 218 | ChromaDB + BGE-M3/MiniLM向量搜索 |
| `agent_engine.py` | 1038 | Agent Loop核心：_query_two_round() + 纠错循环 + 执行层 |
| `block_assembler.py` | 360 | Block CTE模板化 + Hybrid组装 + 参数解析 |
| `llm_client.py` | ~60 | OpenAI兼容API客户端(chat + chat_stream) |
| `templates.jsonl` | 92行 | Recipe语义描述（向量索引源） |
| `concept_groups.yaml` | 42条 | 概念定义(nl_variants, tag_names, composition_rule) |
| `blocks/*.yaml` | 18个 | CTE block模板定义 |
| `recipes/*.yaml` | 92个 | Recipe定义(raw_sql或block组装) |
| `user_strategies/*.yaml` | 8个 | 用户自定义策略 |

---

## 附录A: RECIPE_BLOCK_MAP

Recipe名到所需block的映射，用于compound场景的`_infer_required_blocks()`：

```python
RECIPE_BLOCK_MAP = {
    "obstacle_avoidance": ["obstacle_proximity", "steering_change_detect"],
    "large_curvature_road": ["continuous_segment"],
    "cutin_analysis": ["simple_tag_extraction", "ego_speed_simple", "target_proximity"],
    "close_follow_analysis": ["simple_tag_extraction", "close_follow_detail"],
    "conflict_pipeline": ["simple_tag_extraction", "conflict_classification"],
    # ... 更多映射
}
```

## 附录B: Composition Rule 定义

9种组合规则，决定SQL生成策略：

| composition | 说明 | involved_tables |
|-------------|------|-----------------|
| `single_tag` | 单标签查询 | range_tag |
| `multi_tag` | 多标签组合 | range_tag |
| `tag_join_ego` | 标签+自车状态 | range_tag, ego |
| `tag_join_dynamic_obj` | 标签+动态目标 | range_tag, dynamic_obj |
| `tag_join_dynamic_lane` | 标签+动态车道 | range_tag, dynamic_lane |
| `tag_join_intersection_info` | 标签+路口信息 | range_tag, intersection_info |
| `cross_table` | 跨表关联 | range_tag, ego, dynamic_obj |
| `ego_only` | 仅自车状态 | ego |
| `cte_analysis` | CTE分析流水线 | (由blocks决定) |
| `hybrid_blocks` | 混合block组装 | (由blocks决定) |

---

## 附录C: _clean_sql() 完整实现

LLM输出的SQL需要经过多层清洗：

```python
def _clean_sql(self, raw: str) -> str:
    raw = raw.strip()
    # 1. 移除 <think>...</think> 推理块（某些模型会输出）
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    if "</think>" in raw:
        raw = raw.split("</think>")[-1]
    
    # 2. 去markdown代码块包裹
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    if raw.endswith("```"):
        raw = raw.rsplit("\n", 1)[0] if "\n" in raw else raw
    raw = raw.strip()
    if raw.lower().startswith("sql"):
        raw = raw[3:].strip()

    # 3. 硬修正：移除时间戳转换（字段 * 1e9 / * 1000）
    # 只匹配 字段名/列引用 * 数值 的模式，避免误伤 WHERE speed > 1000
    raw = re.sub(r'(\w+)\s*\*\s*(?:1e9|1e6|1000000|1000)\b', r'\1', raw)

    # 4. 提取完整SQL
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if lines:
        first_upper = lines[0].upper()
        if first_upper.startswith("SELECT") or first_upper.startswith("WITH"):
            return raw
    # 回退：找最后一个SELECT/WITH开头的行
    for i in range(len(lines) - 1, -1, -1):
        upper = lines[i].upper()
        if upper.startswith("SELECT") or upper.startswith("WITH"):
            return "\n".join(lines[i:])
    return raw
```

**常见清洗场景**：
| LLM输出问题 | 处理方式 |
|-------------|---------|
| ` ```sql\nSELECT...``` ` | 去markdown包裹 |
| `<think>让我分析一下...</think>SELECT...` | 移除推理块 |
| `以下是SQL：\nSELECT...` | 找SELECT行开始拼接 |
| `speed * 1e9` | 硬修正移除*1e9 |
| `sql\nSELECT...` | 去掉"sql"前缀 |

---

## 附录D: _parse_hybrid_llm_output() 解析格式

Hybrid场景下LLM输出的结构化格式：

```
---BLOCK_PARAMS---
continuous_segment: where_condition=ABS(steering_angle) > 0.6, min_duration=2.0
---CUSTOM_CTE---
speed_change AS (
    SELECT start_ts, end_ts, ...
    FROM continuous_segment
    WHERE duration > 1.0
)
---END_CTE---
---FINAL_SELECT---
SELECT
    'large_curvature' AS tag_name,
    seg.start_ts, seg.end_ts,
    sc.speed_drop
FROM continuous_segment seg
JOIN speed_change sc ON seg.start_ts = sc.start_ts
```

解析逻辑：
1. `---BLOCK_PARAMS---` 节：解析 `block_name: param1=value1, param2=value2` 格式
2. `---CUSTOM_CTE---` 节：按`---END_CTE---`分割多个CTE
3. `---FINAL_SELECT---` 节：最终SELECT语句

**Fallback**：如果LLM没有按格式输出，把全文作为final_select（最安全的降级）。

```python
def _parse_hybrid_llm_output(raw_text: str, required_blocks: list) -> dict:
    result = {"extra_auto_blocks": [], "custom_ctes": [], "final_select": "",
              "block_params": {}}
    
    # 解析BLOCK_PARAMS
    if "---BLOCK_PARAMS---" in raw_text:
        params_section = raw_text.split("---BLOCK_PARAMS---", 1)[1]
        for delimiter in ["---CUSTOM_CTE---", "---FINAL_SELECT---"]:
            if delimiter in params_section:
                params_section = params_section.split(delimiter, 1)[0]
                break
        for line in params_section.strip().splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            bname, rest = line.split(":", 1)
            params = {}
            for pair in rest.split(","):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    params[k.strip()] = v.strip()
            if params:
                result["block_params"][bname.strip()] = params

    # 解析CUSTOM_CTE + FINAL_SELECT
    if "---CUSTOM_CTE---" in remaining and "---FINAL_SELECT---" in remaining:
        parts = remaining.split("---CUSTOM_CTE---", 1)[1]
        cte_part, select_part = parts.split("---FINAL_SELECT---", 1)
        result["custom_ctes"] = [c.strip() for c in cte_part.strip().split("---END_CTE---") if c.strip()]
        result["final_select"] = select_part.strip()
    elif "---FINAL_SELECT---" in remaining:
        _, select_part = remaining.split("---FINAL_SELECT---", 1)
        result["final_select"] = select_part.strip()
    else:
        result["final_select"] = remaining.strip()  # Fallback: 全文作为final SELECT

    return result
```

---

## 附录E: vector_router.py 完整加载流程

```python
# vector_router.py 核心流程

# 1. 懒初始化：ConceptRouter.__init__() 触发
def _init_vector_index(self):
    """懒加载：如果依赖未安装则静默跳过"""
    try:
        from .vector_router import init_if_needed, load_from_templates
        init_if_needed()           # 加载模型 + 连接ChromaDB
        load_from_templates()      # 从templates.jsonl建索引（如果为空）
    except ImportError:
        pass

# 2. init_if_needed()
def init_if_needed():
    global _embedding_model, _collection, EMBED_MODEL
    
    # 模型优先级链
    model_path = os.environ.get("SCENESQL_EMBED_MODEL")
    if model_path and os.path.exists(model_path):
        EMBED_MODEL = model_path
    elif os.path.exists("/root/models/bge-m3"):
        EMBED_MODEL = "/root/models/bge-m3"
    else:
        try:
            from sentence_transformers import SentenceTransformer
            _tmp = SentenceTransformer("BAAI/bge-m3")
            EMBED_MODEL = "BAAI/bge-m3"
        except:
            EMBED_MODEL = "all-MiniLM-L6-v2"
    
    # 加载模型
    from sentence_transformers import SentenceTransformer
    _embedding_model = SentenceTransformer(EMBED_MODEL)
    
    # 连接ChromaDB
    import chromadb
    if "bge-m3" in (EMBED_MODEL or "").lower():
        persist_dir = CORE_DIR / "vector_db_bge_m3"
    else:
        persist_dir = CORE_DIR / "vector_db"
    
    client = chromadb.PersistentClient(path=str(persist_dir))
    _collection = client.get_or_create_collection(
        name="scene_recipes",
        metadata={"hnsw:space": "cosine"}
    )

# 3. load_from_templates(force=False)
def load_from_templates(force: bool = False):
    if not is_available():
        return
    # 防覆盖保护
    if not force and _collection.count() > 0:
        logger.info(f"Vector DB already has {_collection.count()} entries, skip")
        return
    entries = _collect_template_entries()  # 从templates.jsonl收集
    if entries:
        index_recipes(entries)

# 4. index_recipes(entries)
def index_recipes(entries: list):
    """编码并写入ChromaDB"""
    texts = [e["text_for_embedding"] for e in entries]
    ids = [e["id"] for e in entries]
    metas = [{"recipe_name": e["recipe_name"]} for e in entries]
    
    embeddings = _embedding_model.encode(texts).tolist()
    
    # 分批写入（ChromaDB单次上限通常为5000+）
    batch_size = 100
    for i in range(0, len(entries), batch_size):
        _collection.upsert(
            ids=ids[i:i+batch_size],
            embeddings=embeddings[i:i+batch_size],
            documents=texts[i:i+batch_size],
            metadatas=metas[i:i+batch_size],
        )

# 5. search(query, top_k=3)
def search(query: str, top_k: int = 3) -> List[Tuple[str, float]]:
    query_embedding = _embedding_model.encode([query]).tolist()
    results = _collection.query(
        query_embeddings=query_embedding,
        n_results=top_k,
        include=["metadatas", "distances"]
    )
    return [
        (results["metadatas"][0][i]["recipe_name"], results["distances"][0][i])
        for i in range(len(results["ids"][0]))
    ]
```

---

## 附录F: Round 1 Prompt 模板

Round 1使用JSON mode + 低温度(0.0)让LLM返回结构化概念识别结果：

```python
# concept_router.py: build_round1_messages()

ROUND1_SYSTEM = """你是一个自动驾驶场景概念识别器。
给定用户的自然语言查询，识别出涉及的场景概念和查询意图。

你必须输出JSON格式：
{
  "concepts": ["概念1", "概念2"],
  "composition": "single_tag|tag_join_ego|cross_table|...",
  "recipe": "recipe_name或空字符串",
  "recipe_variant": "default或空字符串",
  "required_blocks": ["block1", "block2"],
  "ego_fields": ["speed", "steering_angle"],
  "need_dynamic_obj": false,
  "need_dynamic_lane": false,
  "need_intersection_info": false
}

可用的概念列表：
{concept_list}

可用的recipe列表：
{recipe_table}
"""

ROUND1_USER = """用户查询：{nl}

请识别查询中的场景概念，返回JSON。"""
```

---

## 附录G: Round 2 Prompt 模板

当需要LLM生成SQL时，Round 2 prompt包含精确schema和Round 1的分析结果：

```python
# concept_router.py: get_round2_messages()

def get_round2_messages(self, nl: str, r1_result: dict, schema_text: str) -> list[dict]:
    context = assemble_round2_context(r1_result)  # 把Round1结果格式化为上下文
    
    system = f"""你是自动驾驶场景挖掘的SQL生成专家。

## 数据库Schema
{schema_text}

## Round 1 分析结果
{context}

## SQL规则
1. 只使用Schema中存在的表和字段
2. 所有时间字段单位相同（秒级Unix时间戳），直接比较
3. range_tag.param 是JSON字段，用 json_extract(param, '$.key') 提取
4. 跨表时间对齐：e.ts BETWEEN r.start_ts AND r.end_ts
5. 输出纯SQL，不含markdown标记
6. 必须包含 start_ts, end_ts 列
7. 不要生成 LIMIT 子句
"""
    
    user = f"用户查询：{nl}\n\n请生成SQL。"
    return [system, user]
```

---

## 附录H: 开发与测试指南

### H.1 本地测试 Recipe 组装

```python
# 在DSW上运行
cd /root/data/text2sql
python -m agent.backend.app.core.block_assembler

# 输出所有可用recipe+variant
# 输出conflict_pipeline/vehicle的组装SQL
```

### H.2 测试向量搜索

```python
# 在DSW上运行
cd /root/data/text2sql
python -c "
from agent.backend.app.core.vector_router import init_if_needed, search, is_available
init_if_needed()
if is_available():
    results = search('车辆加塞', top_k=5)
    for name, dist in results:
        print(f'{name}: {dist:.4f}')
"
```

### H.3 重建BGE-M3索引

```bash
cd /root/data/text2sql
python scripts/index_bge_m3.py --force
```

### H.4 添加新用户策略

1. 创建 `user_strategies/新策略名.yaml`
2. 定义keywords、tag_name、raw_sql或blocks
3. 重启服务或调用热更新API
4. 在templates.jsonl中添加对应条目（否则向量搜索找不到）

### H.5 调试路由问题

```python
# 直接调用ConceptRouter路由
from agent.backend.app.core.concept_router import ConceptRouter
cr = ConceptRouter()
result = cr.parse_round1_output('{"concepts":["切入"]}', "切入")
print(result)
# 预期: {'concepts': ['切入'], 'recipe': 'cutin_analysis', 'recipe_variant': 'default', ...}
```

---

## 附录I: _inject_limit() 实现

系统自动注入LIMIT子句，防止LLM生成的SQL返回过多数据：

```python
def _inject_limit(self, sql: str, limit: int) -> str:
    """在SQL末尾注入LIMIT，如果已有则取较小值"""
    upper = sql.upper().rstrip()
    # 已有LIMIT
    if re.search(r'\bLIMIT\s+\d+\s*$', upper):
        existing = int(re.search(r'LIMIT\s+(\d+)', upper).group(1))
        if existing <= limit:
            return sql
        # 替换为较小值
        return re.sub(r'\bLIMIT\s+\d+', f'LIMIT {limit}', sql, flags=re.IGNORECASE)
    # 注入新LIMIT
    return sql.rstrip().rstrip(';') + f'\nLIMIT {limit}'
```

---

## 附录J: ConceptRouter初始化时序

```
ConceptRouter.__init__()
    │
    ├─ self.CONCEPT_RECIPE_MAP = {143条keyword→(recipe,variant)}
    │
    ├─ self.load_user_strategies()
    │   └─ 扫描 user_strategies/*.yaml
    │       → _user_strategy_map = {8条keyword→(recipe,variant)}
    │
    ├─ self._init_vector_index()
    │   ├─ init_if_needed()
    │   │   ├─ 确定模型: SCENESQL_EMBED_MODEL > /root/models/bge-m3 > HF > MiniLM
    │   │   ├─ 加载模型: SentenceTransformer(model_path)
    │   │   └─ 连接ChromaDB: PersistentClient → get_or_create_collection
    │   │
    │   └─ load_from_templates(force=False)
    │       └─ if _collection.count() > 0: skip
    │          else: index_recipes(entries)
    │
    └─ ready for route()
```

---

## 附录K: 完整请求生命周期示例

用户输入"查加塞场景"的完整流程：

```
1. AgentEngine.query("查加塞场景", use_two_round=True)
   → _query_two_round("查加塞场景")

2. Round 1: concept_router.get_round1_messages("查加塞场景")
   → LLM(temperature=0.0, response_format=json) 
   → {"concepts": ["加塞"], "composition": "single_tag", ...}

3. parse_round1_output(raw, nl="查加塞场景")
   → combined_map = CONCEPT_RECIPE_MAP ∪ _user_strategy_map
   → Phase 1: "加塞" in combined_map → ("cutin_analysis", "default") ✓
   → result = {"recipe": "cutin_analysis", "recipe_variant": "default", ...}

4. SQL生成:
   recipe命中 → BlockAssembler.assemble("cutin_analysis", "default")
   → RecipeLibrary.get("cutin_analysis") → blocks=[simple_tag_extraction, ego_speed_simple, target_proximity]
   → Block 1: simple_tag_extraction → {cte_name} AS (SELECT ... FROM range_tag WHERE tag_name = 'Cutin')
   → Block 2: ego_speed_simple → {cte_name} AS (SELECT ... FROM cutin_events JOIN ego ...)
   → Block 3: target_proximity → {cte_name} AS (SELECT ... FROM EgoSpeedAnalysis JOIN dynamic_obj ...)
   → final_select_template → SELECT ... FROM EgoSpeedAnalysis LEFT JOIN TargetProximity ...
   → WITH\n cte1,\n\n cte2,\n\n cte3 \n\n SELECT ...

5. 纠错循环:
   sql_source = "recipe" → dry-run only, no LLM correction
   _dry_run(sql) → ok=True ✓

6. 执行:
   _query_batch(sql, result_limit=100, db_limit=30, max_workers=32)
   → ThreadPoolExecutor → 每个DB反序列化+查询
   → bag_id注入 → 结果聚合 → AgentResult

7. 返回:
   AgentResult(
       sql="WITH\n cutin_events AS (...) ...",
       rows=[{tag_name: "Cutin", start_ts: 1234.5, end_ts: 1238.2, ...}],
       columns=["tag_name", "start_ts", "end_ts", ...],
       sql_source="recipe",
       correction_rounds=0,
       scanned_dbs=30,
       matched_dbs=5
   )
```
