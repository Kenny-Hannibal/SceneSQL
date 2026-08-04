# SceneSQL API 契约 v1（DataMining 集成对接）

> 本文档是 SceneSQL 作为 DataMining 旁车服务（SQL 生成引擎）的对外接口契约。
> Java 侧 `SceneSqlClient` 的实现以本文档为准。
>
> **版本**: v1 | **日期**: 2026-08-03

## 1. 兼容性规则（版本承诺）

1. **只加不改**：v1 契约内字段的名称、类型、语义不得变更；新增能力只能新增可选字段（请求侧带默认值）或新增响应字段。
2. **破坏性变更必须换版本**：新契约走 `/api/v2/...` 路径并存，v1 至少保留一个迭代周期。
3. **镜像 tag 即版本**：DataMining 通过 `scenesql:<tag>` 镜像切换 SceneSQL 版本；tag 语义遵循 `v<major>.<minor>-<desc>`，同一 major 内契约兼容。
4. **发版前必须跑**：`tests/run_batch_test.py` 基线评测 + 本文档 §6 契约冒烟用例。

## 2. 通用约定

| 项 | 值 |
|----|----|
| Base URL | `http://<host>:30001`（容器内固定 30001，可用 `PORT` 环境变量改） |
| 认证 | JWT Bearer。除 `/health`、`/`、`/api/auth/login` 外全部接口需 `Authorization: Bearer <token>` |
| 401 处理 | token 过期返回 401，客户端应重新 login 后重试一次 |
| 时间戳 | DB 内 `start_ts`/`end_ts` 为**秒级** Unix 时间戳；视频接口参数为**纳秒** |
| Content-Type | `application/json; charset=utf-8` |

### 2.1 登录

```
POST /api/auth/login
请求:  {"username": "...", "password": "..."}
200:   {"access_token": "<jwt>", "token_type": "bearer"}
401:   {"detail": "Invalid username or password"}
```

凭据由服务端环境变量 `AUTH_USERNAME` / `AUTH_PASSWORD` 配置。

```
GET /api/auth/verify    (带 Bearer)
200: {"valid": true, "user": "<username>"}
```

### 2.2 健康检查

```
GET /health
200: {"status": "ok"}
```

## 3. NL2SQL 核心接口（prefix `/api/agent`）

### 3.1 生成 SQL（不执行）★ DataMining 主用接口

```
POST /api/agent/generate-sql
```

请求体（`AgentQueryRequest`，仅以下字段对本接口有效）：

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| question | string | ✓ | - | 自然语言问题 |
| batch_id | string | 与 db_path 二选一 | - | 数据批次 ID |
| db_path | string | 与 batch_id 二选一 | - | 直接指定 DB 路径/目录 |
| query_mode | string | ✗ | 服务端默认 | `"sqlite"` \| `"parquet"` |

成功响应：

```json
{
  "sql": "SELECT ... start_ts, end_ts ...",
  "validation_error": null,
  "route_method": "recipe|keyword|vector|llm|...",
  "matched_tags": ["Cutin"],
  "involved_tables": ["range_tag", "dynamic_obj"]
}
```

失败响应（HTTP 200 + error 字段，非异常状态码）：

```json
{"sql": "", "error": "<错误描述>"}
```

> Java 侧判定成功的条件：`sql` 非空且 `error` 为空。

### 3.2 完整查询（生成 + 执行）

```
POST /api/agent/query
```

请求体（`AgentQueryRequest` 全量）：§3.1 字段 +

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| db_limit | int | 30 | 批量模式最多扫描的 DB 数 |
| result_limit | int | 100 | 查够 N 条即返回（不是只扫 N 个 DB） |
| page | int | 1 | 页码（从 1 开始） |
| page_size | int | 50 | 每页行数 |
| max_workers | int | 32 | 批量并发数 |

响应（`AgentQueryResponse`）：

```json
{
  "sql": "SELECT ...",
  "explanation": "...",
  "columns": ["bag_id", "tag_name", "start_ts", "end_ts"],
  "rows": [{"bag_id": "...", "start_ts": 1234.5, "end_ts": 1240.1}],
  "error": null,
  "scanned_dbs": 150,
  "matched_dbs": 5,
  "total_rows": 50,
  "page": 1,
  "page_size": 50,
  "correction_rounds": 1,
  "max_corrections_exceeded": false
}
```

> 集成提示：DataMining 侧若用自己的 `BatchSearchSqlite` 执行，只需 §3.1；
> 本接口主要用于回归评测与无自有执行器的场景。

