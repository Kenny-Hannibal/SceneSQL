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

  // 查询模式 & batch 选择
  const [queryMode, setQueryMode] = useState('sqlite');
  const [batchId, setBatchId] = useState('');
  const [batches, setBatches] = useState([]);

  // SQL 编辑器
  const [sqlEditor, setSqlEditor] = useState('');
  const [sqlEditMode, setSqlEditMode] = useState('auto'); // 'auto' | 'preview'

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
      payload.db_path = dbPath.trim();
    } else {
      payload.batch_id = batchId;
      payload.query_mode = queryMode;
    }
    return payload;
  };

  // 执行 SQL 编辑器中的 SQL
  const handleExecuteSql = async () => {
    const sql = sqlEditor.trim();
    if (!sql) return;
    setLoading(true);
    setError('');
    setResult(null);
    clearProgress();
    setVideoRows([]);

    try {
      const payload = {
        sql,
        db_limit: Number(dbLimit) || 30,
        result_limit: Number(resultLimit) || 100,
      };
      if (dbPath.trim()) {
        payload.db_path = dbPath.trim();
      } else {
        payload.batch_id = batchId;
        payload.query_mode = queryMode;
      }

      const res = await fetch(`${API_BASE}/api/agent/execute-sql`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
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

  // LLM 查询（auto 模式：直接执行，preview 模式：仅填入编辑器）
  const handleSubmit = async () => {
    if (!question.trim()) return;
    setLoading(true);
    setError('');
    setResult(null);
    clearProgress();
    setVideoRows([]);

    if (sqlEditMode === 'preview') {
      // 仅生成 SQL，填入编辑器
      try {
        const res = await fetch(`${API_BASE}/api/agent/generate-sql`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(buildPayload()),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || err.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        if (data.error) {
          setError(data.error);
        } else {
          setSqlEditor(data.sql || '');
          if (data.validation_error) {
            setError('SQL 校验警告: ' + data.validation_error);
          }
        }
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
      return;
    }

    // auto 模式：直接执行（原有逻辑）
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
      setSqlEditor(data.sql || '');
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

    if (sqlEditMode === 'preview') {
      // Stream 模式下 preview 也走 generate-sql
      try {
        const res = await fetch(`${API_BASE}/api/agent/generate-sql`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(buildPayload()),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || err.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        if (data.error) {
          setError(data.error);
        } else {
          setSqlEditor(data.sql || '');
          if (data.validation_error) {
            setError('SQL 校验警告: ' + data.validation_error);
          }
        }
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
      return;
    }

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
                setSqlEditor(data.sql || '');
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
    // 如果 bag_path 为空，让用户手动输入或尝试解析
    let bagPath = row.bag_path;
    if (!bagPath) {
      if (row.bag_id) {
        try {
          const resolveRes = await fetch(`${API_BASE}/api/agent/resolve-bag-path?bag_id=${encodeURIComponent(row.bag_id)}`);
          if (resolveRes.ok) {
            const resolveData = await resolveRes.json();
            if (resolveData.bag_path) {
              bagPath = resolveData.bag_path;
            }
          }
        } catch (e) {
          // resolve API 不可用，降级
        }
      }
      if (!bagPath) {
        bagPath = window.prompt(
          `无法自动解析 bag 本地路径（bag_id: ${row.bag_id || '未知'}）。\n请手动输入 bag 的本地路径：`,
          ''
        );
        if (!bagPath) return;
      }
    }

    // 获取 bag 实际时间范围，用于 clamp
    let bagStartNs = null;
    let bagEndNs = null;
    let clampedMsg = '';
    try {
      const bagInfoRes = await fetch(`${API_BASE}/api/bag/info?bag_path=${encodeURIComponent(bagPath)}`, { method: 'POST' });
      if (bagInfoRes.ok) {
        const bagInfo = await bagInfoRes.json();
        bagStartNs = bagInfo.start_time_ns;
        bagEndNs = bagInfo.end_time_ns;
      }
    } catch (e) {
      // bag info 不可用，跳过 clamp
    }

    const topic = window.prompt(
      `请输入该 bag 的 camera topic 名称来提取视频：\nbag_path: ${bagPath}\n时间范围: ${row.start_ts} ~ ${row.end_ts} (秒)`,
      '/camera/front_center'
    );
    if (!topic) return;

    let startTs = row.start_ts ? Math.round(row.start_ts * 1e9) : null;
    let endTs = row.end_ts ? Math.round(row.end_ts * 1e9) : null;

    // Clamp: start_ts 不能小于 bag 起始时间
    if (startTs !== null && bagStartNs !== null && startTs < bagStartNs) {
      clampedMsg += `start_ts ${row.start_ts}s 早于 bag 起始时间 ${Number(bagStartNs / 1e9).toFixed(1)}s，已自动调整\n`;
      startTs = bagStartNs;
    }
    // Clamp: end_ts 不能大于 bag 结束时间
    if (endTs !== null && bagEndNs !== null && endTs > bagEndNs) {
      clampedMsg += `end_ts ${row.end_ts}s 晚于 bag 结束时间 ${Number(bagEndNs / 1e9).toFixed(1)}s，已自动调整\n`;
      endTs = bagEndNs;
    }

    if (clampedMsg) {
      alert('⏱️ 时间范围已自动调整：\n\n' + clampedMsg + '\n将按调整后的范围播放视频。');
    }

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
              padding: '6px 14px', fontSize: 13, borderRadius: 4, border: '1px solid #d9d9d9',
              background: queryMode === 'sqlite' ? '#722ed1' : '#fff',
              color: queryMode === 'sqlite' ? '#fff' : '#555', cursor: 'pointer',
            }}
          >
            🗃️ SQLite 原始查询
          </button>
          <button
            onClick={() => setQueryMode('parquet')}
            style={{
              padding: '6px 14px', fontSize: 13, borderRadius: 4, border: '1px solid #d9d9d9',
              background: queryMode === 'parquet' ? '#13c2c2' : '#fff',
              color: queryMode === 'parquet' ? '#fff' : '#555', cursor: 'pointer',
            }}
          >
            📦 Parquet 聚合查询
          </button>
        </div>

        {/* LLM 行为切换：直接执行 vs 仅生成 */}
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ fontSize: 13, color: '#555', fontWeight: 500 }}>LLM 行为:</span>
          <button
            onClick={() => setSqlEditMode('auto')}
            style={{
              padding: '6px 14px', fontSize: 13, borderRadius: 4, border: '1px solid #d9d9d9',
              background: sqlEditMode === 'auto' ? '#fa8c16' : '#fff',
              color: sqlEditMode === 'auto' ? '#fff' : '#555', cursor: 'pointer',
            }}
          >
            ⚡ 直接执行
          </button>
          <button
            onClick={() => setSqlEditMode('preview')}
            style={{
              padding: '6px 14px', fontSize: 13, borderRadius: 4, border: '1px solid #d9d9d9',
              background: sqlEditMode === 'preview' ? '#52c41a' : '#fff',
              color: sqlEditMode === 'preview' ? '#fff' : '#555', cursor: 'pointer',
            }}
          >
            ✏️ 仅生成 SQL
          </button>
          <span style={{ fontSize: 12, color: '#999' }}>
            {sqlEditMode === 'auto' ? 'LLM 生成 SQL 后自动执行查询' : 'LLM 生成 SQL 后填入下方编辑器，手动审查后执行'}
          </span>
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

        {/* 自然语言输入 + 提交按钮 */}
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
              padding: '10px 20px', fontSize: 14, borderRadius: 4, border: 'none',
              background: loading ? '#ccc' : (sqlEditMode === 'preview' ? '#52c41a' : '#722ed1'),
              color: '#fff', cursor: loading ? 'not-allowed' : 'pointer',
            }}
          >
            {loading ? 'Thinking...' : (sqlEditMode === 'preview' ? '✏️ 生成 SQL' : '⚡ Query')}
          </button>
          <button
            onClick={handleSubmitStream}
            disabled={loading || !question.trim()}
            style={{
              padding: '10px 20px', fontSize: 14, borderRadius: 4, border: 'none',
              background: loading ? '#ccc' : '#13c2c2',
              color: '#fff', cursor: loading ? 'not-allowed' : 'pointer',
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

      {/* SQL 编辑器 */}
      <div style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <div style={{ fontSize: 12, color: '#666', fontWeight: 600 }}>SQL 编辑器</div>
          <button
            onClick={handleExecuteSql}
            disabled={loading || !sqlEditor.trim()}
            style={{
              padding: '6px 16px', fontSize: 13, borderRadius: 4, border: 'none',
              background: (loading || !sqlEditor.trim()) ? '#ccc' : '#1890ff',
              color: '#fff', cursor: (loading || !sqlEditor.trim()) ? 'not-allowed' : 'pointer',
            }}
          >
            ▶ 执行 SQL
          </button>
        </div>
        <textarea
          value={sqlEditor}
          onChange={(e) => setSqlEditor(e.target.value)}
          placeholder="在此输入或编辑 SQL，也可以由 LLM 生成后填入...&#10;&#10;例如：SELECT bag_id, tag_name, start_ts, end_ts FROM range_tag WHERE tag_name = 'intersection_left_turn' LIMIT 100;"
          spellCheck={false}
          style={{
            width: '100%', minHeight: 120, padding: 12, fontSize: 13, fontFamily: 'Consolas, Monaco, "Courier New", monospace',
            borderRadius: 4, border: '1px solid #d9d9d9', background: '#fafafa', resize: 'vertical', lineHeight: 1.6,
          }}
        />
      </div>

      {result && (
        <div style={{ marginTop: 16 }}>
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
                              padding: '4px 10px', fontSize: 12, borderRadius: 4, border: 'none',
                              background: '#1890ff', color: '#fff', cursor: 'pointer',
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
