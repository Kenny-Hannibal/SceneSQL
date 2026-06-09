import React, { useRef, useCallback, useEffect, useState } from 'react';
import Editor from 'react-simple-code-editor';

/**
 * SqlEditor — 基于 react-simple-code-editor + 外部行号
 * 特性：语法高亮、行号、Tab 缩进、快捷键（Ctrl+Enter 执行）
 */

const SQL_KEYWORDS = new Set([
  'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'NOT', 'IN', 'LIKE', 'BETWEEN',
  'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP', 'JOIN', 'LEFT',
  'RIGHT', 'INNER', 'OUTER', 'ON', 'AS', 'GROUP', 'BY', 'ORDER', 'ASC',
  'DESC', 'HAVING', 'LIMIT', 'OFFSET', 'UNION', 'ALL', 'DISTINCT',
  'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'CASE', 'WHEN', 'THEN', 'ELSE',
  'END', 'IS', 'NULL', 'EXISTS', 'WITH', 'RECURSIVE', 'OVER', 'PARTITION',
  'WINDOW', 'ROWS', 'RANGE', 'FETCH', 'NEXT', 'ONLY', 'VALUES', 'INTO',
  'SET', 'TABLE', 'INDEX', 'VIEW', 'TRIGGER', 'IF', 'BEGIN', 'COMMIT',
  'ROLLBACK', 'TRANSACTION', 'PRAGMA', 'JSON_EXTRACT', 'CAST',
]);

const SQL_FUNCTIONS = new Set([
  'json_extract', 'count', 'sum', 'avg', 'min', 'max', 'round',
  'coalesce', 'ifnull', 'nullif', 'typeof', 'length', 'substr',
  'replace', 'trim', 'upper', 'lower', 'hex', 'quote', 'random',
  'abs', 'total', 'group_concat', 'strftime', 'date', 'time',
]);

function highlightSql(text) {
  if (!text) return [];
  const parts = [];
  const regex = /(--[^\n]*|'[^']*'|"[^"]*"|\b\d+(?:\.\d+)?\b|\w+|[^\w\s]|\s+)/g;
  let match;
  let key = 0;
  while ((match = regex.exec(text)) !== null) {
    const token = match[0];
    const upper = token.toUpperCase();
    let className = '';
    if (token.startsWith('--')) {
      className = 'sql-comment';
    } else if (token.startsWith("'") || token.startsWith('"')) {
      className = 'sql-string';
    } else if (/^\d/.test(token)) {
      className = 'sql-number';
    } else if (SQL_KEYWORDS.has(upper)) {
      className = 'sql-keyword';
    } else if (SQL_FUNCTIONS.has(token.toLowerCase())) {
      className = 'sql-function';
    } else if (/^[,;()=<>!+\-*/%&|^~]$/.test(token)) {
      className = 'sql-operator';
    }
    parts.push(<span key={key++} className={className}>{token}</span>);
  }
  return parts;
}

