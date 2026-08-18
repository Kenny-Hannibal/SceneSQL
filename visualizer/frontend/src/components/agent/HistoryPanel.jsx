import React, { useState } from 'react';
import { colors, radius, btn } from '../../theme';

// ── 历史查询面板（P2，docs/ARCHITECTURE.md 待办项） ──
// 纯前端 localStorage 实现，不依赖后端。
// 记录每次成功查询的 question/sql/上下文，点击可回填到输入框和 SQL 编辑器。

const STORAGE_KEY = 'scenesql_query_history';
const MAX_ENTRIES = 50;

export function loadHistory() {
  try {
    const arr = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

// 记录一条历史（由 AgentPanel 在查询成功后调用）
export function saveHistoryEntry({ question, sql, queryMode, batchId, rowCount }) {
  if (!sql?.trim()) return;
  const entry = {
    ts: Date.now(),
    question: question || '',
    sql: sql.trim(),
    queryMode: queryMode || 'sqlite',
    batchId: batchId || '',
    rowCount: rowCount ?? 0,
  };
  const list = loadHistory();
  // 相同 SQL 去重（移到最新）
  const deduped = list.filter((e) => e.sql !== entry.sql);
  deduped.unshift(entry);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(deduped.slice(0, MAX_ENTRIES)));
}

export function clearHistory() {
  localStorage.removeItem(STORAGE_KEY);
}

function formatTime(ts) {
  const d = new Date(ts);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export default function HistoryPanel({ onRestore }) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState([]);

  const toggle = () => {
    if (!open) setEntries(loadHistory());
    setOpen(!open);
  };

  const handleClear = () => {
    clearHistory();
    setEntries([]);
  };

  const handleRestore = (e) => {
    onRestore(e);
    setOpen(false);
  };

  return (
    <div style={{ marginTop: 8 }}>
      <button onClick={toggle} style={btn.outline(colors.textSecondary, false)}>
        🕘 历史查询 {open ? '▲' : '▼'}
      </button>

      {open && (
        <div style={{
          marginTop: 8, border: `1px solid ${colors.border}`, borderRadius: radius.md,
          background: '#fff', maxHeight: 320, overflow: 'auto',
        }}>
          {entries.length === 0 ? (
            <div style={{ padding: 20, textAlign: 'center', color: colors.textTertiary, fontSize: 13 }}>
              暂无历史记录（成功执行的查询会自动记录在这里）
            </div>
          ) : (
            <>
              <div style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '8px 12px', borderBottom: `1px solid ${colors.borderLight}`,
                position: 'sticky', top: 0, background: '#fff',
              }}>
                <span style={{ fontSize: 12, color: colors.textTertiary }}>{entries.length} 条记录，点击回填</span>
                <button onClick={handleClear} style={btn.outline(colors.error, false)}>清空</button>
              </div>
              {entries.map((e, i) => (
                <div
                  key={e.ts + '-' + i}
                  onClick={() => handleRestore(e)}
                  style={{
                    padding: '10px 12px', borderBottom: `1px solid ${colors.borderLight}`,
                    cursor: 'pointer', transition: 'background 0.1s',
                  }}
                  onMouseEnter={(ev) => { ev.currentTarget.style.background = colors.bgHover; }}
                  onMouseLeave={(ev) => { ev.currentTarget.style.background = 'transparent'; }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 13, color: colors.text, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {e.question || '(直接执行 SQL)'}
                    </span>
                    <span style={{ fontSize: 11, color: colors.textTertiary, flexShrink: 0 }}>
                      {formatTime(e.ts)} · {e.rowCount} 行
                    </span>
                  </div>
                  <div style={{
                    fontSize: 11, color: colors.textTertiary, fontFamily: 'Consolas, Monaco, monospace',
                    marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {e.sql}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
