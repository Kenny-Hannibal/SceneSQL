---
name: development-workflow
description: >
  SceneSQL 项目开发流程规范 — 部署、代码同步、端到端测试、测试报告、CHANGELOG 的强制规则。
  每次修改代码后必须遵循此流程，无例外。
  触发条件：任何涉及 SceneSQL 代码修改的操作。
tags: [workflow, testing, deployment, changelog, scene-sql]
version: 1.0
---

# SceneSQL 开发流程规范

> 本文档是 SceneSQL 项目的强制开发流程规范。每次修改代码后必须按此流程执行，无例外。

## 1. 核心原则

1. **前端才是交付产品** — 只有在前端页面端到端跑通才算真正通过
2. **E2E测试通过API模拟用户操作** — 不写外部脚本直接操作DB/文件系统，而是调平台API模拟用户流程（选batch→输入NL→生成SQL→执行查询→验证结果），等同于用户在前端点击按钮
3. **Git 是唯一代码同步方式** — 禁止 SCP/SSH 传文件同步代码，统一使用 git push/pull
4. **每改必测** — 每次代码修改都必须进行端到端测试，不仅看"是否报错"
5. **每改必报** — 每次代码修改都必须写测试报告 + 更新 CHANGELOG.md
6. **SQL 质量校验** — 测试不仅要看执行是否报错，还要检验生成的 SQL 语句是否正确

## 2. 代码修改流程（强制执行）

每次代码修改必须按以下步骤执行：

### Step 1: 代码修改

在本地修改代码，确保语法正确（`py_compile` / 前端 build 无错）。

### Step 2: 更新 CHANGELOG.md

路径：`/data/var/workspace/projects/projects/SceneSQL/CHANGELOG.md`

在文件顶部（最新版本条目之后）添加本次变更记录：

```markdown
## [YYYY-MM-DD] 变更标题（版本号）

**Commit**: (commit hash，推送后填写)
**测试报告**: `test_reports/vX.X-xxx-test-report.md`

### 新增/修复/变更

1. **变更内容描述** — 涉及文件

### 涉及文件

- `path/to/changed/file.py` — 变更说明

### 测试验证

- 测试结果概述
- 详见 `test_reports/vX.X-xxx-test-report.md`
```

### Step 3: Commit + Push

```bash
cd /data/var/workspace/projects/projects/SceneSQL
git add -A
git commit -m "feat/fix/docs: 简明描述"
git push origin master
```

### Step 4: DSW 部署

```bash
ssh -o ConnectTimeout=15 dsw "cd /root/data/text2sql && git pull origin master && bash visualizer/deploy.sh -f"
```

如果 SSH 连接失败（被限流），等待 1-2 分钟后重试。

### Step 5: 端到端测试（强制）

**核心逻辑：通过API模拟用户操作流程，不写外挂脚本直接操作DB/文件系统。**

用户在前端点击按钮的本质就是调API。E2E测试就是通过API走完用户完整操作链路：
1. 指定batch_id → 2. 输入NL问题 → 3. LLM生成SQL → 4. 执行SQL查询 → 5. 验证结果

#### 5.1 平台连接信息

| 项 | 值 |
|-----|-----|
| 平台地址 | `http://8.130.175.37:30001`（DSW公网） |
| 本机连接 | **无需SSH到DSW**，本机通过HTTP代理直连平台 |
| HTTP代理 | `http://127.0.0.1:18888`（sing-box，已配置DSW 30001端口走domestic-proxy） |
| 健康检查 | `curl -x http://127.0.0.1:18888 http://8.130.175.37:30001/health` |
| 前端页面 | `http://8.130.175.37:30001`（浏览器直接访问，已配sing-box路由，无需手动指定18888代理） |

#### 5.2 E2E测试API端点

**所有请求从本机发出，经HTTP代理`http://127.0.0.1:18888`到达DSW平台。**

##### A. 生成SQL（不执行）

```
POST /api/agent/generate-sql
```

请求体：
```json
{
  "question": "找出cutin场景",
  "batch_id": "20260616_T68_2434_c5afa57_1.5w",
  "query_mode": "sqlite"
}
```

返回：
```json
{
  "sql": "SELECT ...",
  "validation_error": null,
  "route_method": "keyword",
  "matched_tags": ["Cutin", "navi_other"],
  "involved_tables": ["dynamic_obj", "range_tag"]
}
```

##### B. 完整查询流程（生成SQL + 执行 + 返回结果）

```
POST /api/agent/query
```

请求体：
```json
{
  "question": "找出cutin场景",
  "batch_id": "20260616_T68_2434_c5afa57_1.5w",
  "query_mode": "sqlite",
  "db_limit": 30,
  "result_limit": 50,
  "page": 1,
  "page_size": 50
}
```

