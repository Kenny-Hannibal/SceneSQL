# LLM 标签开发交接手册（Schema / SQL→策略→评测集 / Spark 批量打标→数据集）

> [版本归属] 接手人以本副本（SceneSQL 仓库内 `docs/gac/`，随 clone 分发）为准；projects 根目录 `docs/gac/` 下的同名文件为原作者工作区副本，可能滞后。

> 整理日期：2026-08-31（黄梓建交接用）
> 本文档路径：`/data/var/workspace/projects/projects/docs/gac/LLM标签开发交接手册.md`（唯一总入口）
> 读者：接手同事。你有自己的 DSW 平台机器（非原 8.130.209.216）和公司 Qoder。
> 本文档**自包含**：所有关键事实已内联，不依赖任何个人机器上的记忆库。
> 文中 `<DSW>` = 你的 DSW 机器，`<SCENESQL_URL>` = 你的 SceneSQL 服务地址
> （如 `http://<你的DSW公网IP>:30001`）。原环境的实际值汇总在附录 A。

---

## 0. 交接总览

### 0.1 这套东西是什么

「LLM 标签开发」= 用 LLM 辅助，把自然语言场景需求开发成可批量执行的场景标签，
最终产出两样东西：**策略**（可复用、可发版的打标 SQL）和**评测集**（验证过的正/负样本）。

两条并行链路：

```
链路 A（小规模迭代验证，SceneSQL 服务）
  写SQL → POST /api/agent/execute-sql（sqlite 批次） → 可视化/抽帧打标
        → POST /api/strategies（SQL 链入策略列表、发版）
        → POST /api/eval-labels + sync-evalset（打标结果链入评测集）

链路 B（大规模批量打标，独立脚本，不依赖任何服务）
  脚本包：SceneSQL/scripts/spark_toolkit/（随仓库分发，拿到即可跑）
  ① task_submit_sqlitedb_query.py 直接调 EMR Serverless Spark OpenAPI 提交
     spark_sqlitedb_query_job.py（百万级 SQLite DB 分布式打标）
  ② 结果落湖仓表 gac_dlf.default.sqlite_query_result_table（每次运行一个 sql_id）
  ③ run_to_dataset.sh（preview/test/full）按 sql_id 捞结果 → 倒查车型 → 写数据集
```

两条链路的衔接：链路 A 迭代验证好的策略（其 SQL），把 `.sql` 文件上传到作业读取的
OSS 规则目录后，即可在链路 B 批量打标（§3.1）。
（另有基于 DataMining 服务的等价 API 版链路，属可选产线通道，见 §3.5。）

### 0.2 环境清单（接手要准备什么）

| # | 项 | 说明 | 谁提供 |
|---|----|------|--------|
| 1 | SceneSQL 仓库 | `git clone`（仓库权限找团队负责人开），内含开发指南、3 个 skill、fact_store 交接包；本手册在 `docs/gac/`（projects 根目录下） | 已有 |
| 2 | 你的 DSW 机器 | 部署 SceneSQL（端口 30001）；仓库路径随意，原环境是 `/root/data/text2sql` | 你自己 |
| 3 | SQLite DB 批次数据 | OSS 挂载 `/mnt/gacrnd-oss/gac_liulian/common_data/sqlite_dbs` 等（具体挂载方式问团队） | 团队 |
| 4 | **链路 B 独立脚本包** | `SceneSQL/scripts/spark_toolkit/`（随仓库一起拿到，§3）；只需你的 DSW 能访问公司 OSS/EMR/StarRocks 内网 + 装好几个 pip 包（`README` 里有清单）。**不需要部署任何服务**。DataMining 服务（§4.1）降级为可选 | 仓库自带 |
| 5 | SceneSQL `.env` | `AUTH_USERNAME`/`AUTH_PASSWORD`（登录凭证）、`JWT_SECRET`、`DATAMINING_BASE_URL` | 自己配 |
| 6 | data_mining 代码库 | 本地路径自定，用于 schema 同步（§1.2 的 `DATA_MINING_PROJECT_PATH`） | 团队 |
| 7 | Qoder | 公司有权限。把本手册 + `SceneSQL/AGENTS.md` 指给 Qoder 即可开工；仓库里 `.agents/skills/` 的 3 个 skill（llm-sql-writing / ubm-schema-sync / development-workflow）会随仓库一起拿到 | 已有 |
| 8 | **fact_store 导入（强烈建议）** | `bash docs/knowledge_base/setup.sh` —— 一条命令完成：① 把前任积累的 64 条标签开发记忆灌进你机器的记忆库；② 注入全局指令 `~/.qoder-cn/AGENTS.md`；③ 安装全局 skill。**机器级生效**：装完你机器上的 Qoder 在任何项目里都知道怎么查这些知识。详见 `docs/knowledge_base/README.md` | 仓库自带 |

### 0.3 仓库内权威文件