### 3.3 直接执行 SQL（不经 LLM）

```
POST /api/agent/execute-sql
请求体: ExecuteSQLRequest = {sql 必填} + §3.2 的执行参数
响应:   同 AgentQueryResponse
```

### 3.4 流式查询（SSE，可选对接）

```
POST /api/agent/query-stream
Content-Type: text/event-stream
```

事件阶段：`understanding` / `generating_token` / `recipe_hit` / `sql_generated` / `completed` / `error`。
v1 内事件名与 payload 结构冻结；Java 侧若做思考过程透传，需映射到 `ThinkingEvent`。

### 3.5 Arrow 直传（可选）

```
POST /api/agent/execute-sql-arrow
请求体同 execute-sql；响应为 Apache Arrow IPC 二进制流
```

## 4. 策略接口（prefix `/api/strategies`）

用户自定义策略（Recipe 覆盖），YAML 持久化在服务端。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/strategies` | 列表，返回 `StrategyInfo[]` |
| GET | `/api/strategies/{name}` | 详情 |
| POST | `/api/strategies` | 创建（同名覆盖语义由服务端保证） |
| PUT | `/api/strategies/{name}` | 更新（字段可选） |
| DELETE | `/api/strategies/{name}` | 删除 |

`StrategyCreateRequest`：

```json
{"name": "high_speed_cutin", "keywords": ["高速切入"], "tag_name": "Cutin",
 "sql": "SELECT ...", "description": ""}
```

`StrategyInfo` 额外含 `created_at` / `updated_at`（epoch 秒，可能为 null）。

## 5. 部署契约

### 5.1 镜像

- 构建：`./docker-build.sh <tag>`（仓库内，对齐 DataMining build.sh 风格）
- 镜像内容：FastAPI 后端 + 前端静态页面；**不含 gsbag C++ SDK**（bag 视频功能自动降级，NL2SQL 不受影响）
- 架构：linux/amd64，Python 3.10

### 5.2 必需环境变量

| 变量 | 说明 |
|------|------|
| OPENAI_API_KEY / OPENAI_BASE_URL / AGENT_MAIN_MODEL | LLM 配置（可切 DashScope 兼容端点） |
| AUTH_USERNAME / AUTH_PASSWORD | JWT 登录凭据 |
| SQLITE_DB_PATH | SQLite DB 根目录（batch_id 拼接 `{SQLITE_DB_PATH}/sqlite_dbs/{batch_id}`） |

可选：`AGENT_FALLBACK_MODEL`、`ETL_BASE_PATH` + `ETL_BATCH_ID`（parquet 模式）、`QUERY_MODE`、`HTTPS_PROXY`。

### 5.3 数据卷

DB 目录以只读卷挂载：`-v <宿主机DB目录>:/data -e SQLITE_DB_PATH=/data`。

## 6. 契约冒烟用例（发版必跑）

```bash
BASE=http://127.0.0.1:30001
TOKEN=$(curl -s -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"'$AUTH_USERNAME'","password":"'$AUTH_PASSWORD'"}' | jq -r .access_token)

# 1. 健康
curl -sf $BASE/health | grep -q '"status":"ok"'

# 2. generate-sql 返回非空 sql 且含 start_ts/end_ts
curl -s -X POST $BASE/api/agent/generate-sql \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"找出cutin场景","batch_id":"<BATCH>","query_mode":"sqlite"}' | jq -e '.sql | contains("start_ts")'

# 3. query 返回 total_rows >= 0 且 columns 含 start_ts/end_ts
curl -s -X POST $BASE/api/agent/query \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"找出cutin场景","batch_id":"<BATCH>","query_mode":"sqlite","db_limit":10,"result_limit":5}' \
  | jq -e '.columns | index("start_ts") != null and index("end_ts") != null'
```

## 7. DataMining 集成映射（参考）

| DataMining（Java） | SceneSQL 契约 |
|--------------------|---------------|
| `Text2SqlGraph` generate 节点（灰度开关） | §3.1 `generate-sql` |
| `BatchSearchSqlite` 执行 | 保持不变，SQL 来自 §3.1 |
| `SqlStrategyService` few-shot | 迁移/同步到 §4 策略接口（以 SceneSQL 为权威） |
| Schema（OSS schema.sql） | 统一到 SceneSQL `schema_master.yaml` 派生链（ubm-schema-sync） |
| `ThinkingEvent` SSE | §3.4 事件映射（可选） |
