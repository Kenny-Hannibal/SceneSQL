---
name: ubm-schema-sync
description: >
  Track SQLite schema changes from the UBM data-mining codebase and keep schema docs in sync.
  Use when the user says anything like:
  - "sync/update schema" / "更新schema" / "同步schema"
  - "schema change" / "schema diff" / "schema out of date" / "schema 过期"
  - "data-mining code changed, what tags are new" / "上游代码变了"
  - "check which tags enter SQLite range_tag" / "检查标签" / "标签系统" / "标签变更"
  - "git log for schema changes" / "git diff schema"
  - "operator registry changed" / "算子注册" / "算子有变更"
  - "new tags" / "新增标签" / "少了标签" / "标签不对"
  - "range_tag enum" / "schema_structure.yaml" / "schema_dictionary.yaml" / "schema_master_raw.yaml"
  - "字典更新" / "结构更新"
  Also triggers on: tag injection, SQLite writer changes, range_tag enum updates, car-end behavior tag changes, user_workspace operator changes.
---

# UBM Schema Sync (v2.0)

## What this skill does

The data-mining repo (`DATA_MINING_PROJECT_PATH`) evolves continuously:
new operators are added, `add_event()` logic changes, car-end behavior tags expand.
This skill automates the detection of those changes and produces a report + auto-updates schema files.

## Schema v2.0 Architecture

**母表 = 唯一权威源**，structure 和 dictionary 从母表自动派生：

```
schema_master_raw.yaml (母表)
  ├── tables.{table_name}.enum_columns.{col_name}.values  (per-table per-column枚举)
  ├── tables.{table_name}.enum_columns.{col_name}.source_map  (枚举值来源标注)
  ├── tag_semantics  (标签语义描述，跨表共享)
  ├── git_version
  │
  ├── 派生 → schema_structure.yaml (给LLM: 表结构+enum)
  └── 派生 → schema_dictionary.yaml (给LLM: 标签语义)
```

**人只维护母表**，`derive_schemas.py` 自动生成 structure 和 dictionary。
**`sync_schema.py`** 检测git变动 → 自动提取label_id → 更新母表 → 自动派生。

### Key v2.0 Change from v1.0

v1.0: `range_tag.tag_enum` (flat list)
v2.0: `tables.range_tag.enum_columns.tag_name.values` (per-table per-column)

All sync paths in `sync_schema.py` have been updated for v2.0 structure.

## How it works

Two scripts do the heavy lifting:

1. **`scripts/sync_schema.py`** — compares git hashes, extracts label_ids from source code, auto-updates母表, auto-derives structure/dictionary
2. **`scripts/derive_schemas.py`** — reads母表, generates `schema_structure.yaml` + `schema_dictionary.yaml`

The LLM runs the script, the script does everything automatically.

## Label Extraction Pipeline (5 strategies)

`extract_label_ids_from_operators()` scans source code with 5 strategies:

| Strategy | Source | What it extracts |
|----------|--------|------------------|
| 1 | `activity_new/op_*.py` | `label_id = "TAG_NAME"` explicit declarations |
| 2 | `activity_new/op_*.py` + `user_workspace/` | `CASE WHEN ... THEN "TAG_NAME"` SQL renames |
| 3 | `tag_map.py` refDictEn | Car-end behavior tags (64 tags) |
| 4 | Refactored operator functions | Label names from function signatures |
| 5 | `_build_event_info(..., "TAG_NAME")` | Event construction calls |

### Exclusion rules (NOT range_tag tags)

- `_RENAMED_RAW_IDS`: steering_15_60 etc (renamed to steering_left_15_60 by CASE)
- `_INVALID_VEH_TAGS`: typos (CRUISE YIELOTOPEDESTRIANS, NTERSECTION_UTURN)
- `_OBJ_TYPE_VALUES`: car/bus/pedestrian etc (these are dynamic_obj.type, NOT range_tag.tag_name)

## Step-by-step workflow

### Step 1 — Run sync (automatic)

```bash
cd /data/var/workspace/projects/projects/SceneSQL
DATA_MINING_PROJECT_PATH=/data/var/workspace/projects/projects/data_mining \
AUTO_UPDATE_SCHEMA=1 \
python3 .agents/skills/ubm-schema-sync/scripts/sync_schema.py
```

What the script does:
1. Reads current git hash of data_mining repo
2. Compares with hash stored in母表 `git_version.data_mining_repo`
3. If hash changed: generates `/tmp/schema_sync_report.md` with relevant commits
4. **Always**: extracts all label_ids from source code (5 strategies above)
5. Compares extracted labels vs母表 existing `range_tag.tag_name.values`
6. If new labels found: **auto-appends** to母表 + updates source_map + adds tag_semantics TODO placeholders
7. **Auto-derives** `schema_structure.yaml` + `schema_dictionary.yaml`
8. **Auto-syncs** ETL `CORE_TABLES` in `etl_sqlite_to_parquet.py`

Even if git hash unchanged, the script still checks for new labels (in case previous sync missed some).

### Step 2 — Review and fill TODOs

After sync, new tags get `description: "TODO: 补充 XXX 的语义描述"` in tag_semantics.
These need human/LLM review to fill in proper descriptions.

### Step 3 — Verify with SQLite DB (validation, not primary workflow)

SQL DB scanning is a **complementary validation method**, not the primary sync mechanism:

```python
# Compare actual SQLite tags vs schema
import sqlite3, glob, yaml

db_dir = "/mnt/ubm_code_nas/gac_huangzijian/common_data/sqlite_dbs/20260616_T68_2434_c5afa57_1.5w"
actual_tags = set()
for db in glob.glob(f"{db_dir}/*.db"):
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT DISTINCT tag_name FROM range_tag").fetchall()
    actual_tags.update(r[0] for r in rows)
    conn.close()

with open('agent/backend/app/core/schema_master_raw.yaml') as f:
    schema_tags = set(yaml.safe_load(f)['tables']['range_tag']['enum_columns']['tag_name']['values'])

missing = sorted(actual_tags - schema_tags)  # Should be EMPTY
print(f"SQLite有但Schema缺失: {missing if missing else 'NONE ✓'}")
```

**Important distinction**: 
- **Primary workflow**: git diff on data_mining code → sync_schema.py auto-extracts → updates母表
- **Validation**: SQLite DB scan to find gaps in extraction pipeline
- SQLite DB may be stale (not yet re-generated with latest tags) — only look at big picture, not details
- If DB has a tag that extraction missed → trace where it's injected in code → fix extraction pipeline

## Prerequisites

- `DATA_MINING_PROJECT_PATH` env var pointing to data_mining repo
- The mining repo is a git repo on branch `data_mining/master`
- PyYAML installed

## When NOT to use this skill

- Do NOT use for SQL query generation — handled by agent engine
- Do NOT use for rosbag parsing or video extraction
- Do NOT use when the user only wants to query existing SQLite data
