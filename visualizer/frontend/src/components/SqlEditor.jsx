import React, { useRef, useCallback, useEffect, useState } from 'react';

/**
 * SqlEditor — 专业化 SQL 编辑器组件 (Light Theme)
 * 特性：语法高亮、行号、Tab 缩进、自动大写关键字、快捷键（Ctrl+Enter 执行）
 */

// SQL 关键字列表（用于高亮）
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

// 简单 SQL 语法高亮：将 SQL 文本转为 span 数组
function highlightSql(text) {
  if (!text) return [];
  const parts = [];
  // 按单词和空白分割
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
  const textareaRef = useRef(null);
  const lineNumbersRef = useRef(null);
  const [lineCount, setLineCount] = useState(1);

  // 计算行数
  useEffect(() => {
    const lines = (value || '').split('\n').length;
    setLineCount(Math.max(lines, 1));
  }, [value]);

  // 同步行号滚动
  const handleScroll = useCallback(() => {
    if (textareaRef.current && lineNumbersRef.current) {
      lineNumbersRef.current.scrollTop = textareaRef.current.scrollTop;
    }
  }, []);

  // Tab 键缩进
  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const start = e.target.selectionStart;
      const end = e.target.selectionEnd;
      const newValue = value.substring(0, start) + '  ' + value.substring(end);
      onChange(newValue);
      // 恢复光标位置
      requestAnimationFrame(() => {
        e.target.selectionStart = e.target.selectionEnd = start + 2;
      });
    }
    // Ctrl+Enter / Cmd+Enter 执行 SQL
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (onExecute) onExecute();
    }
  }, [value, onChange, onExecute]);

  // 高亮预览（叠加在 textarea 上方）
  const highlightedPreview = highlightSql(value);

  return (
    <div style={{
      border: '1px solid #d9d9d9',
      borderRadius: 6,
      overflow: 'hidden',
      background: '#fff',
    }}>
      {/* CSS 样式 — Light 主题 */}
      <style>{`
        .sql-keyword { color: #0033b3; font-weight: 600; }
        .sql-function { color: #00627a; }
        .sql-string { color: #067d17; }
        .sql-number { color: #1750db; }
        .sql-comment { color: #8c8c8c; font-style: italic; }
        .sql-operator { color: #333; }
        .sql-line-num { color: #999; text-align: right; user-select: none; padding-right: 12px; }
        .sql-editor-textarea {
          background: transparent; color: transparent; caret-color: #333;
          position: absolute; top: 0; left: 0; width: 100%; height: 100%;
          font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
          font-size: 13px; line-height: 1.6; padding: 12px 12px 12px 0;
          border: none; outline: none; resize: none; z-index: 2;
          white-space: pre; overflow: auto; tab-size: 2;
        }
        .sql-editor-highlight {
          position: absolute; top: 0; left: 0; width: 100%; height: 100%;
          font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
          font-size: 13px; line-height: 1.6; padding: 12px 12px 12px 0;
          white-space: pre; overflow: hidden; z-index: 1; pointer-events: none;
          color: #333;
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
            onClick={() => { if (value) { navigator.clipboard.writeText(value); } }}
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

      {/* 编辑器主体：行号 + 代码区域 */}
      <div style={{ display: 'flex', position: 'relative', minHeight: 140, maxHeight: 400 }}>
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

        {/* 代码区 */}
        <div style={{ position: 'relative', flex: 1, overflow: 'hidden' }}>
          {/* 高亮层 */}
          <div className="sql-editor-highlight">
            {highlightedPreview}
          </div>
          {/* 输入层 */}
          <textarea
            ref={textareaRef}
            value={value || ''}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            onScroll={handleScroll}
            placeholder={placeholder}
            spellCheck={false}
            className="sql-editor-textarea"
            disabled={disabled}
          />
        </div>
      </div>

      {/* 状态栏 */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', padding: '3px 12px',
        background: '#fafafa', borderTop: '1px solid #e8e8e8', fontSize: 11, color: '#999',
      }}>
        <span>{lineCount} 行</span>
        <span>SQLite | UTF-8</span>
      </div>
    </div>
  );
}