| 内容 | 文件 |
|------|------|
| **本交接手册**（唯一总入口） | `docs/gac/LLM标签开发交接手册.md`（即本文件） |
| 标签 SQL 开发深度知识库（18 条 schema 实证坑完整版、误报分类学、验证协议、对照组清单） | `SceneSQL/docs/scene_tag_sql_dev_guide.md` |
| SQL 写作 skill（8 表结构、100+ 标签、7 个 SQL 模板） | `SceneSQL/.agents/skills/llm-sql-writing/SKILL.md` |
| Schema 同步 skill | `SceneSQL/.agents/skills/ubm-schema-sync/SKILL.md` |
| 开发流程规范（部署/测试/CHANGELOG 纪律） | `SceneSQL/.agents/skills/development-workflow/SKILL.md` |
| 项目开发指令（Qoder 自动读取） | `SceneSQL/AGENTS.md` |
| **fact_store 交接包（64 条记忆 + 一键安装脚本）** | `SceneSQL/docs/knowledge_base/`（README + setup.sh + import_fact_store.py + facts/） |
| 参考实现：无保护左转标签 | 底稿 `SceneSQL/unprotected_left_turn.sql`（v10.4）+ recipe `agent/backend/app/core/recipes/unprotected_left_turn.yaml` + 验证集 `docs/gac/sql_validation/unprotected_left_turn_visualation_val_v10.4/` |
| **链路 B 独立脚本包（Spark 批量打标→转数据集）** | `SceneSQL/scripts/spark_toolkit/`（5 个脚本 + README，§3；拿到即可跑，无需服务） |
| Spark 批量检索服务版说明书（可选，§3.5） | DataMining 仓库 `openspec/specs/spark-search/api-guide.md`，或运行时 `GET /datamining/api/spark-search/doc/api-guide` |

> 注：原作者机器上的 Hermes fact_store / Qoder memories / CodeBuddy skill 里的标签开发知识，
> 已做两层落地：① 关键事实**内联到本手册**（不导入记忆库也能用）；
> ② 原始记忆精选 64 条导出为 `docs/knowledge_base/`，跑一次 `bash setup.sh`
> 即嵌入你机器的 fact_store，你的 Qoder 可按需深挖长尾细节（历史案例、bag_id 级复现）。

### 0.4 fact_store 嵌入：机器级知识交接（背景 + 做法）

**背景**：前任两年多踩坑积累的经验（18 条 schema 实证坑、Loop 规程、链路细节）原本存在
其个人机器的记忆库（Hermes fact_store，一个 sqlite 文件）里，人走了知识不能丢，所以随仓库
导出成 `docs/knowledge_base/`。但**记忆库本身是机器级的、不分项目**——光把数据灌进去没用，
Qoder 还需要「指路牌」才知道这个库的存在和查法。指路牌有两个层级：

| 层级 | 载体 | 生效范围 |
|------|------|----------|
| 项目级 | `SceneSQL/AGENTS.md` 末节「标签开发交接知识库」 | 只在 SceneSQL 项目目录内 |
| **机器级** | `~/.qoder-cn/AGENTS.md`（全局指令，每次会话自动注入）+ `~/.agents/skills/fact-store-query/`（全局 skill，所有项目可见） | **你机器上任何项目** |

**一键安装（推荐）**：

```bash
cd <SceneSQL仓库>/docs/knowledge_base
bash setup.sh          # 幂等，重复跑无副作用；--db <路径> 可指定目标库
```

`setup.sh` 做三件事：

1. **导入记忆**：`import_fact_store.py` 把 `facts/` 下 64 条 markdown 写入
   `/root/.hermes/memory_store.db`（表 `facts(content, category, tags)`，content UNIQUE 去重）。
   目标库/表不存在会自动创建（含 FTS5 索引与维护触发器），**没装 Hermes 也能用**；
2. **注入全局指令**：往 `~/.qoder-cn/AGENTS.md` 追加一段带标记（`<!-- fact-store-handover -->`）
   的说明——库路径、用途、查询模板。这是 Qoder 的用户级全局指令，每次会话自动加载；
3. **安装全局 skill**：写 `~/.agents/skills/fact-store-query/SKILL.md`，描述何时触发
   （标签/打标/策略/评测集/Spark/DSW 话题）和查询规则。装完新会话的技能列表里可见。

**手动分步（不想跑脚本时的等价做法）**：

```bash
# ① 只导入记忆
python3 docs/knowledge_base/import_fact_store.py            # --dry-run 先预览

# ② 手动在 ~/.qoder-cn/AGENTS.md 追加：库路径 + 「先查库再动手」+ 查询模板（见 setup.sh 内容）

# ③ 手动把查询说明放到 ~/.agents/skills/fact-store-query/SKILL.md
```

**验证**：

```bash
# 库已就位
python3 -c "
import sqlite3
c = sqlite3.connect('file:/root/.hermes/memory_store.db?mode=ro', uri=True).cursor()
print(list(c.execute('SELECT COUNT(*) FROM facts'))[0][0], '条 facts')
for r in c.execute(\"SELECT content FROM facts WHERE content LIKE '%标签%'\"):
    print(r[0][:80], '\n---')
"
# Qoder 已感知：新开任意项目的会话，技能列表里应有 fact-store-query
```

**交接包目录结构**（`SceneSQL/docs/knowledge_base/`，随仓库分发）：

```
knowledge_base/
├── README.md                # 安装说明（内容与本节相同，维护以本手册为准）
├── setup.sh                 # 一键安装（导入 + 全局指令 + 全局 skill）
├── import_fact_store.py     # 导入脚本（自包含，可单独拷走）
└── facts/                   # 64 条记忆，按 category 分目录的 markdown
    ├── project/             # 项目事实（标签架构、实证坑、Loop 规程、策略/评测集…）
    ├── infra/               # 环境（DSW 部署、隧道、DataMining 连通性、转数据集实测）
    ├── general/             # 通用（架构方向、Memory 纪律…）
    ├── tool/                # 工具（API 诊断、凭证位置…）
    └── user_pref/           # 工作偏好（前端测试纪律、大样本验证、复用模式写SQL…）
```

**后续维护**（你自己沉淀的新经验）：往 `facts/<category>/` 加 markdown 再跑一遍脚本即可
（幂等增量）；或直接向库里 `INSERT`。

**查询约定**：
- **中文关键词必须用 `LIKE`**（FTS5 unicode61 把连续中文当单个长 token，短词 MATCH 不命中）；
  英文/标签词可用 `facts_fts MATCH 'word1 OR word2'`；