**`result_limit` 的含义**：查询到设定数量的场景就返回，而非在设定数量的DB下搜索。例如`result_limit=50`意味着查够50条结果就停止，而不是只搜50个DB文件。这是验证SQL正确性的关键参数——设定合理数量即可快速验证。

返回：
```json
{
  "sql": "SELECT ...",
  "explanation": "...",
  "columns": ["bag_id", "start_ts", "end_ts", "tag_name", ...],
  "rows": [...],
  "error": null,
  "scanned_dbs": 150,
  "matched_dbs": 5,
  "total_rows": 50,
  "page": 1,
  "page_size": 50
}
```

##### C. 直接执行SQL（不经过LLM）

```
POST /api/agent/execute-sql
```

请求体：
```json
{
  "sql": "SELECT * FROM range_tag WHERE tag_name = 'Cutin' LIMIT 10",
  "batch_id": "20260616_T68_2434_c5afa57_1.5w",
  "query_mode": "sqlite",
  "db_limit": 30,
  "result_limit": 50
}
```

用于验证/修正LLM生成的SQL。

#### 5.3 curl调用方式（从本机）

```bash
# 生成SQL（不执行）
curl -s -x http://127.0.0.1:18888 -X POST \
  http://8.130.175.37:30001/api/agent/generate-sql \
  -H "Content-Type: application/json" \
  -d '{"question":"找出cutin场景","batch_id":"20260616_T68_2434_c5afa57_1.5w","query_mode":"sqlite"}'

# 完整查询（生成+执行+结果）
curl -s -x http://127.0.0.1:18888 -X POST \
  http://8.130.175.37:30001/api/agent/query \
  -H "Content-Type: application/json" \
  -d '{"question":"找出cutin场景","batch_id":"20260616_T68_2434_c5afa57_1.5w","query_mode":"sqlite","db_limit":30,"result_limit":50}'

# 直接执行SQL
curl -s -x http://127.0.0.1:18888 -X POST \
  http://8.130.175.37:30001/api/agent/execute-sql \
  -H "Content-Type: application/json" \
  -d '{"sql":"SELECT * FROM range_tag WHERE tag_name = '"'"'Cutin'"'"'","batch_id":"20260616_T68_2434_c5afa57_1.5w","query_mode":"sqlite","db_limit":30,"result_limit":10}'
```

#### 5.4 E2E测试检查项

| 检查项 | 要求 | 如何验证 |
|--------|------|---------|
| SQL 生成 | API返回sql字段非空 | 检查`generate-sql`返回的sql |
| SQL 执行 | API返回无error | 检查`query`返回的error字段 |
| 结果行数 | total_rows > 0 | 检查`query`返回的total_rows |
| start_ts + end_ts | columns中必须包含 | 检查`query`返回的columns |
| SQL 逻辑 | SQL语句逻辑正确 | 审查SQL内容 |
| sql_source | recipe命中应为recipe,LLM为llm | 检查route_method和matched_tags |
| 数据合理性 | 行数在合理范围（0或超大可能异常） | 检查matched_dbs和total_rows |

**SQL 逻辑审查要点**：
- 表名是否正确（不是 LLM 编造的）
- JOIN 条件是否正确（时间戳单位是否一致，禁止 `*1e9`）
- WHERE 条件是否完整（没有遗漏用户要求的过滤条件）
- SELECT 字段是否包含 start_ts、end_ts
- range_tag无ego_link_id列，跨表查询须通过ego.ts桥接
- speed单位是m/s，120km/h = 33.3m/s
- SQLite不兼容：无GREATEST/LEAST/->>/EXTRACT/ILIKE

### Step 6: 写测试报告

路径：`/data/var/workspace/projects/projects/SceneSQL/test_reports/`

文件命名：`vX.X-描述-test-report.md`（如 `v0.4-beta-test-report.md`）

测试报告必须包含以下结构：

```markdown
# SceneSQL vX.X-xxx 测试报告

> **版本**: vX.X-xxx
> **日期**: YYYY-MM-DD
> **测试人**: Coder Agent

## 1. 版本概述
简要说明本次版本的主要变更。

## 2. 代码修改总结
列出所有修改的文件及变更内容：
| 文件 | 变更类型 | 说明 |
|------|---------|------|

## 3. 测试总结
### 3.1 端到端测试结果
| # | NL查询 | 命中Recipe/概念 | sql_source | 行数 | start_ts | end_ts | SQL逻辑 | 结果 |
|---|--------|---------------|-----------|------|----------|--------|---------|------|

### 3.2 SQL 逻辑审查
对每个测试用例生成的 SQL 进行逻辑审查，记录发现的问题。

### 3.3 已知问题
| # | 问题 | 影响 | 计划 |
|---|------|------|------|

## 4. 下一步计划
如果大功能尚未完成，列出后续工作：
| 优先级 | 任务 | 预计工作量 |
|--------|------|-----------|

（如果一个大功能已经完成，此节可省略）
```

