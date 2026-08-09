# 端到端测试：调用平台查询验证

## 核心原则

**前端页面端到端跑通才算真正通过**——写脚本直接调API不算。但 API 调用是诊断漏斗的必要手段。

## 访问方式

### 方式一：本地通过 SSH 隧道调 API（推荐）

本地机器通过 SSH 隧道直接调 DSW 上的 SceneSQL 后端 API，无需 SSH 到 DSW 上执行脚本。

```bash
# 1. 建立 SSH 隧道（一次性）
ssh -fNL 30001:127.0.0.1:30001 dsw

# 2. 直接调 API
curl -s http://127.0.0.1:30001/api/auth/login -X POST \
  -H "Content-Type: application/json" \
  -d '{"username":"gac","password":"gac_data"}'
```

Python 示例：

```python
import urllib.request, json

BASE = "http://127.0.0.1:30001"

# 1. Login
data = json.dumps({"username":"gac","password":"gac_data"}).encode()
req = urllib.request.Request(f"{BASE}/api/auth/login", data=data,
    headers={"Content-Type":"application/json"})
resp = urllib.request.urlopen(req)
token = json.loads(resp.read().decode())["access_token"]

# 2. Execute SQL
sql = "SELECT * FROM range_tag WHERE tag_name='topology_intersection' AND end_ts-start_ts>2"
body = json.dumps({
    "sql": sql,
    "batch_id": "20260702_T68_2471_c5afa57_100w",
    "query_mode": "sqlite",
    "db_limit": 0,       # 0=不限DB数量
    "result_limit": 50000 # 诊断时设大
}).encode()
req = urllib.request.Request(f"{BASE}/api/agent/execute-sql", data=body,
    headers={"Content-Type":"application/json", "Authorization": f"Bearer {token}"})
resp = urllib.request.urlopen(req, timeout=600)
result = json.loads(resp.read().decode())
# result: {rows, matched_dbs, total_rows, scanned_dbs, columns}
```

### 方式二：DSW 上直接调

当本地隧道不通时，可以 SCP 脚本到 DSW 上执行：

```bash
cat /tmp/test_api.py | ssh dsw 'python3 -'
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/auth/login` | POST | 登录，返回 JWT token |
| `/api/auth/verify` | GET | 验证 token 有效性 |
| `/api/agent/batches` | GET | 获取可用批次列表 |
| `/api/agent/execute-sql` | POST | 执行 SQL（跨所有 DB） |
| `/api/agent/generate-sql` | POST | LLM 生成 SQL |
| `/api/agent/query` | POST | 完整 Agent 查询流程 |
| `/api/agent/query-stream` | POST | SSE 流式 Agent 查询 |
| `/api/agent/execute-sql-arrow` | POST | Arrow 格式执行 SQL |

## execute-sql 参数

| 参数 | 说明 |
|------|------|
| `sql` | SQLite SQL 语句 |
| `batch_id` | 批次 ID（从 `/api/agent/batches` 获取） |
| `query_mode` | `"sqlite"` 或 `"parquet"` |
| `db_limit` | 限制扫描 DB 数，0=不限 |
| `result_limit` | 限制返回行数（诊断时设 50000） |

## 返回值

| 字段 | 说明 |
|------|------|
| `rows` | 结果行（列表，每行是 dict） |
| `matched_dbs` | 有结果的 DB 数 |
| `total_rows` | 所有 DB 返回的行数总和（**这是真实数量**） |
| `scanned_dbs` | 扫描的 DB 总数 |
| `columns` | 列名列表 |

## ⚠ 诊断漏斗必须用 SELECT * + total_rows

**`COUNT(*)` 返回每个DB内的聚合值**，API对每个SQLite DB分别执行SQL。`result_limit` 截断后丢失大部分DB的计数结果，导致严重低估。

**错误做法**：
```sql
SELECT COUNT(*) as cnt FROM range_tag WHERE tag_name = 'INTERSECTION_STRAIGHT'
-- 只返回每个DB的计数行，result_limit截断后只能看到前几个DB
```

**正确做法**：
```sql
SELECT * FROM range_tag WHERE tag_name = 'INTERSECTION_STRAIGHT' AND end_ts - start_ts > 2
-- 看 response 中的 total_rows 字段
```

**实测对比**（100w批次，15460 DBs）：

| 方法 | 得到的数量 | 说明 |
|---|---|---|
| `COUNT(*)` + sum rows | 40 | 严重低估！ |
| `SELECT *` + `total_rows` | **11996** | 真实数量 |

## 漏斗分析法

逐步加条件，每步记录 `total_rows` 和 `matched_dbs`，观察哪一步砍人最多。

```python
funnel_steps = [
    ("topology_intersection", "SELECT * FROM range_tag WHERE tag_name='topology_intersection'"),
    ("+ duration>2", "SELECT * FROM range_tag WHERE tag_name='topology_intersection' AND end_ts-start_ts>2"),
    ("+ successor_count=1", "..."),
    ("+ 红绿灯", "..."),
]

for label, sql in funnel_steps:
    body = {"sql": sql, "batch_id": BATCH, "query_mode": "sqlite",
            "db_limit": 0, "result_limit": 50000}
    # ... 执行并记录 total_rows
```

## 验证方法与 GT 局限性

### GT 来源

验证用的 GT 通常是 `range_tag` 中的某个标签（如 `INTERSECTION_STRAIGHT`）。**这是算法标签，不是人工标注**。

- Precision=1.0 只表示"SQL输出和GT一致"，不代表绝对正确
- Recall=0.84 表示"GT里有N个正样本，找回了84%"

### 没有"绝对正确数"

无法从数据本身知道"到底有多少个直行路口"。只能：
1. 用算法标签当GT，接受局限性
2. 人工抽检一批样本
3. 前端端到端跑通验证

### 200DB 采样发现的问题

- 97个 `topology_intersection` 不与任何 `INTERSECTION_*` 重叠 → 无法判断正负
- 46个 `INTERSECTION_STRAIGHT` 完全不在 `topology_intersection` 内 → 时间范围不对齐

## 注意事项

1. **SSH 隧道先建**：本地调 API 前必须 `ssh -fNL 30001:127.0.0.1:30001 dsw`
2. **timeout**: 全量扫描15460个DB可能需要5-10分钟，设 `timeout=600`
3. **curl 显示 token 截断**：JWT 太长，curl 终端显示会做 `eyJhbG...xxx` 截断，实际 token 完整。用 python 调可避免此问题
4. **SCP脚本而非SSH heredoc**: JSON在heredoc中转义容易出错，写 .py 文件再 `cat | ssh dsw 'python3 -'` 更可靠
