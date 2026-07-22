# 向量语义路由升级：MiniLM→BGE-M3 + Phase4a阈值调整

## 日期
2026-07-22

## 变更内容
1. **Phase4a向量路由阈值**：从0.35放宽到0.55（MiniLM中文语义距离偏大）
2. **ChromaDB目录按模型分离**：BGE-M3用`vector_db_bge_m3/`，MiniLM用`vector_db/`（维度不同不能混用）
3. **templates.jsonl补充recipe_name字段**：`id`即为recipe_name，确保向量搜索返回结果可直接映射到CONCEPT_RECIPE_MAP
4. **92条recipe全部索引到ChromaDB**（MiniLM: vector_db/, BGE-M3: vector_db_bge_m3/）

## 修改文件
- `agent/backend/app/core/concept_router.py` — Phase4a阈值0.35→0.55
- `agent/backend/app/core/vector_router.py` — 按模型选择不同ChromaDB目录
- `agent/backend/app/core/templates.jsonl` — 补充recipe_name字段
