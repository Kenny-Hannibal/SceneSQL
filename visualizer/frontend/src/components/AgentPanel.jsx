import React, { useState, useEffect, useRef } from 'react';

const API_BASE = process.env.REACT_APP_API_BASE || '';

export default function AgentPanel() {
  const [question, setQuestion] = useState('');
  const [dbPath, setDbPath] = useState('');
  const [dbLimit, setDbLimit] = useState(30);
  const [resultLimit, setResultLimit] = useState(100);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState([]);

  // 新增：查询模式 & batch 选择
  const [queryMode, setQueryMode] = useState('sqlite');
  const [batchId, setBatchId] = useState('');
  const [batches, setBatches] = useState([]);

  // Video extraction states
  const [videoRows, setVideoRows] = useState([]);
  const intervalRef = useRef(null);

  const addProgress = (msg) => setProgress((prev) => [...prev, msg]);
  const clearProgress = () => setProgress([]);

  // 组件挂载时获取 batch 列表
  useEffect(() => {
    fetch(`${API_BASE}/api/agent/batches`)
      .then((res) => res.json())
      .then((data) => {
        setBatches(data || []);
        if (data && data.length > 0 && !batchId) {
          setBatchId(data[0].batch_id);
        }
      })
      .catch((e) => console.error('获取 batch 列表失败:', e));
  }, []);

  // 构建请求 payload
  const buildPayload = () => {
    const payload = {
      question: question.trim(),
      db_limit: Number(dbLimit) || 30,
      result_limit: Number(resultLimit) || 100,
    };

    if (dbPath.trim()) {
      // 手动路径优先
      payload.db_path = dbPath.trim();
    } else {
      // 使用 batch_id + query_mode
      payload.batch_id = batchId;
      payload.query_mode = queryMode;
    }
    return payload;
  };

  const handleSubmit = async () => {
    if (!question.trim()) return;
    setLoading(true);
    setError('');
    setResult(null);
    clearProgress();
    setVideoRows([]);

    try {
      const res = await fetch(`${API_BASE}/api/agent/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setResult(data);
      if (data.error && !data.rows?.length) {
        setError(data.error);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmitStream = async () => {
    if (!question.trim()) return;
    setLoading(true);
    setError('');
    setResult(null);
    clearProgress();
    setVideoRows([]);

    try {
      const response = await fetch(`${API_BASE}/api/agent/query-stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const jsonStr = line.slice(6);
            try {
              const data = JSON.parse(jsonStr);
              if (data.stage === 'error') {
                setError(data.message);
              } else if (data.stage === 'completed') {
                setResult({
                  sql: data.sql,
                  explanation: data.explanation,
                  columns: data.columns,
                  rows: data.rows,
                  error: data.error,
                  scanned_dbs: data.scanned_dbs,
                  matched_dbs: data.matched_dbs,
                });
              } else {
                addProgress(data.message || data.stage);
              }
            } catch (e) {
              // ignore parse errors
            }
          }
        }
      }
    } catch (e) {
      setError('Stream failed: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  const startVisualization = async (row) => {
    const topic = window.prompt(
      `请输入该 bag 的 camera topic 名称来提取视频：\nbag_path: ${row.bag_path || '未知'}\n时间范围: ${row.start_ts} ~ ${row.end_ts} (秒)`,
      '/camera/front_center'
    );
    if (!topic) return;

    const bagPath = row.bag_path;
    if (!bagPath) {
      alert('无法获取该场景的 bag 本地路径，可能 OSS 映射未配置');
      return;
    }

    const startTs = row.start_ts ? Math.round(row.start_ts * 1e9) : null;
    const endTs = row.end_ts ? Math.round(row.end_ts * 1e9) : null;

    try {
      const res = await fetch(`${API_BASE}/api/video/extract`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bag_path: bagPath, topic, start_ts: startTs, end_ts: endTs }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Extraction failed');
      }
      const data = await res.json();
      setVideoRows((prev) => [
        ...prev.filter((v) => v.task_id !== data.task_id),
        { task_id: data.task_id, row, topic, status: data.status, video_url: '', progress: 0, message: data.message },
      ]);
    } catch (e) {
      alert('启动视频提取失败: ' + e.message);
    }
  };

  // Poll video extraction status
  useEffect(() => {
    if (videoRows.length === 0) {
      if (intervalRef.current) clearInterval(intervalRef.current);
      return;
    }
    const pending = videoRows.some((v) => v.status === 'pending' || v.status === 'processing');
    if (!pending) {
      if (intervalRef.current) clearInterval(intervalRef.current);
      return;
    }

    intervalRef.current = setInterval(async () => {
      const currentRows = [];
      for (const v of videoRows) {
        if (v.status !== 'pending' && v.status !== 'processing') {
          currentRows.push(v);
          continue;
        }
        try {
          const res = await fetch(`${API_BASE}/api/video/status/${v.task_id}`);
          if (!res.ok) {
            currentRows.push({ ...v, status: 'failed', message: 'Status fetch failed' });
            continue;
          }
          const data = await res.json();
          currentRows.push({ ...v, status: data.status, video_url: data.video_url || '', progress: data.progress || 0, message: data.message || '' });
        } catch (e) {
          currentRows.push({ ...v, status: 'failed', message: String(e) });
        }
      }
      setVideoRows(currentRows);
    }, 1500);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [videoRows]);

  const hasResults = result && result.rows && result.rows.length > 0;
  const columns = result?.columns || [];

  return (
    <div style={{ padding: 20, background: '#fff', borderRadius: 8, marginBottom: 20, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
      <h2>🤖 NL2SQL Agent</h2>

      <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
        {/* 查询模式切换 */}
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ fontSize: 13, color: '#555', fontWeight: 500 }}>查询模式:</span>
          <button
            onClick={() => setQueryMode('sqlite')}
            style={{
              padding: '6px 14px',
              fontSize: 13,
              borderRadius: 4,
              border: '1px solid #d9d9d9',
              background: queryMode === 'sqlite' ? '#722ed1' : '#fff',
              color: queryMode === 'sqlite' ? '#fff' : '#555',
              cursor: 'pointer',
            }}
          >
            🗃️ SQLite 原始查询
          </button>
          <button
            onClick={() => setQueryMode('parquet')}
            style={{
              padding: '6px 14px',
              fontSize: 13,
              borderRadius: 4,
              border: '1px solid #d9d9d9',
              background: queryMode === 'parquet' ? '#13c2c2' : '#fff',
              color: queryMode === 'parquet' ? '#fff' : '#555',
              cursor: 'pointer',
            }}
          >
            📦 Parquet 聚合查询
          </button>
        </div>

        {/* Batch 下拉选择 */}
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ fontSize: 13, color: '#555', fontWeight: 500 }}>数据批次:</span>
          <select
            value={batchId}
            onChange={(e) => setBatchId(e.target.value)}
            style={{ padding: '8px 12px', fontSize: 13, borderRadius: 4, border: '1px solid #ccc', minWidth: 320 }}
          >
            {batches.length === 0 && <option value="">加载中...</option>}
            {batches.map((b) => (
              <option key={b.batch_id} value={b.batch_id}>
                {b.batch_id} ({b.sqlite_count} DBs{b.has_parquet ? ' / 已有Parquet' : ''})
              </option>
            ))}
          </select>
        </div>

        {/* 手动路径输入（可空） */}
        <input
          type="text"
          value={dbPath}
          onChange={(e) => setDbPath(e.target.value)}
          placeholder="手动输入路径（留空使用上方选择的批次）"
          style={{ padding: '10px', fontSize: 14, borderRadius: 4, border: '1px solid #ccc' }}
        />

        <div style={{ display: 'flex', gap: 10 }}>
          <input
            type="number"
            value={dbLimit}
            min={1}
            max={10000}
            onChange={(e) => setDbLimit(parseInt(e.target.value, 10) || 30)}
            placeholder="DB 数量限制"
            title="批量查询时最多扫描的 DB 数量"
            style={{ width: 100, padding: '10px', fontSize: 14, borderRadius: 4, border: '1px solid #ccc' }}
          />
          <input
            type="number"
            value={resultLimit}
            min={1}
            max={10000}
            onChange={(e) => setResultLimit(parseInt(e.target.value, 10) || 100)}
            placeholder="结果行数限制"
            title="单条 SQL 返回的最大行数"
            style={{ width: 100, padding: '10px', fontSize: 14, borderRadius: 4, border: '1px solid #ccc' }}
          />
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
            placeholder="用自然语言提问，例如：查询所有路口左转的场景"
            style={{ flex: 1, padding: '10px', fontSize: 14, borderRadius: 4, border: '1px solid #ccc' }}
          />
          <button
            onClick={handleSubmit}
            disabled={loading || !question.trim()}
            style={{
              padding: '10px 20px',
              fontSize: 14,
              borderRadius: 4,
              border: 'none',
              background: loading ? '#ccc' : '#722ed1',
              color: '#fff',
              cursor: loading ? 'not-allowed' : 'pointer',
            }}
          >
            {loading ? 'Thinking...' : 'Query'}
          </button>
          <button
            onClick={handleSubmitStream}
            disabled={loading || !question.trim()}
            style={{
              padding: '10px 20px',
              fontSize: 14,
              borderRadius: 4,
              border: 'none',
              background: loading ? '#ccc' : '#13c2c2',
              color: '#fff',
              cursor: loading ? 'not-allowed' : 'pointer',
            }}
          >
            {loading ? 'Streaming...' : 'Stream Query'}
          </button>
        </div>
      </div>

      {progress.length > 0 && (
        <div style={{ marginTop: 12, padding: 10, background: '#f0f5ff', borderRadius: 4, fontSize: 13 }}>
          {progress.map((p, i) => (
            <div key={i} style={{ marginBottom: 4 }}>{p}</div>
          ))}
        </div>
      )}

      {error && (
        <div style={{ marginTop: 12, padding: 10, background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 4, color: '#cf1322' }}>
          {error}
        </div>
      )}

      {result && (
        <div style={{ marginTop: 16 }}>
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>Generated SQL</div>
            <pre style={{ background: '#f6ffed', border: '1px solid #b7eb8f', borderRadius: 4, padding: 10, overflowX: 'auto', fontSize: 13 }}>
              {result.sql}
            </pre>
          </div>

          {result.explanation && (
            <div style={{ fontSize: 13, color: '#555', marginBottom: 12 }}>
              {result.explanation}
            </div>
          )}

          {hasResults && (
            <div>
              <div style={{ fontSize: 12, color: '#666', marginBottom: 4 }}>
                Results ({result.rows.length} rows)
                {result.scanned_dbs > 0 && (
                  <span style={{ marginLeft: 12, color: '#999' }}>
                    扫描 {result.scanned_dbs} 个 DB，命中 {result.matched_dbs} 个
                  </span>
                )}
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: '#fafafa' }}>
                      {columns.map((col) => (
                        <th key={col} style={{ border: '1px solid #e8e8e8', padding: '8px 12px', textAlign: 'left', fontWeight: 600 }}>
                          {col}
                        </th>
                      ))}
                      <th style={{ border: '1px solid #e8e8e8', padding: '8px 12px', textAlign: 'left', fontWeight: 600 }}>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((row, idx) => (
                      <tr key={idx} style={{ background: idx % 2 === 0 ? '#fff' : '#fafafa' }}>
                        {columns.map((col) => (
                          <td key={col} style={{ border: '1px solid #e8e8e8', padding: '8px 12px' }}>
                            {typeof row[col] === 'object' ? JSON.stringify(row[col]) : String(row[col] ?? '')}
                          </td>
                        ))}
                        <td style={{ border: '1px solid #e8e8e8', padding: '8px 12px' }}>
                          <button
                            onClick={() => startVisualization(row)}
                            style={{
                              padding: '4px 10px',
                              fontSize: 12,
                              borderRadius: 4,
                              border: 'none',
                              background: '#1890ff',
                              color: '#fff',
                              cursor: 'pointer',
                            }}
                          >
                            📹 播包可视化
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {result.rows && result.rows.length === 0 && !error && (
            <div style={{ color: '#666', fontSize: 14 }}>No rows returned.</div>
          )}
        </div>
      )}

      {/* Video extraction panel */}
      {videoRows.length > 0 && (
        <div style={{ marginTop: 20, padding: 16, background: '#f0f5ff', borderRadius: 8, border: '1px solid #d6e4ff' }}>
          <h3 style={{ margin: '0 0 12px 0', fontSize: 16 }}>📹 播包可视化</h3>
          {videoRows.map((v) => (
            <div key={v.task_id} style={{ marginBottom: 16, padding: 12, background: '#fff', borderRadius: 6, border: '1px solid #e8e8e8' }}>
              <div style={{ fontSize: 13, color: '#555', marginBottom: 6 }}>
                <b>Bag:</b> {v.row.bag_id || v.row.db_file} &nbsp;|&nbsp;
                <b>Topic:</b> {v.topic} &nbsp;|&nbsp;
                <b>Time:</b> {v.row.start_ts} ~ {v.row.end_ts}
              </div>
              <div style={{ fontSize: 13, color: '#666' }}>
                {v.status === 'pending' && '⏳ 等待中...'}
                {v.status === 'processing' && `⏳ 提取中... ${v.progress.toFixed(1)}%`}
                {v.status === 'completed' && '✅ 提取完成'}
                {v.status === 'failed' && `❌ 失败: ${v.message}`}
              </div>
              {v.video_url && (
                <video
                  src={v.video_url}
                  controls
                  style={{ width: '100%', maxHeight: 400, background: '#000', borderRadius: 4, marginTop: 8 }}
                />
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
