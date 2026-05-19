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

# UBM Schema Sync

## What this skill does

The data-mining repo (`DATA_MINING_PROJECT_PATH`) evolves continuously:
new operators are added, `add_event()` logic changes, car-end behavior tags expand.
This skill automates the detection of those changes and produces a report so the
schema YAML files (`schema_structure.yaml`, `schema_dictionary.yaml`) can be updated.

**Key rule:** Only `to_sqlite_db.py` writes to SQLite. All other processors
(track converter, db_py_rule queries) only read SQLite or output JSON.

## How it works (no functions — scripts + LLM)

This skill has no callable functions. Instead it provides:
1. `scripts/sync_schema.py` — deterministic script that compares git hashes and generates a Markdown report
2. `references/injection_sources.md` — methodology for manually verifying what enters SQLite
3. This SKILL.md — workflow for the LLM to follow

The LLM (you) runs the script, reads the report, decides what needs updating,
and edits the schema files.

## Prerequisites

- `DATA_MINING_PROJECT_PATH` set in `.env` (default: `/root/data/data_mining/UBM_mining/ubm_data_mining`)
- The mining repo is a git repo on branch `data_mining/master` (may appear as detached HEAD)
- PyYAML installed

## Step-by-step workflow

### Step 1 — Detect changes

Run the sync script. It reads the previous git hash stored in `schema_master_raw.yaml`
and diffs against the current HEAD of `data_mining/master`:

```bash
cd /root/data/text2sql
python .agents/skills/ubm-schema-sync/scripts/sync_schema.py
```

The script:
1. Gets current commit hash + branch (`origin/data_mining/master` if detached HEAD)
2. Reads previous hash from `schema_master_raw.yaml → git_version.data_mining_repo`
3. Runs `git log PREV..HEAD` filtered to SQLite-relevant files:
   - `L2_Pred/downstream/ubm/to_sqlite_db.py` — SQLite writer
   - `L2_Pred/rule_based_mining/semantic_mining/tokenizer_processor_new.py` — operator registry
   - `L2_Pred/rule_based_mining/semantic_mining/activity_new/op_*.py` — operators with add_event
   - `user_workspace/*/*.py` — custom operators
   - `gsbag_parser/tag_map.py` / `em_behavior_tag_parser.py` — car-end tags
   - `mining_pipeline.py` — pipeline orchestration
4. Generates `/tmp/schema_sync_report.md`
5. Prompts to update `git_version` in all three schema files

If the report says "no relevant changes", the schema enum probably does not need updating.
Still answer `y` to record the new git hash.

### Step 2 — Read the report

If the report shows changed files, open `references/injection_sources.md` to understand:
- How to verify whether an `op_*.py` injects into `range_tag`
- How `to_sqlite_db.py` filtering affects tag presence
- How car-end tags flow into SQLite
- The difference between `add_event()` (standard) and `add_table("range_tag")` (direct)

### Step 3 — Update schema files manually

The script **does not auto-update enum values** — this requires human/LLM judgment.

1. **Update `schema_structure.yaml`**
   - Add/remove tag names from `range_tag.enum`
   - Update table/column defs if `to_sqlite_db.py` changed

2. **Update `schema_dictionary.yaml`**
   - Add new tag entries under `tags:`
   - Remove obsolete entries
   - Update `limitations`, `sub_tags`, `source`, `operator`

3. **Update `schema_master_raw.yaml`** (reference doc)
   - Update `actual_tags_from_sample_db` if a new DB was sampled
   - Update `potential_tags_not_in_sample`

### Step 4 — Record the new git hash

Run the script again (or answer `y` at the prompt) to write the new commit hash
into all three schema files' `git_version` block.

## Schema git_version format

Each schema file contains:

```yaml
git_version:
  data_mining_repo: 1df5784fe76e3d67d08edac2da9bcbeae65b42cc
  branch: origin/data_mining/master
  synced_at: "2026-05-19T17:14:52+08:00"
  note: 数据挖掘项目当前 commit hash，schema 以此版本为基准
```

Future syncs diff from this hash, so the report is always incremental.

## Auto-update mode

Set `AUTO_UPDATE_SCHEMA=1` to skip the confirmation prompt:

```bash
export AUTO_UPDATE_SCHEMA=1
python .agents/skills/ubm-schema-sync/scripts/sync_schema.py
```

## When NOT to use this skill

- Do NOT use for SQL query generation — handled by agent engine.
- Do NOT use for rosbag parsing or video extraction.
- Do NOT use when the user only wants to query existing SQLite data.
