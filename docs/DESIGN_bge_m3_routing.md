# SceneSQL v2.0 — BGE-M3 语义路由设计

> 日期: 2026-07-02
> 状态: 规划中
> 前置: v2.0 阶段1(Schema接入Prompt)已完成, 阶段2(基准测试)进行中

## 1. 问题定义

### 1.1 现状

当前 `TagRouter.route()` 使用**关键词子串匹配** (`kw in query`)，将用户NL路由到tag_name：

```
用户NL: "找出主路上有cutin的场景"
  ↓ 遍历626个关键词做子串匹配
  "切入" → Cutin  (来自 _MANUAL_KEYWORD_OVERRIDES)
  "主路" → static_link.link_type='主路' (来自 _MAP_ENUM_KEYWORDS)
```

### 1.2 数据规模

| 层 | 数量 | 来源 |
|----|------|------|
| schema_dictionary.yaml tag定义 | 222 | schema_dictionary.yaml |
| range_tag 实际 tag_name | 81 | DB中实际出现的 |
| tag_router 关键词总量 | 626 | auto_extract + manual_overrides |
| _MANUAL_KEYWORD_OVERRIDES 覆盖 | ~50 tags | 手写同义词 |
| _MAP_ENUM_KEYWORDS | ~15 条 | 手写枚举映射 |

### 1.3 核心问题

1. **同义词覆盖靠手写**：用户说"挤进来"匹配不上 Cutin，必须手动加到 `_MANUAL_KEYWORD_OVERRIDES`
2. **维护成本随 tag 数量线性增长**：222 个 tag × 平均 3 个同义词 = 626 条人工维护
3. **三层映射逻辑分散**：`_kw_to_tags`(auto) + `_MANUAL_KEYWORD_OVERRIDES`(manual) + `_MAP_ENUM_KEYWORDS`(enum)，三个独立数据结构

## 2. 方案：BGE-M3 语义路由

### 2.1 核心思路

用 BGE-M3 向量模型**替代**关键词子串匹配，一次向量查询覆盖精确匹配 + 语义匹配：

```
用户NL → BGE-M3 encode → 余弦相似度 → Top-K tags → 组装hint给LLM
```

### 2.2 为什么不用"关键词+向量fallback"？

| 方案 | 关键词命中 | 关键词未命中 | 维护 |
|------|-----------|------------|------|
| 关键词+向量fallback | ~0.5ms (keyword) | ~30ms (keyword+vector) | 两套系统 |
| **纯向量** | ~10ms (vector) | ~10ms (vector) | 一套系统 |

关键词遍历626条虽然<1ms，但维护两套匹配逻辑的成本更高。222个tag的语义空间不大，BGE-M3完全覆盖精确+模糊匹配，无需双系统。

### 2.3 唯一保留关键词的：map表枚举

`_MAP_ENUM_KEYWORDS`（"主路"→link_type='主路'）因文本过短（2-3字），向量区分度低，保留关键词精确匹配。

## 3. 架构设计

### 3.1 离线索引构建（一次性/CI）

```python
# 对每个tag，拼接 name + description + 手工同义词 为文本
tag_texts = []
for tag_name, info in tag_index.items():
    parts = [tag_name, info.description]
    parts.extend(_MANUAL_KEYWORD_OVERRIDES.get(tag_name, []))
    tag_texts.append(" ".join(parts))

# BGE-M3 encode + 保存
tag_embeddings = model.encode(tag_texts, normalize_embeddings=True)
save(tag_embeddings, "tag_index.pt")
```

索引内容：

| 索引项 | 文本来源 | 数量 |
|--------|---------|------|
| range_tag 标签 | tag_name + description + 手工同义词 | 222 |
| map表枚举值 | "主路" → static_link.link_type='主路' | ~15 |
| 交集查询模板 | "路口有绿灯" → Intersection + GreenLightNotProceeding | ~10 |

### 3.2 在线路由

```python
def route(self, query: str) -> RouteResult:
    # 1. 向量匹配
    q_emb = self.model.encode([query], normalize_embeddings=True)
    scores = (q_emb @ self.tag_embeddings.T).squeeze()
    top_k = scores.argsort(descending=True)[:5]
    
    # 2. 阈值过滤
    matched = [self.tag_names[i] for i in top_k if scores[i] > 0.3]
    
    # 3. map枚举仍走关键词
    map_hits = [e for e in _MAP_ENUM_KEYWORDS if e["kw"] in query]
    
    # 4. 组装hint给LLM
    return self._build_route_result(matched, map_hits)
```

### 3.3 阈值设计

| 相似度 | 含义 | 处理 |
|--------|------|------|
| > 0.7 | 高置信匹配 | 直接作为命中tag |
| 0.4-0.7 | 可能匹配 | 传入LLM作为候选，让LLM判断 |
| < 0.4 | 无关 | 丢弃 |

## 4. 部署方案

### 4.1 硬件：DSW PPU-ZW810E (96GB)

| 项目 | 状态 |
|------|------|
| PyTorch 2.6.0 + CUDA 12.6 兼容层 | ✅ |
| matmul算子 | ✅ 正常 |
| flash_attn | ✅ 可用 |
| BGE-M3 (568M) 推理 | 预计 <10ms/query |

### 4.2 模型存放

```
/mnt/ubm_code_nas/gac_huangzijian/common_data/models/bge-m3/
```

## 5. 与现有代码的集成

| 模块 | 变更 |
|------|------|
| `tag_router.py` TagRouter.route() | **核心变更**: 关键词遍历→向量匹配 |
| `tag_router.py` _MANUAL_KEYWORD_OVERRIDES | 离线索引构建时使用，线上不再遍历 |
| `tag_router.py` _MAP_ENUM_KEYWORDS | 保留，map枚举仍走关键词 |
| `tag_router.py` _kw_to_tags | 删除，被向量索引替代 |
| `schema_reader.py` | 无变更 |
| `agent_engine.py` | 无变更 |

## 6. 实施计划

| Phase | 内容 | 工期 | 前置 |
|-------|------|------|------|
| Phase 1 | 离线验证：下载BGE-M3，构建索引，baseline_queries测试匹配准确率 | 0.5天 | 无 |
| Phase 2 | 集成：修改route()，map枚举保留关键词，DSW部署 | 0.5天 | Phase 1 |
| Phase 3 | E2E验证：前端测试baseline + 同义词泛化（"加塞"→Cutin） | 0.5天 | Phase 2 |

## 7. 风险

| 风险 | 缓解 |
|------|------|
| BGE-M3对短query（2-3字）匹配不稳定 | 短query走关键词兜底 |
| map枚举值语义太短，向量区分度低 | 保留关键词匹配 |
| PPU兼容性（某些算子不支持） | Phase 1先验证 |
| 首次加载模型冷启动~3s | 服务启动时预加载 |
