# SceneSQL v2.0 开发计划

## 阶段1：Schema接入Prompt（预计0.5天）

### 目标
让LLM在生成SQL时能看到完整schema信息（包括map表枚举），解决"schema补了但LLM看不到"的问题。

### 任务清单
1. **审查 `build_prompt()`** — 确认当前prompt中注入了哪些schema信息
2. **确认缺口** — map表的枚举值（link_type 20个、lane_type 11个等）是否被注入
3. **修改prompt模板** — 将schema_structure.yaml中所有表的enum_columns注入到prompt
4. **控制prompt长度** — 枚举值全部注入可能很长，需评估token消耗
5. **DSW部署验证** — 发一个涉及map表查询的NL，确认LLM能正确引用枚举值

### 关键文件
- `agent/backend/app/core/tag_router.py` — `build_prompt()` 方法
- `agent/backend/app/core/schema_structure.yaml` — 派生schema（已含map表枚举）
- `agent/backend/app/core/schema_dictionary.yaml` — 标签字典描述

---

## 阶段2：组合标签基准测试（预计1-2天）

### 目标
测试DeepSeek v4在给定完整schema信息后的组合推理能力，确定v2.0架构方向。

### 前置条件
- 阶段1完成（LLM能看到所有schema信息）

### 测试集设计

#### L1: 单标签查询（已有，验证基础能力）
- "找出会车场景" → 1个tag，1个表

#### L2: 双标签AND查询（核心测试）
- "找出路口且有绿灯的场景" → 2个tag，range_tag自JOIN
- "找出路口且有红绿灯的场景" → 2个tag
- "找出匝道入口的场景" → 1个tag + 1个map表条件
- "找出主路上有cutin的场景" → 1个range_tag + 1个static_link条件

#### L3: 三标签+时间关系查询
- "找出先经过路口，然后绿灯亮起时前车未起步的场景" → 2个tag + 时序关系
- "找出高速公路上跟车过近的场景" → 1个range_tag + 1个static_link.link_type条件

#### L4: 用户指定返回列
- "找出路口绿灯场景，返回bag_id和start_ts" → 2个tag + 指定SELECT列
- "找出所有cutin场景，返回bag_id和时间范围" → 1个tag + 特定列

### 评估指标
1. **标签拆解正确率**：LLM是否正确识别出所有需要的tag_name
2. **SQL语法正确率**：生成的SQL是否可执行
3. **SQL语义正确率**：SQL逻辑是否正确表达用户意图
4. **执行结果非空率**：查询结果是否返回数据
5. **与产线SQL对比**：是否与模板SQL等价

### 执行方式
- 通过前端页面发NL query（不用脚本绕过API）
- 每个query记录：输入NL → LLM输出SQL → 执行结果 → 评分
- 测试集大小：20-30个query

### 产出
- 基准测试报告（含每个query的详细结果和失败分析）
- v2.0架构建议（基于测试结果的"LLM自由组合" vs "模板约束"路线选择）

---

## 阶段3：BGE-M3 语义路由（预计1.5天）

### 目标
用 BGE-M3 向量匹配替代关键词子串匹配，解决 222 tag / 626 关键词手写同义词不可扩展的问题。

### 详细设计
→ 参见 [DESIGN_bge_m3_routing.md](./DESIGN_bge_m3_routing.md)

### 任务清单
1. **Phase 1 离线验证（0.5天）**：下载 BGE-M3 到 DSW，构建 222 个 tag 的 embedding 索引，用 baseline_queries.json 测试匹配准确率，确认阈值
2. **Phase 2 集成到 tag_router（0.5天）**：修改 route() 为向量匹配，map 枚举保留关键词，DSW 部署
3. **Phase 3 E2E 验证（0.5天）**：前端测试 baseline + 同义词泛化（"加塞"、"挤进来"→Cutin），与关键词方案对比

### 关键决策
- **纯向量**替代关键词+向量 fallback，避免维护两套系统
- map 枚举（"主路"→link_type='主路'）因文本过短保留关键词精确匹配
- PPU-ZW810E (96GB) 部署 BGE-M3，预计 <10ms/query

---

## 时间估算

| 阶段 | 工作量 | 前置依赖 |
|------|--------|---------|
| 阶段1: Schema接入Prompt | 0.5天 | 无 |
| 阶段2: 组合标签基准测试 | 1-2天 | 阶段1 |
| 阶段3: BGE-M3 语义路由 | 1.5天 | 阶段2 |
| **合计** | **3-4天** | |

---

## 注意事项
- 所有SQL测试必须通过前端页面进行
- 测试报告保存到 `/data/var/workspace/projects/projects/SceneSQL/test_reports/`
- E2E测试必须验证start_ts+end_ts列存在