- 只读连接（`mode=ro`），禁止直接写库（写入走导入脚本或 INSERT）；
- 模板：

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('file:/root/.hermes/memory_store.db?mode=ro', uri=True).cursor()
for r in c.execute(\"SELECT content, category FROM facts WHERE content LIKE '%关键词%'\"):
    print(r[0], '|', r[1], '\n---')
"
```

**与本手册的分工**：手册保主线流程（不导入记忆库也能用）；fact_store 保长尾细节
（历史案例、bag_id 级复现）。两者有重叠是正常的。

### 0.5 导入事实的时效声明（先读）

`facts/` 下 64 条记忆是从前任的**活记忆**导出的原环境快照，语义分流要点：

- **环境类陈述**（服务地址、ssh 别名、绝对路径、"本机"）→ 一律指**前任环境**（xct 本机 + 大写 DSW 8.130.209.216）。
  接手人环境中："本机"=你自己的 DSW；服务地址=你部署后的 `http://localhost:30001`；
  前任的 xct 机器和跳板机你**均无访问权限**，相关地址仅作历史对照（附录 A）。
- **方法论类陈述**（18 条 schema 实证坑、误报分类学、Loop 规程、交付纪律、验证协议）→ **与机器无关，直接照用**，这是交接包的主体价值。
- 命中环境类事实时，操作前先做一次映射：把地址/路径替换为你自己的部署值；拿不准就看本手册对应章节（手册正文已全部按接手人视角书写）。

---

## 1. Schema：在哪里看，如何更新

### 1.1 在哪里看

所有 schema 文件位于 `SceneSQL/agent/backend/app/core/`：

| 文件 | 角色 |
|------|------|
| `schema_master_raw.yaml` | **母表 = 唯一权威源**。含 `tables.{表}.enum_columns.{列}.values`（per-table per-column 枚举）、`source_map`（枚举来源标注）、`tag_semantics`（标签语义）、`git_version` |
| `schema_structure.yaml` | 派生产物，给 LLM：8 表结构 + 枚举 |
| `schema_dictionary.yaml` | 派生产物，给 LLM：100+ 标签语义字典 |
| `schema_master.yaml` | 汇总视图 |

**人只维护母表**；`schema_structure.yaml` / `schema_dictionary.yaml` 由
`derive_schemas.py` 自动派生，**禁止手动编辑**。

补充参考：`SceneSQL/docs/SCHEMA_REFERENCE.md` / `SCHEMA_REFERENCE_V2.md`
（注意：文档里说 `dynamic_obj.x/y` 是 UTM —— **这是错的**，实为 ego 相对系，见 §5.2 坑表）。

覆盖情况（2026-08 快照）：`range_tag.tag_name` 192 个枚举值，SQLite 94 标签 100% 覆盖。

**标签架构关键事实**（决定 schema 里该有什么）：
- SQLite `range_tag` 表只有基础标签（由上游 `to_sqlite_db.py` 写入），是 SceneSQL/Spark 打标能查到的标签；
- 上游 `db_py_rule` 目录下的规则（如 `all_tags_convert.py`）产出的是 **cloud_tags**（云端标签 JSON），只存 cloud_tags、不写 SQLite；
- 因此 schema 字典**只应包含 SQLite range_tag 中真实存在的标签**；
  若 sync 脚本从 db_py_rule 扫出标签，属于噪音，不要进母表。

### 1.2 如何更新（Schema v2.0 自动同步流程）

**一条命令**（在你的 DSW 或本地，SceneSQL 仓库根目录）：

```bash
cd <SceneSQL仓库根>
DATA_MINING_PROJECT_PATH=<你本地的data_mining仓库路径> \
AUTO_UPDATE_SCHEMA=1 \
python3 .agents/skills/ubm-schema-sync/scripts/sync_schema.py
```

脚本自动完成：
1. 读 data_mining 仓库当前 git hash，与母表 `git_version.data_mining_repo` 比对；
   变了就生成 `/tmp/schema_sync_report.md`（相关 commit）；
2. **5 策略从源码提取 label_id**（无论 git hash 是否变都跑）：

   | 策略 | 来源 | 提取内容 |
   |------|------|----------|
   | 1 | `activity_new/op_*.py` | `label_id = "TAG_NAME"` 显式声明 |
   | 2 | `activity_new/op_*.py` + `user_workspace/` | `CASE WHEN ... THEN "TAG_NAME"` SQL 重命名 |
   | 3 | `tag_map.py` refDictEn | 车端行为标签（64 个） |
   | 4 | 重构后的算子函数 | 函数签名中的标签名 |
   | 5 | `_build_event_info(..., "TAG_NAME")` | 事件构造调用 |

3. 新标签自动追加进母表 + 更新 source_map + 在 `tag_semantics` 加 `TODO: 补充语义描述` 占位；
4. 自动派生 `schema_structure.yaml` + `schema_dictionary.yaml`；
5. 自动同步 ETL `CORE_TABLES`（`etl_sqlite_to_parquet.py`）。

**同步后必做**：人工/LLM 补全新标签的 `tag_semantics` TODO。

**排除规则**（不是 range_tag 标签，勿进母表）：
- `_RENAMED_RAW_IDS`：如 `steering_15_60`（被 CASE 重命名为 `steering_left_15_60`）
- `_INVALID_VEH_TAGS`：源码拼写错误（`CRUISE YIELOTOPEDESTRIANS`、`NTERSECTION_UTURN`）
- `_OBJ_TYPE_VALUES`：car/bus/pedestrian 等 —— 那是 `dynamic_obj.type`，不是 tag_name

**验证手段（互补，非主流程）**：扫描实际 SQLite DB 找 gap：

```python
import sqlite3, glob, yaml
db_dir = "<你的sqlite_dbs批次目录>"
actual = set()
for db in glob.glob(f"{db_dir}/*.db"):
    conn = sqlite3.connect(db)
    actual |= {r[0] for r in conn.execute("SELECT DISTINCT tag_name FROM range_tag")}
    conn.close()
schema_tags = set(yaml.safe_load(open(
    'agent/backend/app/core/schema_master_raw.yaml')
    )['tables']['range_tag']['enum_columns']['tag_name']['values'])
print("SQLite有但Schema缺失:", sorted(actual - schema_tags) or "NONE ✓")
```

注意 SQLite DB 可能滞后（未按最新标签重生成），只看大局不看细节；
若 DB 有提取管道漏掉的标签 → 回溯代码中注入位置 → 修提取管道。

---

## 2. 链路 A：开发 SQL → 可视化 → 策略列表 → 评测集（SceneSQL 服务）

### 2.1 认证与基础信息

| 项 | 值 |
|----|----|
| 服务地址 | `<SCENESQL_URL>`（原环境 `http://8.130.209.216:30001`） |
| 登录 | `POST /api/auth/login`，body `{"username":"<AUTH_USERNAME>","password":"<AUTH_PASSWORD>"}` → `{"access_token": ...}`；后续所有请求带 `Authorization: Bearer <token>` |
| 凭证来源 | 部署根目录 `.env`（`AUTH_USERNAME`/`AUTH_PASSWORD`/`JWT_SECRET`，自己定） |
| 主力批次 | `20260702_T68_2471_c5afa57_100w`（sqlite 模式，15460 个 DB）——你机器上可用的批次以实际挂载为准 |

### 2.2 开发 SQL 并执行

```
POST /api/agent/execute-sql
{ "sql": "...", "query_mode": "sqlite", "batch_id": "<批次名>", "result_limit": N }
→ [{ bag_id, start_ts(秒), end_ts(秒) }, ...]
```

硬性要求：
- **SQL 必须返回 `start_ts`/`end_ts`（秒）**——前端靠这两列定位视频片段；
- **不能 `SELECT bag_id`**——range_tag 表无 bag_id 列，API 自动从 db 文件名提取；
- 统计标签数量不要用 `COUNT(*)` 跨 DB 聚合（被 result_limit 截断会严重失真），
  用 `SELECT * FROM range_tag WHERE tag_name='...'` 看响应的 `total_rows`；
- SQL 底稿放 `SceneSQL/<tag>.sql`（头注写版本史），定稿后与
  `agent/backend/app/core/recipes/<tag>.yaml` **逐字节一致**（脚本校验）；
- 写 SQL 的语义/坑直接引用 `docs/scene_tag_sql_dev_guide.md`（摘要见 §5.2）。

### 2.3 可视化打标

前端页面（AgentPanel）或 API：

| 用途 | 端点 |
|------|------|
| 抽帧（批量） | `POST /api/video/extract-batch` → `GET /api/video/extract-batch/{task_id}` → `GET /api/video/frames/{task_id}/{clip_idx}/{filename}` |
| 导出 mp4 | `POST /api/video/extract`（bag_id + ts → mp4）→ `GET /api/video/status/{task_id}` / `GET /api/video/file/{task_id}` |
| 播放 | 前端「可视化」按钮（HEVC 流式：`/api/video/stream-hevc` 等） |
| VLM 辅助评测（可选） | `POST /api/mage-vl/evaluate` `{bag_id, start_ts, end_ts, topic, prompt, max_tokens}`；健康检查 `GET /api/mage-vl/health`（需要 DSW 上另起 Mage-VL 服务，可选配） |

注意：
- **时间戳单位**：range_tag 的 start_ts/end_ts 是**秒**（10 位）；送抽帧/评测类 API 要 **×10⁹ 转纳秒**（19 位）；
- **默认评测 topic = 前视宽 120°**：新 bag `/gac/cam/orig_fw120_encoded`（10Hz），
  老 bag `/gac/cam/fw120_encoded`（28Hz）。**带 120 字样的才是前摄**；
  完整相机 topic 映射见 `scene_tag_sql_dev_guide.md`（Pattern A/B 两种命名）；
- **抽帧 API 缺 topic 会静默 fallback 到任意相机**（侧后视）——必须显式带 topic，
  并用返回消息 `"Extracted N frames from /xxx"` 逐 clip 核验；
- **VLM 判正例不可信**（原环境实测抽样精度≈0）：正例必须帧中可见证据才保留；负例可宽泛采信。

### 2.4 把打标结果 + SQL 链到策略列表

**策略列表 = `user_strategies/` 目录的 YAML**（与系统 recipe 同格式），
路径 `SceneSQL/agent/backend/app/core/user_strategies/`
（现有：分流 / 合流 / 直行路口 / 隧道 / 隧道入口(_v2) / 隧道出口 / Y型路口）。

通过 API（`/api/strategies`）管理，**不要手搓 YAML 文件**：

| 操作 | 端点 |
|------|------|
| 列表 | `GET /api/strategies`（多用户：自己的 + admin 的） |
| 单个 | `GET /api/strategies/{name}` |
| **创建（把 SQL 链入策略列表）** | `POST /api/strategies`，body：`{"name", "keywords": [...], "tag_name", "sql", "description"}`。keywords 进 ConceptRouter 匹配表（优先于系统 recipe），创建后自动 reload |
| 更新/发版覆盖 | `PUT /api/strategies/{name}`，可更新 `keywords/tag_name/sql/description/status`；`status: active\|disabled`，disabled 不进路由与向量索引；仅 owner 或 admin 可改 |
| **同步到产线 DataMining（可选）** | `POST /api/strategies/{name}/sync-dm` → 写产线 `sql_strategy` 表（`/api/text2sql/strategy/save` 或 `/update/{id}`），并推送该策略的标注 case 为策略评测记录（`/api/text2sql/strategy/review`）。需要 DataMining 服务在线（§4.1）；不部署服务可跳过，链路 B 主通道不依赖它 |
| 删除 | `DELETE /api/strategies/{name}`（同时清空标注） |

前端入口：AgentPanel 策略列表；每条策略有「验证集」按钮（青色），
弹出标注列表（Bag ID / 时间范围 / ✅通过 ❌不通过 / 📹可视化按钮）。

### 2.5 把打标结果链到评测集

标注按 `(strategy, bag_id, start_ts, end_ts)` 去重 upsert，存 JSONL：

| 操作 | 端点 | 说明 |
|------|------|------|
| 添加标注 | `POST /api/eval-labels` `{strategy_name, bag_id, start_ts, end_ts, verdict}` | `verdict` ∈ `"pass"\|"fail"`；**时间戳为秒**；pass→正例，fail→负例 |
| 查询标注 | `GET /api/eval-labels/{strategy_name}` | 返回该策略全部标注 |
| 删除标注 | `DELETE /api/eval-labels/{strategy_name}?bag_id=..&start_ts=..&end_ts=..` | |
| **同步到产线评测集** | `POST /api/eval-labels/{strategy_name}/sync-evalset` `{benchmark_name}` | 幂等。转产线格式：`bin_id=bag_id`，`tag_name={strategy}_positive/{strategy}_negative`，秒→×10⁹ 纳秒，`version:"v1"`；不传 mining_table（产线按 em_bin 反查 UBM 推断 collection 表） |

产线端点（`.env` 的 `DATAMINING_BASE_URL` 下）：`POST /evalset/benchmark/upload`（幂等去重）。
约定：每集合 ~100 条正 + 负。

### 2.6 策略 + 评测集工作流（迭代模型）

- Loop 以**策略**形式维护，一个场景一个或多个策略，每个策略有正式发版版本；
- 发版节奏：结果验证不错了再发版；可视化后发现太差 → 修正后覆盖或删除；
- 验证过的场景收回到策略的评测集；发版后告知使用方交付版本号；
- 评测集可被调取做二次打标 → 下一轮 loop 的评测集作为金标准。

---

## 3. 链路 B：Spark 批量打标 → 结果捞取 → 转数据集（独立脚本，无需服务）

> 百万级 DB 的产线批量打标通道。**完全由脚本完成，不依赖任何常驻服务**：
> 脚本包 = `SceneSQL/scripts/spark_toolkit/`（本仓库自带，拷到你 DSW 任意目录即可）。
>
> ```
> spark_toolkit/
> ├── run_debug.sh                    # 环境变量封装（LD_LIBRARY_PATH 等），用它跑 python
> ├── task_submit_sqlitedb_query.py   # ① 提交：直调 EMR Serverless Spark OpenAPI
> ├── spark_sqlitedb_query_job.py     # ② Spark 作业本体（被上传到 OSS 在 EMR 上执行）
> ├── query_result_to_dataset.py      # ③ 捞结果 + 倒查车型 + 写数据集
> ├── run_to_dataset.sh               # ③ 的一键封装（preview/test/full）
> └── vendor/                         # gsbag/dm_sdk 内部包的 venv 原样副本 + 一键安装脚本
> ```

### 3.0 前置

- 你的 DSW 能访问阿里云内网（OSS / EMR OpenAPI / StarRocks 内网地址）；
- 依赖（提交机侧）：`pip install oss2 pymysql tqdm requests`、
  `alibabacloud_emr_serverless_spark20230808`、`alibabacloud_tea_openapi`、
  `alibabacloud_tea_util`；
- **内部包 `dm_sdk` / `gsbag`（原安装包已丢失）→ 用 `vendor/` 副本直接装**：
  这两个内部包的安装包已删，实测可行的替代做法是把原虚拟环境
  （text2sql/.venv，Python 3.10）site-packages 里的文件直接复制——
  `vendor/` 就是那份原样导出，一条命令装好并验证导入：

  ```bash
  cd SceneSQL/scripts/spark_toolkit/vendor
  bash install_vendor.sh            # 或指定解释器: bash install_vendor.sh /path/to/.venv/bin/python
  # dm_sdk 的 pip 依赖: pip install kafka-python cachetools alibabacloud-oss-v2
  ```

  注意：
  - gsbag 的原生封装（`.so`）来自 Python 3.10 环境，**建议目标环境用 3.10**
    （否则导入可能失败；dm_sdk 不受影响，>=3.8 即可）；
  - gsbag 运行所需的一整套原生库（libgacbag/libgsbag/libsqlcipher/libfmt 等）
    已随 `vendor/` 打包（共 ~30MB），安装时一并进 site-packages；导入 gsbag 需
    site-packages 在 `LD_LIBRARY_PATH` 里 —— `run_debug.sh` 已自动处理；
  - 已在干净 Python 3.10 venv 端到端验证（安装后三个模块导入全通过）。
- 提交与转数据集命令一律通过环境封装跑：`bash run_debug.sh <python脚本> ...`。
  `run_debug.sh` 里残留的 `/mnt/data/...` 等路径是原机器的，你机器上没有可删，不影响。

### 3.1 提交批量打标作业

作业本体 `spark_sqlitedb_query_job.py` 是**自包含**的：读 OSS 上的 SQLite DB →
逐 DB 内存建库执行规则 → 结果写湖仓表。**换批次/换规则 = 改作业里的常量再提交**：

| 常量 | 含义 | 原环境值 |
|------|------|----------|
| `DB_BUCKET_NAME` / DB 前缀 | 要批量打标的 SQLite DB 目录 | `gacrnd-infra-datamining` / `sqlite_dbs/ubm_production_260709` |
| `SQL_BUCKET_NAME` / SQL 前缀 | 规则目录：作业启动时拉取该前缀下**全部** `.sql` / `.py` 文件逐条执行 | `gacrnd-oss` / `gac_huangzijian/sql_production/dataworks_0803` |
| `TARGET_TABLE` | 结果落表 | `gac_dlf.default.sqlite_query_result_table` |

规则文件要求：
- `.sql`：整个文件就是一条 SQL；`.py`：从 `sql = """..."""` 块里提取（`parse_py_sql`）；
- **每条 SQL 必须输出 `start_ts`、`end_ts`、`tag_name` 三列**（`tag_name` 推荐写字面量
  `'标签名' AS tag_name`）；
- 把链路 A 验证好的策略 SQL 存成 `.sql` 上传到上面的 SQL 前缀目录（ossutil/oss2 都行），
  下次提交即随批执行。

提交命令：

```bash
cd <spark_toolkit目录>
# 会先把 spark_sqlitedb_query_job.py 上传到 OSS（作业上传路径在
# task_submit 的 JOB_SCRIPT_OSS_KEY 常量里，建议改成你自己名下的目录），
# 再调 EMR OpenAPI 起 Spark 作业
bash run_debug.sh task_submit_sqlitedb_query.py --job-name my_tag_$(date +%m%d)
# → 打印 jobRunId；结束时作业日志末尾会打印 sql_id（UUID）——转数据集用它

bash run_debug.sh task_submit_sqlitedb_query.py --wait   # 提交并阻塞等结果
bash run_debug.sh task_submit_sqlitedb_query.py --no-upload  # 作业脚本没改时跳过上传
```

提交脚本里需要知道的配置（均已内置原环境可用值）：
- `ACCESS_KEY_ID/ACCESS_KEY_SECRET`：团队共用的 EMR/OSS 提交凭证（问团队是否要换成你的）；
- `WORKSPACE_ID=w-3fa048e86117d91f`、`RESOURCE_QUEUE_ID=dev_queue`、
  region `cn-wulanchabu`、`esr-4.8.0 (Spark 3.5.2)`；
- 资源：driver/executor 32 cores + 64g，executor 6 instances（在 `SPARK_SUBMIT_CONF` 里改）。

### 3.2 结果表与 sql_id

结果统一落 **DLF 湖仓表 `gac_dlf.default.sqlite_query_result_table`**
（StarRocks catalog，可直查）。一次作业运行对应一个 **`sql_id`**（作业末尾打印的 UUID），
后续捞结果、转数据集都按 `sql_id` 过滤。

注意：**独立脚本用的表名没有 `_v2` 后缀**；服务版链路（§3.5）写的是
`sqlite_query_result_table_v2`，两条通道结果不互通，查询时别混。

### 3.3 捞结果 → 转数据集（run_to_dataset.sh）

`query_result_to_dataset.py` 做的事：按 `sql_id`（+可选 `--tag_name` 过滤）从
StarRocks 查结果 → 时间单位自适应（秒/毫秒/纳秒）→ 用 `dm_sdk` 拿 `bag_id`
（=回灌后的 em bin id）反查 `ubm_vehicle_module_bin` 得到真实车型/来源表 →
按车型分组写入湖仓集合表 + 数据平台集合（`tag_source="sqlite_search_mining"`，
支持去重与断点续写）。

**换一批数据只改 `run_to_dataset.sh` 顶部【任务配置】**：`SQL_ID`、`TAG_NAMES`（可多个）、
`TASK_ID`（数据集名前缀，如 `my_mining_20260901`）。

```bash
./run_to_dataset.sh preview        # 只预览：条数 / bag 数 / 车型分布 / 样例（安全，随便跑）
./run_to_dataset.sh test           # 小批量 20 条 + 真实写入（全量前必做的验证）
./run_to_dataset.sh full           # 全量写入（前台，SSH 断连会中断）
./run_to_dataset.sh full bg        # 全量后台跑，日志 /root/data/logs/db_to_dataset/
```

关键坑（实测结论）：
- **写入不可回滚**：湖仓集合表不支持 DELETE —— `test`/`full` 前必须先 `preview`
  确认 bag 数、标签分布、车型分布；
- **车型倒查依赖 `dm_sdk` + 有效 token**（脚本内已内置 `DEFAULT_TOKEN`，过期问团队要）；
  测试/合成 bag 在 UBM 里查不到来源表 → 不会写入，属预期行为；
- 同 bag 同时间窗的多个标签会被聚合进同一条记录；
- 集合命名规则：`data_mining_collection_<车型>_...`（按来源表自动映射），
  已存在的同名集合走扩展（幂等去重，可安全重跑续写）。

### 3.4 链路 B 端到端示例（一条命令流）

```
① 链路 A 迭代好某标签的 SQL（验证过、带 '标签名' AS tag_name 输出）
② 存成 xxx.sql 上传到 OSS 规则前缀（gacrnd-oss/gac_huangzijian/sql_production/... 或你自己的目录，
   同步改作业里的 SQL 前缀）
③ bash run_debug.sh task_submit_sqlitedb_query.py --wait
   → 作业完成，日志里拿到 sql_id（UUID）
④ 改 run_to_dataset.sh 的 SQL_ID / TAG_NAMES / TASK_ID
⑤ ./run_to_dataset.sh preview          # 核对条数与车型分布
⑥ ./run_to_dataset.sh test             # 20 条试写，数据平台抽查
⑦ ./run_to_dataset.sh full bg          # 全量写入，完成后数据平台可见
```

### 3.5 可选替代：DataMining 服务版 spark-search API

> 产线上另有一套把同样流程包成 HTTP API 的服务（DataMining，端口 8089，
> `POST /datamining/api/spark-search/{submit,status,results,tag-query/*,convert/*}`），
> 支持「按策略名批量打标」「去重拦截」「运行记录」等便利。**它不是必需的**——
> 功能与本节脚本完全等价，且写的是另一张表（`sqlite_query_result_table_v2`）。
> 只有在团队要求走产线服务、或想用策略名直连时才需要部署（见 §4.1）。
> 完整说明书：`GET /datamining/api/spark-search/doc/api-guide` 或
> DataMining 仓库 `openspec/specs/spark-search/api-guide.md`。

---

## 4. 部署备忘

### 4.1 DataMining 服务（可选，仅 §3.5 服务版链路需要）

> 链路 B 的主通道是 §3 的独立脚本，**不部署此服务也能完整跑通**。
> 以下仅在你要用服务版 spark-search API 时才需要。

- 仓库：Gerrit 上的 DataMining（Java，Maven 多模块），部署目录原环境为 `/root/data/DataMining`；
- 启动命令（原环境实测，按你的环境改路径）：

```bash
java -jar data-mining-starter/target/data-mining.jar \
  --spring.profiles.active=prod --scene-sql.enabled=true \
  --oss.sync.enabled=false \
  --oss.sync.local-dir=<你的sqlite_dbs挂载目录>/ \
  --oss.nas.sync.nas-dir=<你的sqlite_dbs挂载目录>/ \
  --text2sql.schema.local-dir=/tmp/dm_schema \
  ...（其余参数见 DataMining 仓库 README / 问团队）
```

- **外部依赖连通性**（重要，新机器大概率要处理）：
  服务依赖多个云上实例（MySQL data_manage、PG strategy_db、MongoDB data_pipeline、
  StarRocks、Kafka、OSS 等）。其中**部分实例与 DSW 不在同一 VPC**，
  原环境靠「公司办公电脑 autossh 反向隧道」打通（MongoDB 3717、strategy_db PG 15432 等）。
  你的机器连通性需逐个实测；隧道注意点：
  - MongoDB 单点连接必须 `directConnection=true`；
  - strategy_db 必须指向含 `sql_strategy` 和 `spark_run_record` 表的 PG 实例
    （原环境：`jdbc:postgresql://127.0.0.1:15432/ods?currentSchema=public`）——
    曾有另一个实例（可直连但没有这两张表）被混淆过的事故；
  - 隧道只能由办公网侧发起（DSW 无法主动连跳板机）。
- 服务端口 8089，context-path `/datamining`。

### 4.2 SceneSQL 服务（链路 A）

- 部署：本机改代码 → `git push` → 你的 DSW `git pull --ff-only && bash visualizer/deploy.sh -f`
  （**禁止远程直接编辑**；流程详见 `.agents/skills/development-workflow/SKILL.md`）；
- 端口 30001；凭证走 `.env`；
- 手工重启必须复刻 `deploy.sh` 的环境变量（否则行为不一致）。

---

## 5. 纪律与高频坑

### 5.1 交付纪律（用户/团队明确规定，2026-08-28 定稿）

1. **不要把自定义标签写入各 bag SQLite 的 `range_tag` 表**；
2. **不要手搓 `user_strategies` 策略 YAML（走 API/前端）**；
   原因：产线生产环境的 SQL DB 没有本地注入的标签，注入后产线搜不到任何结果；
3. 正确交付流程：高召回 SQL → execute-sql → 可视化打标（人工复核为主）→
   `/api/eval-labels` 导入标注 → `sync-evalset` 进产线评测集；
   大规模产线打标走链路 B（Spark）；
4. 发版前必须大样本验证：对照组回归（防过拟合）+ **新 seed 随机抽样 ≥10 条**，
   逐样本目标级数据 + 抽帧画面双核对，给出精度统计（真/弱/假）再交付；
   （历史教训：只跑固定对照组导致连续 4 版误报被抽检发现；
   另有「跟大车」曾注入标签 + 注册策略，次日被纠正全部回滚——清除 4,920 行注入标签、删策略 YAML、重启服务。）

### 5.2 Schema 实证坑 18 条（摘要；完整版含案例见 `docs/scene_tag_sql_dev_guide.md` §2）

1. `dynamic_obj.heading` = 自车相对系（h_rel = obj_yaw − ego_yaw，CCW 正，±π=正对向）；绝对朝向 = `heading + utm_yaw`
2. `dynamic_obj.x/y` = ego 相对系（x 前、y 左，米），**不是 UTM**
3. `ego.utm_x/utm_y` 部分 bag 全程冻结，不可用
4. `relative_velocity`/`absolute_velocity` 全部不可用（与位置增量物理矛盾）
5. 目标运动唯一可信源 = `obs_dr_trajectory` 的 speed 数组（JSON，5 采样/行，**单位 km/h**，静止=0）
6. `ego_dr_trajectory` 与 `obs_dr_trajectory` 同一 DR 系，数组 = 当前 + 未来 ~0.4s 外推（10Hz）
7. ego 系距离差不能当「目标驶来」判据——ego 驶向静止目标签名相同
8. 转弯中目标相对朝向扫掠（90°→180°），逐帧窄带过滤只剩 1~2 帧 → 用绝对朝向 + 首/末朝向比较
9. `dynamic_obj.type` 仅 5 种：bus/car/motorcycle/pedestrian/truck
10. `ego.specify_topology_tag` 枚举直查（cross_road/small_cross_road/t_junction/small_t_junction/straight_intersection/multi_fork/other/none）；别解析 intersection_info.lane_info（部分 bag malformed JSON）
11. 信号灯只有颜色（无箭头形状字段）；「绿箭头 ≠ 无冲突」
12. 自车行为锚点 `range_tag Turning(sub_tag='turn_left'/'turn_right')` × Intersection 重叠；标签可能滞后实际行为 15s 或缺失 → 轨迹类判据用全窗口
13. `range_tag.start_ts/end_ts` 是**秒**；抽帧/评测类 API 要**纳秒**（×10⁹）；结果表 `start_time/end_time` 纳秒/毫秒/秒自适应
14. 前摄 topic：新 bag `orig_fw120_encoded`（10Hz）/ 老 bag `fw120_encoded`（28Hz）；带 120 的才是前摄
15. 抽帧 API 缺 topic 静默 fallback 到侧后视 → 必须显式带 topic 并核验返回消息
16. dynamic_obj 采样 1Hz：高速目标窗口内常仅 2 帧 → per-obj 帧数闸 ≤2，质量闸放事件级
17. 跟踪器远距/初段朝向不可靠（~180° 翻转）→ 朝向判据以最近帧为锚
18. execute-sql API 偶发路由抽风（裸库有数据 API 返回空）→ 排查先 SSH 直查 sqlite3 对照

### 5.3 误报分类学（机理 → 判据）

| 误报模式 | 判据 |
|----------|------|
| 静止/排队车 | DR speed>5 且 ≥2 帧 |
| 已通过远离车 | 最近帧非首帧（逼近方向） |
| 时间错配会车 | 轨迹交叉 \|Δts\|≤5s |
| 自身转弯/横穿车 | ≥90% 帧朝向 ±15° + 首个 dist<20m 帧方位角 <30° |
| 驶入路对向（平行不交叉） | 只用 diff_init，删 diff_final |
| 后方跟随车 | diff_init≈0 结构性排除 |
| 远距弱冲突 | 同时刻最近 <10m |
| 锚定标签滞后 | 轨迹交叉用全窗口 ego 路径 |

### 5.4 标签开发 Loop（每轮必走）

1. **先向需求方要金标准片段**（bag_id + ts 窗口），不凭场景名想象定义；
2. 改底稿 `SceneSQL/<tag>.sql`（头注版本史）；
3. 对照组回归（用户判错的 bag 永远进组；当前 14 个清单在指南 §4.1）；
4. 全量批次（`db_limit=20000, max_workers=32`，原批次 15460 DBs）；
5. 新 seed 随机抽 10 条 → 抽帧（fps 自适应铺满窗口）→ 画面 + 目标级数据双核对 →
   TRUE/LIKELY/UNCERTAIN/FALSE 分类 + 精度统计；
6. 误报归因到机制（朝向稳定性/距离趋势/方位轨迹/速度分布四维），进对照组；
7. 交付：底稿 + recipe yaml 逐字节一致 + 验证集打包 `docs/gac/sql_validation/<tag>_val_<版本>/`；
8. 复核判错 → 回到 1。

心智守则：不信文档（每条结论要 bag_id 可复现）、金标准先行、对照组会过拟合（每版新 seed）、
每个误报归因到机制、DR 系优先于 ego 系、验证相机必须核验、被复核判错的样本是最宝贵资产。

### 5.5 回归与测试纪律

- 改 NL2SQL 链路（concept_router/agent_engine/recipe/prompt/同义词/阈值）后必须跑回归：
  `.venv/bin/python tools/eval_nl2sql_regression.py --report /tmp/regression_$(date +%F).json`；
- 测试必须走前端页面，禁止写脚本绕过前端调 API（E2E 链路联调除外）；
- 策略启停：`status: active|disabled`，路由/索引只收 active。

---

## 附录 A：原环境实际值（参考，勿直接照搬到你的机器）

| 项 | 原环境值 |
|----|----------|
| SceneSQL 服务 | `http://8.130.209.216:30001`（大写 DSW，`ssh DSW` = 8.130.209.216:1025；仓库 `/root/data/text2sql`） |
| 登录凭证 | `gac / gac_data`（.env: AUTH_USERNAME/AUTH_PASSWORD；JWT_SECRET=sceneSQL_visualizer_secret_key_2026） |
| sqlite 批次 | `20260702_T68_2471_c5afa57_100w`（15460 DBs），挂载 `/mnt/gacrnd-oss/gac_liulian/common_data/sqlite_dbs` |
| parquet 数据 | `/mnt/gacrnd-oss/gac_huangzijian/common_data/parquet/20260522_T68_1131_97ba3f_sdpro_1.5w/` |
| DataMining 服务 | `/root/data/DataMining`，`http://127.0.0.1:8089/datamining`（启动脚本 `/root/data/restart_dm.sh`） |
| strategy_db PG | 隧道 `127.0.0.1:15432` → 实例 `pgm-0jle221pauf42i45`，库 ods/public（含 `sql_strategy`、`spark_run_record`）；另一实例 `pgm-0jls2m702d32y179` 可直连但**没有**这两张表，勿混 |
| Mage-VL（可选） | DSW 内 `localhost:31000`（SGLang，PPU） |
| 相机 topic | 前视宽 120°（默认评测）：新 `/gac/cam/orig_fw120_encoded`，老 `/gac/cam/fw120_encoded`；ft30=前视 30°、r50=后视、fl99/fr99=左右前、rl99/rr99=左右后、apa=泊车 |
| Gerrit 脚本 | `gerrit_submit.py` / `gerrit_detail.py`（注意 `git credential fill` 导出的变量是小写 `GERRIT_username`/`GERRIT_password`） |
| 已发版策略示例 | 分流 / 合流 / 直行路口 / 隧道 / 隧道入口(_v2) / 隧道出口 / Y型路口；无保护左转底稿 `unprotected_left_turn.sql` v10.4（55 事件 10 样本 7 TRUE+2 LIKELY+1 UNCERTAIN，0 FALSE） |

> 小写 `dsw`（8.130.175.37:1021）已废弃，任何文档/脚本里看到它都当无效配置处理。
