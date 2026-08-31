#!/usr/bin/env python3
"""import_fact_store.py — 把本目录下的 facts/*.md 导入 fact_store（Hermes 记忆库）。

交接用途：把黄梓建积累的标签开发知识灌进你自己机器的 fact_store，
让你机器上的 Qoder（或任何读 memory_store.db 的 agent）直接开工。

用法：
    python3 import_fact_store.py                 # 默认写入 /root/.hermes/memory_store.db
    python3 import_fact_store.py --db <路径>      # 指定目标库
    python3 import_fact_store.py --dry-run       # 只预览

特性：
- 目标库/表不存在时自动创建（facts + FTS5 + 维护触发器），即没装 Hermes 也能用；
- facts.content UNIQUE，INSERT OR IGNORE 幂等，重复跑无副作用；
- frontmatter 提供 category / tags（与 ai_mem_sync.py 汇聚格式一致）。

查询方式（导入后）：
    python3 -c "
    import sqlite3
    c = sqlite3.connect('file:/root/.hermes/memory_store.db?mode=ro', uri=True).cursor()
    for r in c.execute(\"SELECT content FROM facts WHERE content LIKE '%标签%'\"):
        print(r[0], '\n---')
    "
"""
import argparse
import glob
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^(\w+)\s*:\s*(.*)$", line)
        if kv:
            meta[kv.group(1)] = kv.group(2).strip().strip('"').strip("'")
    return meta, m.group(2)


def ensure_schema(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS facts (
        fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
        content         TEXT NOT NULL UNIQUE,
        category        TEXT DEFAULT 'general',
        tags            TEXT DEFAULT '',
        trust_score     REAL DEFAULT 0.5,
        retrieval_count INTEGER DEFAULT 0,
        helpful_count   INTEGER DEFAULT 0,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    cur.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
        USING fts5(content, tags, content=facts, content_rowid=fact_id)""")
    cur.execute("""CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
        INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
    END""")
    cur.execute("""CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
        INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
    END""")
    cur.execute("""CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
        INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
        INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
    END""")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--db', default='/root/.hermes/memory_store.db',
                   help='目标 fact_store 路径（默认 %(default)s）')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    files = sorted(glob.glob(os.path.join(HERE, 'facts', '**', '*.md'), recursive=True))
    if not files:
        raise SystemExit(f'未找到 facts/*.md（应在 {HERE}/facts/ 下）')

    os.makedirs(os.path.dirname(args.db) or '.', exist_ok=True)
    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    ensure_schema(cur)

    inserted = dup = 0
    for path in files:
        with open(path, encoding='utf-8') as f:
            meta, body = parse_frontmatter(f.read())
        content = body.strip()
        if len(content) < 20:
            continue
        category = meta.get('category', 'general')
        tags = meta.get('tags', 'handover')
        if args.dry_run:
            print(f'[DRY] ({category}) {content[:60]}...')
            inserted += 1
            continue
        cur.execute('INSERT OR IGNORE INTO facts (content, category, tags) VALUES (?,?,?)',
                    (content, category, tags))
        if cur.rowcount:
            inserted += 1
        else:
            dup += 1

    if not args.dry_run:
        conn.commit()
    total = cur.execute('SELECT COUNT(*) FROM facts').fetchone()[0]
    print(f'inserted={inserted} dup_skipped={dup} facts_total={total} db={args.db}')
    conn.close()


if __name__ == '__main__':
    main()
