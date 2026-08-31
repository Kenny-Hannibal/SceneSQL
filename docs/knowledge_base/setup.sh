#!/usr/bin/env bash
#
# setup.sh — 交接一键安装：把标签开发知识嵌入本机，让任意项目下的 Qoder 都能用
#
# 做三件事：
#   1. 导入 64 条 fact_store 记忆 → /root/.hermes/memory_store.db（幂等）
#   2. 注入全局指令 ~/.qoder-cn/AGENTS.md（Qoder 每次会话自动加载，跨所有项目）
#   3. 安装全局 skill ~/.agents/skills/fact-store-query/（所有项目可见）
#
# 用法：
#   bash setup.sh            # 全部安装
#   bash setup.sh --db <路径> # 指定 fact_store 目标库
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
QODER_AGENTS="$HOME/.qoder-cn/AGENTS.md"
SKILL_DIR="$HOME/.agents/skills/fact-store-query"
MARKER="<!-- fact-store-handover -->"

DB_ARG=()
if [[ "${1:-}" == "--db" && -n "${2:-}" ]]; then DB_ARG=(--db "$2"); fi

# ---------- 1. 导入 fact_store ----------
echo "== [1/3] 导入 fact_store =="
python3 "$HERE/import_fact_store.py" "${DB_ARG[@]}"

# ---------- 2. 全局 AGENTS.md ----------
echo "== [2/3] 注入全局指令 $QODER_AGENTS =="
mkdir -p "$(dirname "$QODER_AGENTS")"
if grep -q "$MARKER" "$QODER_AGENTS" 2>/dev/null; then
    echo "已存在，跳过"
else
    cat >> "$QODER_AGENTS" <<'EOF'

<!-- fact-store-handover -->
## 本机 fact_store（机器级长期记忆）

本机有一个机器级事实库 `/root/.hermes/memory_store.db`（sqlite，表 `facts(content, category, tags)`），
内含自动驾驶标签开发的历史知识：18 条 schema 实证坑、SQL 开发 Loop 规程、策略/评测集工作流、
Spark 批量打标与转数据集链路、DSW 部署纪律等（64 条，交接导入）。
遇到 SceneSQL / 标签 / 打标 / 策略 / 评测集 / schema / Spark 打标相关问题，**先查库再动手**。

查询方法（只读连接；中文关键词必须用 LIKE，FTS5 对中文短词不命中）：

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('file:/root/.hermes/memory_store.db?mode=ro', uri=True).cursor()
for r in c.execute(\"SELECT content, category FROM facts WHERE content LIKE '%关键词%'\"):
    print(r[0], '|', r[1], '\n---')
"
```

SceneSQL 项目内的配套文档：
`/data/var/workspace/projects/projects/docs/gac/LLM标签开发交接手册.md`（总交接手册）、
`SceneSQL/docs/scene_tag_sql_dev_guide.md`（深度知识库）。
<!-- /fact-store-handover -->
EOF
    echo "已注入"
fi

# ---------- 3. 全局 skill ----------
echo "== [3/3] 安装全局 skill $SKILL_DIR =="
mkdir -p "$SKILL_DIR"
cat > "$SKILL_DIR/SKILL.md" <<'EOF'
---
name: fact-store-query
description: >
  查询本机 fact_store 长期记忆（/root/.hermes/memory_store.db）。
  当用户或任务涉及 SceneSQL、标签、打标、策略、评测集、schema、Spark 批量打标、
  转数据集、DSW 部署、历史踩坑等话题，且需要历史结论/先例/细节时加载本 skill。
---

# fact_store 查询

事实库：`/root/.hermes/memory_store.db`（sqlite，表 `facts(content, category, tags)`，
含 FTS5 辅助表 `facts_fts`）。64 条标签开发交接记忆，含 bag_id 级可复现案例。

## 查询规则

- **中文关键词必须用 `LIKE`**（FTS5 unicode61 把连续中文当单个长 token，短词 MATCH 不命中）
- 英文/标签词可用 `facts_fts MATCH 'word1 OR word2'`
- 只读连接（`mode=ro`），禁止写入
- 关键词试不出来时换同义词多试几次（如 标签/打标/评测集/策略/抽帧/批次）

## 模板

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('file:/root/.hermes/memory_store.db?mode=ro', uri=True).cursor()
for r in c.execute(\"SELECT content, category FROM facts WHERE content LIKE '%关键词%'\"):
    print(r[0], '|', r[1], '\n---')
"
```

按类浏览：`WHERE category IN ('project','infra','tool','user_pref','general')`

## 配套深度文档（SceneSQL 仓库内）

- `docs/llm_tag_dev_materials.md` — 总交接手册（两条链路全流程）
- `docs/scene_tag_sql_dev_guide.md` — 18 条 schema 实证坑完整版 + 误报分类学
EOF
echo "已安装"

echo
echo "完成。新开任意项目的 Qoder 会话即自动获得 fact_store 指引。"