## 3. 部署规则

### 3.1 DSW 部署

- **SSH 命令**: `ssh dsw`（已配置 SSH config 别名）
- **部署命令**: `cd /root/data/text2sql && git pull origin master && bash visualizer/deploy.sh -f`
- **健康检查**: `ssh dsw "curl -s http://127.0.0.1:30001/health"`
- **DSW 不会重启**: DSW 是工业机，不做 systemd 自启，不执行 reboot

### 3.2 代码同步（Git Only）

**禁止 SCP/SSH 传文件同步代码。统一使用 git push/pull 管理。**

流程：
1. 本地改代码 → `git add -A && git commit -m "msg" && git push origin master`
2. DSW: `git pull origin master` → `bash visualizer/deploy.sh -f`
3. 如果本地 push 失败（网络/冲突），先在 DSW 提交→强推，本地再 `git fetch && git reset --hard origin/master`

### 3.3 环境变量

- `.env` 变量优先级高于 `config.yaml`，残留变量会静默覆盖配置
- DSW 的 `.env` 必须通过 `set -a && source .env && set +a` 加载

## 4. 测试纪律

### 4.1 测试不仅要看"是否报错"

| 错误做法 ✗ | 正确做法 ✓ |
|-----------|----------|
| 只看 API 返回 200 就算通过 | 检查返回的 SQL 语句内容 |
| 只看"无报错"就当通过 | 检查 SQL 的 WHERE/JOIN 逻辑 |
| 只看返回行数 > 0 | 检查 start_ts/end_ts 列是否存在 |
| 写Python脚本直接连SQLite查DB | 通过平台API（generate-sql / query / execute-sql）走完整用户流程 |
| SSH到DSW上写脚本跑SQL | 本机curl调API即可，无需SSH |

### 4.2 SQL 质量校验清单

每次测试必须对生成的 SQL 进行以下校验：

1. **表名正确性** — 无 LLM 编造的表名（如 `e.position_x` → 应为 `e.utm_x`）
2. **时间戳单位** — 禁止 `*1e9`，所有时间字段都是秒级 Unix 时间戳，直接比较
3. **SELECT 完整性** — 必须包含 `start_ts` 和 `end_ts`
4. **JOIN 条件** — 跨表 JOIN 使用 `ego.ts BETWEEN r.start_ts AND r.end_ts`（直接比较，无转换）
5. **SQLite 兼容性** — 无 `->>` 运算符（需 3.38+，DSW 是 3.37.2）、无 `json_extract_string`、无 `EXTRACT`、无 `ILIKE`
6. **WHERE 条件完整性** — 未遗漏用户查询要求的过滤条件
7. **tag_name 正确性** — 使用实际存在的 tag_name（如 `INTERSECTION_LEFTTURN`，不是 LLM 编造的）

### 4.3 Recipe vs LLM SQL 区别

| 检查项 | Recipe SQL | LLM SQL |
|--------|-----------|---------|
| 语法错误 | 模板 bug，不走 LLM 纠错，开发者直接修 | 走 Round 3 纠错循环（最多 3 次） |
| start_ts/end_ts | 已全量验证，免检 | 必须校验，缺失触发纠错 |
| SQL 逻辑 | 与产线 SQL 一致，人工审查一次即可 | 每次生成可能不同，必须逐条审查 |

## 5. 测试报告规则

### 5.1 何时写测试报告

- **每次版本迭代**（v0.1, v0.2, v0.3...）必须写
- **每次重大功能完成**后必须写
- **不写测试报告 = 版本未完成**

### 5.2 测试报告必须包含

| 章节 | 是否必须 | 说明 |
|------|---------|------|
| 版本概述 | ✓ | 主要变更概述 |
| 代码修改总结 | ✓ | 修改的文件列表及变更说明 |
| 测试总结 | ✓ | 端到端测试结果 + SQL 逻辑审查 |
| 已知问题 | ✓ | 即使没有也要写"无已知问题" |
| 下一步计划 | 条件必须 | 大功能未完成时必须写；大功能已完成可省略 |

### 5.3 测试报告路径

`/data/var/workspace/projects/projects/SceneSQL/test_reports/`

