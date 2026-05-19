# SQLite 注入源分析参考

本文档说明如何判断数据挖掘项目中的代码变更是否会影响 SQLite `range_tag` 表的内容。

## 一、注入链路总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  数据源                        处理阶段                      SQLite 目标表   │
├─────────────────────────────────────────────────────────────────────────────┤
│  cloud 端算子 (activity_new/op_*.py)                                       │
│    ├─ add_event()              → events DataFrame                            │
│    └─ 或 add_table("range_tag")→ virtual_table                              │
│                                ↓                                            │
│  tokenizer_processor_new.py    → TokenizerResult.activity_list              │
│                                ↓                                            │
│  to_sqlite_db.py               → _inject_range_tag_to_virtual_table()       │
│                                ↓                                            │
│  to_sqlite_db.py               → _fill_table_from_virtual_table("range_tag")│
│                                ↓                                            │
│                                                                      range_tag│
├─────────────────────────────────────────────────────────────────────────────┤
│  车端 bag 文件 (beh_tag 消息)                                               │
│    └─ em_behavior_tag_parser.py → behavior_tag_list                         │
│                                ↓                                            │
│  mining_pipeline.py            → label_info JSON                            │
│                                ↓                                            │
│  (部分 pipeline 版本)          → 合并到 TokenizerResult → range_tag          │
├─────────────────────────────────────────────────────────────────────────────┤
│  user_workspace 自定义算子                                                  │
│    ├─ add_event()              → 同 cloud 端链路                            │
│    └─ add_table("range_tag")   → 直接写入 virtual_table                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 二、关键文件清单

### 2.1 SQLite 写入核心

| 文件 | 作用 | 变更影响 |
|------|------|---------|
| `L2_Pred/downstream/ubm/to_sqlite_db.py` | 唯一 SQLite 写入器 | **极高** — 修改过滤规则会直接影响哪些标签进入 DB |

关键函数：
- `_inject_range_tag_to_virtual_table()`：从 `TokenizerResult.activity_list` 读取，过滤空 label_id、零时间戳、`EgoIntoIntersection`
- `_fill_table_from_virtual_table("range_tag")`：将 virtual_table 的 range_tag 数据写入 SQLite

### 2.2 Cloud 端算子注册与执行

| 文件 | 作用 | 变更影响 |
|------|------|---------|
| `L2_Pred/rule_based_mining/semantic_mining/tokenizer_processor_new.py` | 算子注册中心 | **高** — 新增/删除/启用/禁用算子 |
| `L2_Pred/rule_based_mining/semantic_mining/activity_new/operator_branch.py` | 算子基类 | **中** — 基类 add_event 逻辑变更影响所有子类 |
| `L2_Pred/rule_based_mining/semantic_mining/activity_new/op_*.py` | 具体算子 | **高** — 新增/修改 add_event 的 label_id |

判断一个算子是否注入 range_tag 的方法：
1. 搜索文件中 `add_event(` 调用
2. 追踪 `label_id` 的赋值（可能是硬编码字符串，也可能是变量）
3. 确认算子是否在 `tokenizer_processor_new.py` 中被 `register_operator()` 注册
4. 确认 `enabled` 状态（默认 true，除非显式设为 false）

### 2.3 User Workspace 自定义算子

| 文件 | 作用 | 变更影响 |
|------|------|---------|
| `user_workspace/*/operator_registry.json` | 自定义算子注册表 | **中** — enabled 状态变化 |
| `user_workspace/*/*.py` | 自定义算子实现 | **高** — 新增 add_event 或 add_table("range_tag") |

注意：
- `add_event()` 走的还是标准链路
- `add_table("range_tag", ...)` 绕过 activity_list，直接进 virtual_table
- 需要查看 `_register_custom_operator()` 的加载逻辑

### 2.4 车端行为标签

| 文件 | 作用 | 变更影响 |
|------|------|---------|
| `gsbag_parser/topic_parser/em_behavior_tag_parser.py` | 解析 bag 中的 beh_tag 消息 | **高** — 新增标签类型支持 |
| `gsbag_parser/tag_map.py` | 车端标签 ID → 英文名的映射 | **高** — 新增映射即新增潜在标签 |
| `gsbag_parser/em_parser.py` | 调用 behavior_tag_parser | **低** — 通常是框架性变更 |
| `mining_pipeline.py` | 保存 label_info 到 JSON | **中** — 是否合并到 events 的逻辑 |

车端标签的命名规范：
- 全部大写 + 下划线分隔
- 前缀表示类别：`CRUISE_*`, `STOPANDGO_*`, `LANECHANGE_*`, `AVOIDANCE_*`, `INTERSECTION_*`
- 通过 `reflectDict` 和 `refDictEn` 映射

## 三、常见变更模式与判断

### 模式 A：新增算子文件 `op_xxx.py`

检查项：
1. 文件内是否有 `add_event(` 调用？
2. `label_id` 是固定字符串还是变量？
3. 算子是否在 `tokenizer_processor_new.py` 中注册？
4. 是否有 `add_table("range_tag")` 调用？

### 模式 B：修改现有算子

检查项：
1. `add_event` 的 `label_id` 是否改变？
2. 是否新增/删除了 `add_event` 调用？
3. 触发条件是否改变（可能影响标签出现频率）？

### 模式 C：修改 `to_sqlite_db.py`

检查项：
1. `_inject_range_tag_to_virtual_table()` 的过滤条件是否改变？
   - 当前过滤：`EgoIntoIntersection`、空 label_id、零时间戳
2. 是否有新增表或列？
3. `virtual_table` 的处理逻辑是否改变？

### 模式 D：修改 `tag_map.py`

检查项：
1. `reflectDict` 中是否新增主类别？
2. `refDictEn` 中是否新增映射？
3. 新增的标签是否会在 `em_behavior_tag_parser.py` 中被解析？

## 四、快速诊断命令

```bash
# 1. 查看某个算子的 add_event 调用
grep -n "add_event\|label_id" L2_Pred/rule_based_mining/semantic_mining/activity_new/op_xxx.py

# 2. 查看算子注册列表
grep -n "register_operator" L2_Pred/rule_based_mining/semantic_mining/tokenizer_processor_new.py

# 3. 查看 to_sqlite_db 的 range_tag 注入逻辑
grep -n "_inject_range_tag\|_fill_table_from_virtual_table.*range_tag" L2_Pred/downstream/ubm/to_sqlite_db.py

# 4. 查看车端标签映射
grep -n "refDictEn" gsbag_parser/tag_map.py

# 5. 查看 user_workspace 自定义算子
for f in user_workspace/*/*.py; do echo "=== $f ==="; grep -n "add_event\|add_table.*range_tag" "$f"; done
```

## 五、Schema 更新 checklist

当检测到以下变更时，需要更新 schema：

- [ ] 新增/删除 cloud 端算子（影响 `range_tag.enum`）
- [ ] 算子的 `label_id` 变更（影响 `range_tag.enum` 和 `tags` 字典）
- [ ] 新增/删除 user_workspace 算子（影响 `range_tag.enum`）
- [ ] `to_sqlite_db.py` 过滤规则变更（影响注入源说明）
- [ ] `tag_map.py` 新增映射（影响车端标签列表）
- [ ] `em_behavior_tag_parser.py` 新增标签类型支持
- [ ] 新增/删除 SQLite 表或列（影响 `database_schema.tables`）