export default function SqlEditor({ value, onChange, onExecute, placeholder, disabled }) {
  const lineNumbersRef = useRef(null);
  const editorWrapRef = useRef(null);
  const [lineCount, setLineCount] = useState(1);

  useEffect(() => {
    setLineCount(Math.max((value || '').split('\n').length, 1));
  }, [value]);

  // 监听 react-simple-code-editor 内部 textarea 的滚动，同步行号
  useEffect(() => {
    const wrap = editorWrapRef.current;
    if (!wrap) return;
    const ta = wrap.querySelector('textarea');
    if (!ta) return;
    const handleScroll = () => {
      if (lineNumbersRef.current) {
        lineNumbersRef.current.scrollTop = ta.scrollTop;
      }
    };
    ta.addEventListener('scroll', handleScroll);
    return () => ta.removeEventListener('scroll', handleScroll);
  }, []);

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const start = e.target.selectionStart;
      const end = e.target.selectionEnd;
      const newValue = value.substring(0, start) + '  ' + value.substring(end);
      onChange(newValue);
      requestAnimationFrame(() => {
        e.target.selectionStart = e.target.selectionEnd = start + 2;
      });
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (onExecute) onExecute();
    }
  }, [value, onChange, onExecute]);

  return (
    <div style={{
      border: '1px solid #d9d9d9',
      borderRadius: 6,
      overflow: 'hidden',
      background: '#fff',
    }}>
      <style>{`
        .sql-keyword { color: #0033b3; font-weight: 600; }
        .sql-function { color: #00627a; }
        .sql-string { color: #067d17; }
        .sql-number { color: #1750db; }
        .sql-comment { color: #8c8c8c; font-style: italic; }
        .sql-operator { color: #333; }
        .sql-line-num { color: #999; text-align: right; user-select: none; padding-right: 12px; }
        /* react-simple-code-editor 内部对齐 */
        .sql-editor-wrap textarea,
        .sql-editor-wrap pre {
          font-family: 'Consolas', 'Monaco', 'Courier New', monospace !important;
          font-size: 13px !important;
          line-height: 1.6 !important;
        }
        .sql-editor-wrap textarea::placeholder {
          color: #bfbfbf;
          font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
        }
      `}</style>

      {/* 工具栏 */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '6px 12px', background: '#fafafa', borderBottom: '1px solid #e8e8e8',
      }}>
        <span style={{ fontSize: 12, color: '#333', fontWeight: 600 }}>SQL 编辑器</span>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => { if (value) navigator.clipboard.writeText(value); }}
            style={{ padding: '2px 8px', fontSize: 11, borderRadius: 3, border: '1px solid #d9d9d9', background: 'transparent', color: '#666', cursor: 'pointer' }}
            title="复制 SQL"
          >
            📋 复制
          </button>
          <button
            onClick={() => onChange('')}
            style={{ padding: '2px 8px', fontSize: 11, borderRadius: 3, border: '1px solid #d9d9d9', background: 'transparent', color: '#666', cursor: 'pointer' }}
            title="清空"
          >
            ✕ 清空
          </button>
          <button
            onClick={onExecute}
            disabled={disabled || !value?.trim()}
            style={{
              padding: '3px 12px', fontSize: 12, borderRadius: 3, border: 'none',
              background: (disabled || !value?.trim()) ? '#d9d9d9' : '#1890ff',
              color: '#fff', cursor: (disabled || !value?.trim()) ? 'not-allowed' : 'pointer',
              fontWeight: 600,
            }}
          >
            ▶ 执行 (Ctrl+Enter)
          </button>
        </div>
      </div>

      {/* 编辑器主体：行号 + Editor */}
      <div style={{ display: 'flex', minHeight: 140, maxHeight: 400 }}>
        {/* 行号 */}
        <div
          ref={lineNumbersRef}
          style={{
            padding: '12px 0', minWidth: 44, background: '#fafafa',
            borderRight: '1px solid #e8e8e8', overflow: 'hidden',
          }}
        >
          {Array.from({ length: lineCount }, (_, i) => (
            <div key={i} className="sql-line-num" style={{ lineHeight: '1.6', fontSize: 13, paddingLeft: 8 }}>
              {i + 1}
            </div>
          ))}
        </div>

        {/* react-simple-code-editor */}
        <div ref={editorWrapRef} className="sql-editor-wrap" style={{ flex: 1, minWidth: 0, background: '#fff' }}>
          <Editor
            value={value || ''}
            onValueChange={onChange}
            highlight={highlightSql}
            padding={12}
            disabled={disabled}
            placeholder={placeholder}
            onKeyDown={handleKeyDown}
            tabSize={2}
            insertSpaces
            ignoreTabKey={false}
            style={{
              fontFamily: '"Consolas", "Monaco", "Courier New", monospace',
              fontSize: 13,
              backgroundColor: '#fff',
              color: '#333',
              minHeight: 120,
            }}
          />
        </div>
      </div>

      {/* 状态栏 */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', padding: '3px 12px',
        background: '#fafafa', borderTop: '1px solid #e8e8e8', fontSize: 11, color: '#999',
      }}>
        <span>{lineCount} 行</span>
        <span>DuckDB | UTF-8</span>
      </div>
    </div>
  );
}
