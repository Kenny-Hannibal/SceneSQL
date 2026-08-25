import React, { useState } from 'react';
import { colors, radius, btn } from '../../theme';
import { authFetch, API_BASE } from '../../api';

// ── 历史查询面板 ──
// 多用户 v1（2026-08-25）：云端按用户隔离存储（/api/history），localStorage 仅作
// 离线兜底缓存。换设备/浏览器历史不丢。

const STORAGE_KEY = 'scenesql_query_history';  // 离线兜底缓存
const MAX_ENTRIES = 100;

function loadHistoryLocal() {
  try {
    const arr = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]');
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

// 从云端加载历史；失败回退 localStorage
export async function loadHistory() {
  try {
    const res = await authFetch(`${API_BASE}/api/history`);
    if (res.ok) {
      const data = await res.json();
      return data.entries || [];
    }
  } catch { /* 网络失败走本地兜底 */ }
  return loadHistoryLocal();
}

// 记录一条历史（云端为主，本地兜底）
export function saveHistoryEntry({ question, sql, queryMode, batchId, rowCount }) {
  if (!sql?.trim()) return;
  const entry = {
    question: question || '',
    sql: sql.trim(),
    queryMode: queryMode || 'sqlite',
    batchId: batchId || '',
    rowCount: rowCount ?? 0,
  };
  // 云端（fire-and-forget）
  authFetch(`${API_BASE}/api/history`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(entry),
  }).catch(() => {});
  // 本地兜底缓存
  const list = loadHistoryLocal();
  const deduped = list.filter((e) => e.sql !== entry.sql);
  deduped.unshift({ ...entry, ts: Date.now() });
  localStorage.setItem(STORAGE_KEY, JSON.stringify(deduped.slice(0, MAX_ENTRIES)));
}

export async function clearHistory() {
  try {
    await authFetch(`${API_BASE}/api/history`, { method: 'DELETE' });
  } catch { /* 忽略 */ }
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

  const toggle = async () => {
    if (!open) setEntries(await loadHistory());
    setOpen(!open);
  };

  const handleClear = async () => {
    await clearHistory();
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
