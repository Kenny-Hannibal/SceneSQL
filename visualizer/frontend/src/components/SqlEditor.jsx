import React from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { sql } from '@codemirror/lang-sql';
import { keymap } from '@codemirror/view';

/**
 * SqlEditor — 基于 @uiw/react-codemirror (CodeMirror 6)
 * 原生支持行号、SQL 语法高亮、滚动、Tab/快捷键
 */

import { useMemo } from 'react';

export default function SqlEditor({ value, onChange, onExecute, placeholder, disabled }) {
  const lineCount = Math.max((value || '').split('\n').length, 1);

  const extensions = useMemo(() => [
    sql(),
    keymap.of([
      {
        key: 'Ctrl-Enter',
        run: () => {
          if (onExecute) onExecute();
          return true;
        },
      },
    ]),
  ], [onExecute]);

  return (
    <div style={{
      border: '1px solid #d9d9d9',
      borderRadius: 6,
      overflow: 'hidden',
      background: '#fff',
      position: 'relative',
      textAlign: 'left',
    }}>
      <style>{`
        /* 覆盖全局 text-align: center 对 CodeMirror 的影响 */
        .cm-editor { text-align: left !important; }
        .cm-content { text-align: left !important; }
      `}</style>
      {/* 工具栏 — sticky 定位，滚动时冻结在最上方 */}
      <div style={{
        position: 'sticky',
        top: 0,
        zIndex: 10,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '6px 12px',
        background: '#fafafa',
        borderBottom: '1px solid #e8e8e8',
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

      <CodeMirror
        value={value || ''}
        height="400px"
        extensions={extensions}
        onChange={onChange}
        placeholder={placeholder}
        editable={!disabled}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLineGutter: true,
          highlightSpecialChars: true,
          history: true,
          foldGutter: false,
          drawSelection: true,
          dropCursor: false,
          allowMultipleSelections: true,
          indentOnInput: true,
          syntaxHighlighting: true,
          bracketMatching: true,
          closeBrackets: true,
          autocompletion: false,
          rectangularSelection: true,
          crosshairCursor: true,
          highlightActiveLine: true,
          highlightSelectionMatches: true,
          closeBracketsKeymap: true,
          defaultKeymap: true,
          searchKeymap: true,
          historyKeymap: true,
          tabSize: 2,
        }}
      />

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