命名规范：`vX.X-描述-test-report.md`

## 6. CHANGELOG 规则

### 6.1 何时更新 CHANGELOG

**每次代码修改都必须更新 CHANGELOG.md**，无例外。

### 6.2 CHANGELOG 条目格式

```markdown
## [YYYY-MM-DD] 变更标题（版本号）

**Commit**: commit_hash
**测试报告**: `test_reports/vX.X-xxx-test-report.md`

### 新增 / 修复 / 变更

1. **变更描述** — 涉及文件

### 涉及文件

- `path/to/file` — 变更说明

### 测试验证

- 测试结果概述
- 详见 `test_reports/vX.X-xxx-test-report.md`
```

## 7. 版本发版流程

当完成一个里程碑版本（如 v0.4-beta）时：

1. 确认所有端到端测试通过
2. 写测试报告
3. 更新 CHANGELOG.md
4. `git tag -a vX.X-beta -m "summary + known limitations"`
5. `git push origin master --tags`
6. DSW 部署 + 前端验证

## 8. 禁止事项

| # | 禁止 | 原因 |
|---|------|------|
| 1 | SCP/SSH 传文件同步代码 | Git 是唯一代码同步方式 |
| 2 | 写Python脚本直接连SQLite查DB做测试 | E2E测试必须通过平台API走完整用户流程 |
| 3 | SSH到DSW写脚本跑SQL验证 | 本机curl调API即可，无需SSH |
| 4 | 只看"无报错"就当测试通过 | 必须检验 SQL 逻辑 + 结果列 + 行数合理性 |
| 5 | 修改代码后不写测试报告 | 每改必报 |
| 6 | 修改代码后不更新 CHANGELOG | 每改必记录 |
| 7 | DSW 执行 reboot | DSW 是工业机，永不重启 |
| 8 | 为每个标签组合写死模板 | 不可扩展，应使用通用可组合模板 |
| 9 | 在 LLM SQL 中使用 `*1e9` | 所有时间字段都是秒级，无需转换 |
| 10 | 使用`/mnt/gacrnd-oss/`路径查询 | OSS慢，必须用`/mnt/ubm_code_nas/`(NAS) |

## 9. 关键环境信息

| 项 | 值 |
|-----|-----|
| DSW SSH | `ssh dsw`（Host: 8.130.175.37, Port: 1021, User: root） |
| DSW 部署 | `cd /root/data/text2sql && git pull origin master && bash visualizer/deploy.sh -f` |
| 平台API | `http://8.130.175.37:30001`（本机经HTTP代理`http://127.0.0.1:18888`访问） |
| 前端页面 | `http://8.130.175.37:30001`（浏览器直连，已配sing-box路由） |
| 健康检查 | `curl -x http://127.0.0.1:18888 http://8.130.175.37:30001/health` |
| SQLite DB路径 | `/mnt/ubm_code_nas/gac_huangzijian/common_data/sqlite_dbs/` **（NAS，速度快，优先使用）** |
| OSS DB路径 | `/mnt/gacrnd-oss/gac_huangzijian/common_data/sqlite_dbs/` **（OSS，速度慢，避免使用）** |
| SQLite 版本 | 3.37.2（不支持 `->>` 运算符） |
| GitHub | `git@github.com:Kenny-Hannibur/SceneSQL.git` |
| 测试报告 | `/data/var/workspace/projects/projects/SceneSQL/test_reports/` |
| CHANGELOG | `/data/var/workspace/projects/projects/SceneSQL/CHANGELOG.md` |

### 9.1 SQLite DB路径说明

- **`/mnt/ubm_code_nas/`** = NAS挂载，传输速度快，**SQL查询和E2E测试应使用此路径**
- **`/mnt/gacrnd-oss/`** = OSS对象存储挂载，传输速度慢，**避免用于查询**
- DSW `.env` 中 `SQLITE_DB_PATH` 应设为 `/mnt/ubm_code_nas/gac_huangzijian/common_data`
- API的`batch_id`参数会自动拼接 `SQLITE_DB_PATH/sqlite_dbs/{batch_id}`

### 9.2 E2E测试API速查

| 操作 | 端点 | 说明 |
|------|------|------|
| 生成SQL | `POST /api/agent/generate-sql` | 仅生成SQL，不执行 |
| 完整查询 | `POST /api/agent/query` | 生成SQL + 执行 + 返回结果（**E2E测试主力**） |
| 执行SQL | `POST /api/agent/execute-sql` | 直接执行SQL，不经过LLM（用于验证/修正） |
| 流式查询 | `POST /api/agent/query-stream` | SSE流式（前端默认用这个，测试可用非流式query） |
