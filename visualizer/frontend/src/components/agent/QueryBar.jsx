import React from 'react';
import { colors, segBtn, input, select, fieldLabel, btn } from '../../theme';

// 查询控制区：查询模式 / LLM 行为 / 数据批次 / 手动路径 / 行数限制 / 自然语言输入。
// 纯受控组件，所有 state 由 AgentPanel 持有。
export default function QueryBar({
  queryMode, setQueryMode,
  sqlEditMode, setSqlEditMode,
  batches, batchId, setBatchId,
  dbPath, setDbPath,
  resultLimitInput, setResultLimitInput,
  resultLimitUnlimited, setResultLimitUnlimited,
  pageSize, setPageSize, resetPage,
  question, setQuestion,
  loading, onSubmit, onSubmitStream, onCancel,
}) {
  return (
    <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* 查询模式切换 */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={fieldLabel}>查询模式</span>
        <button onClick={() => setQueryMode('sqlite')} style={segBtn(queryMode === 'sqlite', colors.purple)}>
          🗃️ SQLite 原始查询
        </button>
        <button onClick={() => setQueryMode('parquet')} style={segBtn(queryMode === 'parquet', colors.cyan)}>
          📦 Parquet 聚合查询
        </button>
      </div>

      {/* LLM 行为切换：直接执行 vs 仅生成 */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={fieldLabel}>LLM 行为</span>
        <button onClick={() => setSqlEditMode('auto')} style={segBtn(sqlEditMode === 'auto', colors.orange)}>
          ⚡ 直接执行
        </button>
        <button onClick={() => setSqlEditMode('preview')} style={segBtn(sqlEditMode === 'preview', colors.success)}>
          ✏️ 仅生成 SQL
        </button>
        <span style={{ fontSize: 12, color: colors.textTertiary }}>
          {sqlEditMode === 'auto' ? 'LLM 生成 SQL 后自动执行查询' : 'LLM 生成 SQL 后填入下方编辑器，手动审查后执行'}
        </span>
      </div>

      {/* Batch 下拉选择 */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={fieldLabel}>数据批次</span>
        <select
          value={batchId}
          onChange={(e) => setBatchId(e.target.value)}
          style={{ ...select, minWidth: 320, maxWidth: '100%' }}
        >
          {batches.length === 0 && <option value="">加载中...</option>}
          {batches.map((b) => (
            <option
              key={b.batch_id}
              value={b.batch_id}
              disabled={queryMode === 'parquet' && !b.has_parquet}
            >
              {queryMode === 'parquet'
                ? `${b.batch_id} (${b.bag_count ?? 0} bags${b.has_parquet ? '' : ' / 无Parquet'})`
                : `${b.batch_id} (${b.sqlite_count} DBs${b.has_parquet ? ' / 已有Parquet' : ' / 无Parquet'})`}
            </option>
          ))}
        </select>
      </div>

      {/* 手动路径输入（可空） */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <input
          type="text"
          value={dbPath}
          onChange={(e) => setDbPath(e.target.value)}
          placeholder="手动输入路径（留空使用上方选择的批次）"
          style={{ ...input, flex: 1 }}
        />
        {dbPath.trim() && (
          <button onClick={() => setDbPath('')} style={btn.ghost(false)}>
            清空路径
          </button>
        )}
      </div>

      {/* 行数限制 + 每页行数 */}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <input
          type="text"
          inputMode="numeric"
          value={resultLimitUnlimited ? '' : resultLimitInput}
          disabled={resultLimitUnlimited}
          onChange={(e) => {
            const val = e.target.value;
            if (val === '' || /^\d+$/.test(val)) setResultLimitInput(val);
          }}
          onBlur={(e) => {
            const val = e.target.value.trim();
            const n = parseInt(val, 10);
            setResultLimitInput(!val || !Number.isFinite(n) || n <= 0 ? '100' : String(n));
          }}
          placeholder="结果行数限制"
          title="单条 SQL 返回的最大行数（聚焦时允许清空以便输入）"
          style={{ ...input, width: 110 }}
        />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: colors.textSecondary, cursor: 'pointer' }}
               title="交互浏览最多取回 5000 行；更多数据请用「导出 CSV」获取全量">
          <input
            type="checkbox"
            checked={resultLimitUnlimited}
            onChange={(e) => setResultLimitUnlimited(e.target.checked)}
            style={{ cursor: 'pointer' }}
          />
          不限制结果数量（上限5000行）
        </label>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <label style={{ fontSize: 13, color: colors.textSecondary, whiteSpace: 'nowrap' }}>每页显示</label>
          <input
            type="number"
            value={pageSize}
            min={5}
            max={500}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10);
              if (v >= 5 && v <= 500) { setPageSize(v); resetPage(); }
            }}
            title="每页显示行数 (5-500)"
            style={{ ...input, width: 76 }}
          />
        </div>
      </div>

      {/* 自然语言输入 + 提交按钮 */}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSubmit()}
          placeholder="用自然语言提问，例如：查询所有路口左转的场景"
          style={{ ...input, flex: 1, minWidth: 240, fontSize: 15, padding: '10px 14px' }}
        />
        <button
          onClick={onSubmit}
          disabled={loading || !question.trim()}
          style={{
            ...(sqlEditMode === 'preview' ? btn.success(loading || !question.trim()) : btn.purple(loading || !question.trim())),
            padding: '10px 20px',
            fontSize: 14,
          }}
        >
          {loading ? 'Thinking...' : (sqlEditMode === 'preview' ? '✏️ 生成 SQL' : '⚡ Query')}
        </button>
        <button
          onClick={onSubmitStream}
          disabled={loading || !question.trim()}
          style={{ ...btn.cyan(loading || !question.trim()), padding: '10px 20px', fontSize: 14 }}
        >
          {loading ? 'Streaming...' : '🌊 Stream Query'}
        </button>
        {/* P0：查询进行中提供取消入口（abort 底层 fetch/SSE） */}
        {loading && (
          <button
            onClick={onCancel}
            style={{ ...btn.ghost(false), padding: '10px 16px', fontSize: 14, borderColor: colors.error, color: colors.error }}
          >
            ✕ 取消
          </button>
        )}
      </div>
    </div>
  );
}
