---
name: agent-loop
version: 1.0.0
description: SceneSQL V4 Agent Loop — 从NL到SQL结果的完整路由、生成、纠错链路快速参考
category: text2sql
triggers:
  - 修改Agent Loop逻辑
  - 调试路由/纠错/向量搜索问题
  - 新增recipe或block模板
---

# Agent Loop Quick Reference

## 完整调用链

```
query(nl) → _query_two_round(nl)
  → Round1: ConceptRouter.route(nl)
    → Phase1: CONCEPT_RECIPE_MAP keyword命中 (143条)
    → Phase2: Compound concept分解 (42概念, 9组合模式)
    → Phase3: user_strategies/ 覆盖
    → Phase4a: BGE-M3向量搜索 (阈值0.40, chroma向量库)
    → Phase4: n-gram模糊匹配兜底
  → SQL生成:
    recipe命中 → BlockAssembler.assemble(recipe, variant) → Layer1/2直通
    无recipe有blocks → Layer3 Hybrid (已知block + LLM胶水CTE)
    都没有 → Fallback Round2 LLM生成
  → 纠错循环:
    _validate_sql() → _dry_run() → _check_start_end_ts()
    失败 → _build_correction_prompt() → LLM纠错 (最多3轮)
    recipe SQL语法错 → 不纠错,直接报开发者
  → 执行:
    _query_batch() ThreadPoolExecutor → 每DB反序列化1次
    _ensure_bag_id_in_select() → 结果聚合+分页
```

## 5层路由命中率

| 层 | 命中率 | 延迟 | LLM调用 |
|----|--------|------|---------|
| Phase1 keyword | ~85% | 2-5s | 0 |
| Phase2 compound | ~3% | 2-5s | 0 |
| Phase3 user_strategy | ~1% | 2-5s | 0 |
| Phase4a 向量搜索 | ~5% | 2-5s | 0 |
| Phase4 fuzzy+LLM | ~6% | 30-180s | 1-4 |

## 向量路由关键参数

- BGE-M3: 1024维, 阈值0.40, 75% precision, 目录 `vector_db_bge_m3/`
- MiniLM: 384维, 阈值0.35, 100% precision, 目录 `vector_db/`
- 防覆盖: `load_from_templates(force=False)` → `_collection.count()>0`跳过
- 环境变量: `SCENESQL_EMBED_MODEL=/root/models/bge-m3`

## 纠错循环3级验证

1. **_validate_sql()**: 正则检查SELECT/FROM必须存在，禁止DDL
2. **_dry_run()**: SQLite EXPLAIN试编译
3. **_check_start_end_ts()**: 结果列检查start_ts/end_ts

Recipe SQL语法错 → 直接报开发者，不LLM纠错
LLM SQL → 最多3轮纠错

## 关键文件

| 文件 | 行数 | 职责 |
|------|------|------|
| concept_router.py | 855 | 5层路由 + 向量搜索集成 |
| vector_router.py | 218 | ChromaDB + 双模型 |
| agent_engine.py | 1038 | _query_two_round()核心循环 |
| block_assembler.py | ~500 | CTE模板 + Hybrid组装 |
| templates.jsonl | 92 | Recipe定义 |

## 常见调试

- **向量搜索不命中**: 检查templates.jsonl的text_for_embedding是否太简陋
- **Phase4a反查失败**: recipe_name不在combined_map中
- **纠错循环卡死**: 检查LLM返回是否包含markdown包裹(```sql...```)
- **BGE-M3索引被覆盖**: 确认force=False，或手动跑scripts/index_bge_m3.py
