import React, { useState, useEffect, useRef } from 'react';
import SqlEditor from './SqlEditor';

const API_BASE = process.env.REACT_APP_API_BASE || '';

// ── 带认证的 fetch wrapper ──
function authFetch(url, options = {}) {
  const token = localStorage.getItem('token');
  const headers = { ...options.headers };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return fetch(url, { ...options, headers }).then(response => {
    if (response.status === 401) {
      localStorage.removeItem('token');
      window.dispatchEvent(new CustomEvent('auth:401'));
    }
    return response;
  });
}

// ── 给 URL 拼接 token 参数（用于 <video src>、<img src> 等无法设 header 的场景）──
function addTokenParam(url) {
  const token = localStorage.getItem('token');
  if (!url || !token) return url || '';
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

// ============================================
// 分页控件组件
// ============================================
function PaginationControls({ page, pageSize, totalRows, onPageChange }) {
  const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
  const [jumpInput, setJumpInput] = useState('');
  const [showJump, setShowJump] = useState(false);
  const jumpRef = useRef(null);

  // 生成页码列表：首页 ... 当前页附近 ... 尾页
  function getPageNumbers() {
    const pages = [];
    const delta = 2; // 当前页前后各显示几页

    // 始终显示第一页
    pages.push(1);

    const rangeStart = Math.max(2, page - delta);
    const rangeEnd = Math.min(totalPages - 1, page + delta);

    // 左侧省略号
    if (rangeStart > 2) {
      pages.push('left-ellipsis');
    }

    // 中间页码
    for (let i = rangeStart; i <= rangeEnd; i++) {
      pages.push(i);
    }

    // 右侧省略号
    if (rangeEnd < totalPages - 1) {
      pages.push('right-ellipsis');
    }

    // 始终显示最后一页（如果总页数 > 1）
    if (totalPages > 1) {
      pages.push(totalPages);
    }

    return pages;
  }

  // 点击省略号跳转
  const handleJumpSubmit = () => {
    const num = parseInt(jumpInput, 10);
    if (isNaN(num) || num < 1) {
      onPageChange(1);
    } else if (num > totalPages) {
      onPageChange(totalPages);
    } else {
      onPageChange(num);
    }
    setJumpInput('');
    setShowJump(false);
  };

  // 仅允许输入数字
  const handleJumpInput = (e) => {
    const val = e.target.value;
    if (/^\d*$/.test(val)) {
      setJumpInput(val);
    }
  };

  // 省略号点击时显示输入框
  const handleEllipsisClick = (side) => {
    setShowJump(true);
    // 预填：左侧省略号跳到当前页-5，右侧跳到当前页+5
    if (side === 'left') {
      setJumpInput(String(Math.max(1, page - 5)));
    } else {
      setJumpInput(String(Math.min(totalPages, page + 5)));
    }
    setTimeout(() => {
      if (jumpRef.current) jumpRef.current.focus();
    }, 50);
  };

  const pageNumbers = getPageNumbers();

  const btnStyle = (active, disabled) => ({
    minWidth: 32,
    height: 32,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 4,
    border: active ? '1px solid #1890ff' : '1px solid #d9d9d9',
    background: active ? '#1890ff' : (disabled ? '#f5f5f5' : '#fff'),
    color: active ? '#fff' : (disabled ? '#bbb' : '#333'),
    cursor: disabled ? 'not-allowed' : 'pointer',
    fontSize: 13,
    padding: '0 6px',
    fontWeight: active ? 600 : 400,
  });

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 6, marginTop: 12, fontSize: 13, flexWrap: 'wrap' }}>
      {/* 上一页 */}
      <button
        disabled={page <= 1}
        onClick={() => onPageChange(page - 1)}
        style={btnStyle(false, page <= 1)}
      >‹</button>

      {/* 页码 */}
      {pageNumbers.map((p, idx) => {
        if (p === 'left-ellipsis' || p === 'right-ellipsis') {
          return (
            <span key={p} style={{ display: 'inline-flex', alignItems: 'center' }}>
              <button
                onClick={() => handleEllipsisClick(p === 'left-ellipsis' ? 'left' : 'right')}
                style={{
                  minWidth: 32, height: 32, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  borderRadius: 4, border: '1px solid #d9d9d9', background: '#fff',
                  color: '#1890ff', cursor: 'pointer', fontSize: 13, padding: '0 6px',
                }}
                title="点击跳转到指定页"
              >···</button>
              {showJump && (
                <input
                  ref={jumpRef}
                  type="text"
                  value={jumpInput}
                  onChange={handleJumpInput}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleJumpSubmit(); if (e.key === 'Escape') { setShowJump(false); setJumpInput(''); } }}
                  onBlur={() => { if (jumpInput) handleJumpSubmit(); else setShowJump(false); }}
                  placeholder="页码"
                  style={{
                    width: 50, height: 28, fontSize: 12, textAlign: 'center',
                    borderRadius: 4, border: '1px solid #1890ff', marginLeft: 4, padding: '0 4px',
                  }}
                />
              )}
            </span>
          );
        }
        return (
          <button
            key={p}
            onClick={() => onPageChange(p)}
            style={btnStyle(p === page, false)}
          >{p}</button>
        );
      })}

      {/* 下一页 */}
      <button
        disabled={page >= totalPages}
        onClick={() => onPageChange(page + 1)}
        style={btnStyle(false, page >= totalPages)}
      >›</button>

      {/* 总页数提示 */}
      <span style={{ color: '#999', fontSize: 12, marginLeft: 8 }}>
        共 {totalRows} 行 · 第 {page}/{totalPages} 页
      </span>
    </div>
  );
}

export default function AgentPanel() {
  const [question, setQuestion] = useState('');
  const [dbPath, setDbPath] = useState('');
  // 结果数量限制：聚焦时允许为空字符串，失焦后再校验
  const [resultLimitInput, setResultLimitInput] = useState('100');
  const [resultLimitUnlimited, setResultLimitUnlimited] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState([]);

  // 查询模式 & batch 选择
  const [queryMode, setQueryMode] = useState('sqlite');
  const [batchId, setBatchId] = useState(() => localStorage.getItem('lastBatchId') || '');
  const [batches, setBatches] = useState([]);

  // SQL 编辑器
  const [sqlEditor, setSqlEditor] = useState('');
  const [sqlEditMode, setSqlEditMode] = useState('auto'); // 'auto' | 'preview'
  const [streamingSql, setStreamingSql] = useState(''); // 实时显示流式生成的 SQL

  // SQL 执行进度弹窗
  const [sqlExecModalOpen, setSqlExecModalOpen] = useState(false);
  const [sqlExecElapsed, setSqlExecElapsed] = useState(0);
  const [sqlExecStatus, setSqlExecStatus] = useState('pending'); // pending | slow | stuck | loading_body | error
  const sqlExecTimerRef = useRef(null);
  const sqlExecSlowTimerRef = useRef(null);
  const sqlExecStuckTimerRef = useRef(null);

  // 已可视化行标记：记录用户点击过的行索引，用于深色高亮
  const [visualizedRows, setVisualizedRows] = useState(new Set());

  // ── 策略保存 ──
  const [saveStrategyModalOpen, setSaveStrategyModalOpen] = useState(false);
  const [strategyListOpen, setStrategyListOpen] = useState(false);
  const [strategyList, setStrategyList] = useState([]);
  const [strategyForm, setStrategyForm] = useState({ name: '', keywords: '', tag_name: '', description: '' });

  // Video extraction states
  const [videoRows, setVideoRows] = useState([]);
  const intervalRef = useRef(null);
  const pollCancelledRef = useRef(false);
  const videoRef = useRef(null);
  const abortControllerRef = useRef(null);
  const bagAbortControllerRef = useRef(null);
  const streamAbortControllerRef = useRef(null);
  const mseCleanupRef = useRef(null);

  // Extraction progress modal (replaces the bottom panel)
  const [extractModalOpen, setExtractModalOpen] = useState(false);

  // Pagination (client-side)
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [totalRows, setTotalRows] = useState(0);
  const [allRows, setAllRows] = useState([]);  // 缓存全量查询结果，翻页不发请求

  // Visualization modals
  const [topicModalOpen, setTopicModalOpen] = useState(false);
  const [topicModalData, setTopicModalData] = useState(null);
  const [selectedTopic, setSelectedTopic] = useState(() => localStorage.getItem('lastSelectedTopic') || '');
  const [playerModalOpen, setPlayerModalOpen] = useState(false);
  const [playerData, setPlayerData] = useState(null);
  const [playerMode, setPlayerMode] = useState(null); // 'hevc-stream' | 'h264-file'
  const [playerError, setPlayerError] = useState(null);
  const [forceH264, setForceH264] = useState(false);

  const addProgress = (msg) => setProgress((prev) => [...prev, msg]);
  const clearProgress = () => setProgress([]);

  // 监听手动路径输入，自动推断 queryMode
  useEffect(() => {
    if (!dbPath.trim()) return;
    const path = dbPath.trim().toLowerCase();
    if (path.endsWith('.db')) {
      setQueryMode('sqlite');
    } else if (path.includes('/parquet/') || path.includes('manifest.yaml') || path.includes('.parquet')) {
      setQueryMode('parquet');
    }
  }, [dbPath]);

  // Topic modal 关闭时取消未完成的 bag info 请求
  useEffect(() => {
    if (!topicModalOpen && bagAbortControllerRef.current) {
      bagAbortControllerRef.current.abort();
      bagAbortControllerRef.current = null;
    }
  }, [topicModalOpen]);

  // Player modal 关闭时强制断开 video stream，释放浏览器并发连接
  useEffect(() => {
    if (!playerModalOpen && streamAbortControllerRef.current) {
      streamAbortControllerRef.current.abort();
      streamAbortControllerRef.current = null;
    }
  }, [playerModalOpen]);

  // 忽略 HEVC stream 相关的未捕获 AbortError，避免前端崩溃
  useEffect(() => {
    const handler = (e) => {
      const reason = e.reason;
      if (reason && (reason.name === 'AbortError' || reason.code === 20 || String(reason).includes('aborted'))) {
        e.preventDefault();
        console.warn('[HEVC] Ignored AbortError:', reason.message || reason);
        return;
      }
      console.error('Unhandled rejection:', reason);
    };
    window.addEventListener('unhandledrejection', handler);
    return () => window.removeEventListener('unhandledrejection', handler);
  }, []);

  // 记忆上次选择的 batchId
  useEffect(() => {
    if (batchId) localStorage.setItem('lastBatchId', batchId);
  }, [batchId]);

  // 组件挂载时获取 batch 列表
  useEffect(() => {
    authFetch(`${API_BASE}/api/agent/batches`)
      .then(async (res) => {
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || `HTTP ${res.status}`);
        }
        return res.json();
      })
      .then((data) => {
        const arr = Array.isArray(data) ? data : [];
        setBatches(arr);
        if (arr.length > 0) {
          const saved = localStorage.getItem('lastBatchId');
          const savedExists = saved && arr.some((b) => b.batch_id === saved);
          if (savedExists) {
            setBatchId(saved);
          } else {
            const defaultBatch = queryMode === 'parquet'
              ? arr.find((b) => b.has_parquet)
              : arr[0];
            setBatchId(defaultBatch ? defaultBatch.batch_id : arr[0].batch_id);
          }
        }
      })
      .catch((e) => {
        console.error('获取 batch 列表失败:', e);
        setBatches([]);
        setError('获取数据批次列表失败: ' + e.message);
      });
  }, []);

  // 切换 queryMode 时自动切换到对应模式可用的 batch
  useEffect(() => {
    if (batches.length === 0 || !batchId) return;
    const current = batches.find((b) => b.batch_id === batchId);
    if (queryMode === 'parquet' && current && !current.has_parquet) {
      const firstParquet = batches.find((b) => b.has_parquet);
      if (firstParquet) setBatchId(firstParquet.batch_id);
    }
  }, [queryMode, batches]);

  // 解析当前有效的 result_limit（无限制返回 0）
  const getResultLimit = () => {
    if (resultLimitUnlimited) return 0;
    const v = parseInt(resultLimitInput, 10);
    return Number.isFinite(v) && v > 0 ? v : 100;
  };

  // 构建请求 payload（请求全量数据，翻页由前端完成）
  const buildPayload = () => {
    const payload = {
      question: question.trim(),
      result_limit: getResultLimit(),
      page: 1,
      page_size: getResultLimit() || 999999,  // 无限制时取回全部，前端分页
    };

    if (dbPath.trim()) {
      payload.db_path = dbPath.trim();
    } else {
      payload.batch_id = batchId;
      payload.query_mode = queryMode;
    }
    return payload;
  };

  // 执行 SQL 编辑器中的 SQL（取回全量数据，翻页由前端完成）
  // ── 策略保存/加载 ──
  const loadStrategyList = async () => {
    try {
      const res = await fetchWithAuth('/api/strategies');
      if (res.ok) {
        setStrategyList(await res.json());
      }
    } catch (e) { console.error('Failed to load strategies', e); }
  };

  const handleSaveStrategy = async () => {
    try {
      const keywords = strategyForm.keywords.split(/[,，]/).map(s => s.trim()).filter(Boolean);
      if (!strategyForm.name || !keywords.length || !sqlEditor.trim()) {
        alert('请填写策略名、关键词，并确保 SQL 不为空');
        return;
      }
      // 自动推断 tag_name：从 SQL 中提取第一个字符串字面量
      let tag_name = strategyForm.tag_name;
      if (!tag_name) {
        const m = sqlEditor.match(/(?:AS\s+tag_name|tag_name\s*=\s*)['"]([^'"]+)['"]/i)
          || sqlEditor.match(/['"]([A-Z][A-Za-z_]+)['"]\s+AS\s+tag_name/i);
        tag_name = m ? m[1] : strategyForm.name;
      }
      const res = await fetchWithAuth('/api/strategies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: strategyForm.name,
          keywords,
          tag_name,
          sql: sqlEditor.trim(),
          description: strategyForm.description,
        }),
      });
      if (res.ok) {
        setSaveStrategyModalOpen(false);
        setStrategyForm({ name: '', keywords: '', tag_name: '', description: '' });
        loadStrategyList();
        alert('策略已保存');
      } else {
        const err = await res.json();
        alert('保存失败: ' + (err.detail || JSON.stringify(err)));
      }
    } catch (e) {
      alert('保存失败: ' + e.message);
    }
  };

  const handleDeleteStrategy = async (name) => {
    if (!window.confirm(`确定删除策略 "${name}"？`)) return;
    try {
      const res = await fetchWithAuth(`/api/strategies/${name}`, { method: 'DELETE' });
      if (res.ok) loadStrategyList();
    } catch (e) { console.error(e); }
  };

  const handleLoadStrategy = (s) => {
    setSqlEditor(s.sql);
    setStrategyListOpen(false);
  };

  useEffect(() => { loadStrategyList(); }, []);

  const handleExecuteSql = async () => {
    const sql = sqlEditor.trim();
    if (!sql) return;
    // 取消旧请求
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    setError('');
    setSqlExecModalOpen(true);
    setSqlExecElapsed(0);
    setSqlExecStatus('pending');
    if (sqlExecTimerRef.current) clearInterval(sqlExecTimerRef.current);
    if (sqlExecSlowTimerRef.current) clearTimeout(sqlExecSlowTimerRef.current);
    if (sqlExecStuckTimerRef.current) clearTimeout(sqlExecStuckTimerRef.current);
    sqlExecTimerRef.current = setInterval(() => {
      setSqlExecElapsed((prev) => prev + 1);
    }, 1000);
    sqlExecSlowTimerRef.current = setTimeout(() => {
      setSqlExecStatus((s) => (s === 'pending' ? 'slow' : s));
    }, 5000);
    sqlExecStuckTimerRef.current = setTimeout(() => {
      setSqlExecStatus((s) => (s === 'pending' || s === 'slow' ? 'stuck' : s));
    }, 15000);

    try {
      const payload = {
        sql,
        result_limit: getResultLimit(),
        page: 1,
        page_size: getResultLimit() || 999999,  // 取回全量数据
      };
      if (dbPath.trim()) {
        payload.db_path = dbPath.trim();
      } else {
        payload.batch_id = batchId;
        payload.query_mode = queryMode;
      }

      const res = await authFetch(`${API_BASE}/api/agent/execute-sql`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      // 收到响应头，说明后端已经开始处理并返回，后续在接收 body
      setSqlExecStatus('loading_body');
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setResult(data);
      setAllRows(data.rows || []);
      setTotalRows((data.rows || []).length);
      setPage(1);  // 新查询重置到第 1 页
      setVisualizedRows(new Set());  // 新搜索清空可视化标记
      if (data.error && !data.rows?.length) {
        setError(data.error);
      }
    } catch (e) {
      setSqlExecStatus('error');
      setError(e.message);
    } finally {
      setLoading(false);
      setSqlExecModalOpen(false);
      if (sqlExecTimerRef.current) clearInterval(sqlExecTimerRef.current);
      if (sqlExecSlowTimerRef.current) clearTimeout(sqlExecSlowTimerRef.current);
      if (sqlExecStuckTimerRef.current) clearTimeout(sqlExecStuckTimerRef.current);
      sqlExecTimerRef.current = null;
      sqlExecSlowTimerRef.current = null;
      sqlExecStuckTimerRef.current = null;
    }
  };

  // LLM 查询（auto 模式：直接执行，preview 模式：仅填入编辑器）
  const handleSubmit = async () => {
    if (!question.trim()) return;
    // 取消旧请求
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    setError('');
    setResult(null);
    clearProgress();
    setVideoRows([]);
    setPage(1);  // 新查询重置到第 1 页

    if (sqlEditMode === 'preview') {
      // 仅生成 SQL，填入编辑器
      try {
        const res = await authFetch(`${API_BASE}/api/agent/generate-sql`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(buildPayload()),
          signal: controller.signal,
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
      const res = await authFetch(`${API_BASE}/api/agent/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
        signal: controller.signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setResult(data);
      setSqlEditor(data.sql || '');
      setAllRows(data.rows || []);
      setTotalRows((data.rows || []).length);
      setPage(1);
      setVisualizedRows(new Set());  // 新搜索清空可视化标记
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
    // 取消旧请求
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    setError('');
    setResult(null);
    clearProgress();
    setVideoRows([]);
    setPage(1);  // 新查询重置到第 1 页

    if (sqlEditMode === 'preview') {
      // Stream 模式下 preview 也走 generate-sql
      try {
        const res = await authFetch(`${API_BASE}/api/agent/generate-sql`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(buildPayload()),
          signal: controller.signal,
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
      const response = await authFetch(`${API_BASE}/api/agent/query-stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
        signal: controller.signal,
      });

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let sqlBuffer = '';

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
              } else if (data.stage === 'generating_token') {
                // 逐 token 实时显示 SQL 生成过程
                sqlBuffer += data.token;
                setStreamingSql(sqlBuffer);
              } else if (data.stage === 'sql_generated') {
                setStreamingSql(''); // 清除流式显示，最终 SQL 由编辑器接管
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
                setAllRows(data.rows || []);
                setTotalRows((data.rows || []).length);
                setPage(1);
                setVisualizedRows(new Set());  // 新搜索清空可视化标记
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
    // 同步执行旧的 MSE cleanup，确保 TCP 连接立即释放
    if (mseCleanupRef.current) {
      try {
        mseCleanupRef.current();
      } catch (e) {
        console.error('[HEVC] MSE cleanup error:', e);
      }
      mseCleanupRef.current = null;
    }
    // 取消旧的 bag info 请求
    if (bagAbortControllerRef.current) {
      bagAbortControllerRef.current.abort();
      bagAbortControllerRef.current = null;
    }
    // 关闭旧 video stream（避免浏览器并发连接被占满）
    if (streamAbortControllerRef.current) {
      streamAbortControllerRef.current.abort();
      streamAbortControllerRef.current = null;
    }
    // 强制关闭旧的播放器弹窗，避免多个 video stream 连接并发占满浏览器连接槽
    setPlayerModalOpen(false);
    setPlayerData(null);
    setVideoRows([]);

    // 给浏览器/后端一点时间彻底释放旧 video stream 的 TCP 连接和 ffmpeg 进程
    await new Promise((resolve) => setTimeout(resolve, 800));

    // 如果 bag_path 为空，尝试解析
    let bagPath = row.bag_path;
    if (!bagPath) {
      if (row.bag_id) {
        try {
          const resolveRes = await authFetch(`${API_BASE}/api/agent/resolve-bag-path?bag_id=${encodeURIComponent(row.bag_id)}`);
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

    // 打开 modal 并显示加载进度
    setTopicModalData({ bagPath, row, cameraTopics: [], startTs: null, endTs: null, clampedMsg: '', loading: true, loadingMsg: '正在加载 bag 信息...' });
    setSelectedTopic(localStorage.getItem('lastSelectedTopic') || '');
    setTopicModalOpen(true);

    // 使用 SSE 流式获取 bag info（带进度反馈）
    const controller = new AbortController();
    bagAbortControllerRef.current = controller;
    let bagStartNs = null;
    let bagEndNs = null;
    let clampedMsg = '';
    let cameraTopics = [];

    try {
      const response = await authFetch(`${API_BASE}/api/bag/info-stream?bag_path=${encodeURIComponent(bagPath)}`, { method: 'POST', signal: controller.signal });
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
            try {
              const data = JSON.parse(line.slice(6));
              if (data.stage === 'loading' || data.stage === 'parsing_topics') {
                setTopicModalData(prev => prev ? { ...prev, loading: true, loadingMsg: data.message } : prev);
              } else if (data.stage === 'completed' && data.bag_info) {
                const info = data.bag_info;
                bagStartNs = info.start_time_ns;
                bagEndNs = info.end_time_ns;
                cameraTopics = (info.topics || []).map((t) => t.name).filter(Boolean);
              } else if (data.stage === 'error') {
                // 降级：使用非流式 API
                throw new Error(data.message);
              }
            } catch (e) {
              // ignore parse errors
            }
          }
        }
      }
    } catch (e) {
      // 降级到非流式 API
      try {
        const bagInfoRes = await authFetch(`${API_BASE}/api/bag/info?bag_path=${encodeURIComponent(bagPath)}`, { method: 'POST' });
        if (bagInfoRes.ok) {
          const bagInfo = await bagInfoRes.json();
          bagStartNs = bagInfo.start_time_ns;
          bagEndNs = bagInfo.end_time_ns;
          cameraTopics = (bagInfo.topics || []).map((t) => t.name).filter(Boolean);
        }
      } catch (e2) {
        // bag info 不可用，跳过
      }
    }

    let startTs = row.start_ts ? Math.round(row.start_ts * 1e9) : null;
    let endTs = row.end_ts ? Math.round(row.end_ts * 1e9) : null;

    if (startTs !== null && bagStartNs !== null && startTs < bagStartNs) {
      clampedMsg += `start_ts ${row.start_ts}s 早于 bag 起始时间 ${Number(bagStartNs / 1e9).toFixed(1)}s，已自动调整\n`;
      startTs = bagStartNs;
    }
    if (endTs !== null && bagEndNs !== null && endTs > bagEndNs) {
      clampedMsg += `end_ts ${row.end_ts}s 晚于 bag 结束时间 ${Number(bagEndNs / 1e9).toFixed(1)}s，已自动调整\n`;
      endTs = bagEndNs;
    }

    setTopicModalData({ bagPath, row, cameraTopics, startTs, endTs, clampedMsg, loading: false, loadingMsg: '' });
    // 优先使用上次记忆的 topic，如果它在当前可用 topic 列表中；否则取第一个
    const lastTopic = localStorage.getItem('lastSelectedTopic') || '';
    const defaultTopic = (lastTopic && cameraTopics.includes(lastTopic)) ? lastTopic
      : (cameraTopics.length > 0 ? cameraTopics[0] : '');
    setSelectedTopic(defaultTopic);
  };

  const startH264Extraction = async (bagPath, row, startTs, endTs) => {
    try {
      const res = await authFetch(`${API_BASE}/api/video/extract`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bag_path: bagPath, topic: selectedTopic, start_ts: startTs, end_ts: endTs }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Extraction failed');
      }
      const data = await res.json();
      setVideoRows((prev) => [
        ...prev.filter((v) => v.task_id !== data.task_id),
        { task_id: data.task_id, row, topic: selectedTopic, status: data.status, video_url: '', progress: 0, message: data.message },
      ]);
      setTopicModalOpen(false);
      setTopicModalData(null);
      setExtractModalOpen(true);
    } catch (e) {
      alert('启动视频提取失败: ' + e.message);
    }
  };

  const handleExtractVideo = async () => {
    if (!topicModalData || !selectedTopic) return;
    const { bagPath, row, startTs, endTs, clampedMsg } = topicModalData;

    // 记忆用户选择的 topic
    localStorage.setItem('lastSelectedTopic', selectedTopic);

    if (clampedMsg) {
      alert('⏱️ 时间范围已自动调整：\n\n' + clampedMsg + '\n将按调整后的范围播放视频。');
    }

    setPlayerError(null);
    setPlayerMode(null);

    // 检测浏览器是否支持 HEVC in MP4（MSE）
    const hevcMime = 'video/mp4; codecs="hvc1.1.6.L120.B0"';
    const h264Mime = 'video/mp4; codecs="avc1.64001f"';
    const canPlayHevc = document.createElement('video').canPlayType(hevcMime);
    const supportsHevcMSE = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(hevcMime);
    const supportsH264MSE = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(h264Mime);

    console.log('[HEVC诊断] canPlayType:', canPlayHevc, '| HEVC MSE:', supportsHevcMSE, '| H264 MSE:', supportsH264MSE);

    const durationSec = (endTs !== null && startTs !== null) ? (endTs - startTs) / 1e9 : null;
    const streamToken = localStorage.getItem('token');

    // 构建 stream URL 的公共参数
    const buildStreamUrl = (endpoint) => {
      const params = new URLSearchParams({
        bag_path: bagPath,
        topic: selectedTopic,
      });
      if (startTs !== null) params.append('start_ts', String(startTs));
      if (endTs !== null) params.append('end_ts', String(endTs));
      if (streamToken) params.append('token', streamToken);
      return `${API_BASE}/api/video/${endpoint}?${params.toString()}`;
    };

    // 强制 H.264 模式
    if (forceH264) {
      if (supportsH264MSE) {
        console.log('[播放] 用户强制H.264，使用流式MSE播放');
        setPlayerMode('h264-stream');
        setPlayerData({
          stream_url: buildStreamUrl('stream-h264'),
          row,
          topic: selectedTopic,
          use_mse: true,
          mse_codec: h264Mime,
          durationSec,
        });
        setTopicModalOpen(false);
        setTopicModalData(null);
        setPlayerModalOpen(true);
      } else {
        console.log('[播放] 用户强制H.264，但MSE不支持H.264，降级到全量转码');
        setPlayerMode('h264-file');
        startH264Extraction(bagPath, row, startTs, endTs);
      }
      return;
    }

    if (supportsHevcMSE) {
      console.log('[播放] 浏览器支持HEVC MSE，流式播放');
      setPlayerMode('hevc-stream');
      setPlayerData({
        stream_url: buildStreamUrl('stream-hevc'),
        row,
        topic: selectedTopic,
        use_mse: true,
        mse_codec: hevcMime,
        durationSec,
      });
      setTopicModalOpen(false);
      setTopicModalData(null);
      setPlayerModalOpen(true);
      return;
    }

    // 浏览器不支持 HEVC MSE → 尝试 H.264 流式 MSE
    if (supportsH264MSE) {
      console.log('[播放] 浏览器不支持HEVC MSE，使用H.264流式MSE播放');
      setPlayerMode('h264-stream');
      setPlayerData({
        stream_url: buildStreamUrl('stream-h264'),
        row,
        topic: selectedTopic,
        use_mse: true,
        mse_codec: h264Mime,
        durationSec,
      });
      setTopicModalOpen(false);
      setTopicModalData(null);
      setPlayerModalOpen(true);
      return;
    }

    // MSE 完全不支持，降级到全量转码+文件播放
    console.log('[播放] 浏览器不支持任何MSE，降级到H.264全量转码');
    alert('当前浏览器不支持 MSE 流式播放，将使用 H.264 全量转码（需等待转码完成）。');
    setPlayerMode('h264-file');
    startH264Extraction(bagPath, row, startTs, endTs);
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

    // 新一轮轮询开始，标记未取消
    pollCancelledRef.current = false;

    intervalRef.current = setInterval(async () => {
      // 如果在 fetch 之前已经被取消（如用户关闭播放器），直接退出
      if (pollCancelledRef.current) return;

      let newlyCompleted = null;
      const currentRows = [];
      for (const v of videoRows) {
        if (v.status !== 'pending' && v.status !== 'processing') {
          currentRows.push(v);
          continue;
        }
        try {
          const res = await authFetch(`${API_BASE}/api/video/status/${v.task_id}`);
          if (pollCancelledRef.current) return;
          if (!res.ok) {
            currentRows.push({ ...v, status: 'failed', message: 'Status fetch failed' });
            continue;
          }
          const data = await res.json();
          const updated = { ...v, status: data.status, video_url: addTokenParam(data.video_url || ''), progress: data.progress || 0, message: data.message || '' };
          currentRows.push(updated);
          if (data.status === 'completed' && data.video_url && !playerModalOpen) {
            newlyCompleted = updated;
          }
        } catch (e) {
          currentRows.push({ ...v, status: 'failed', message: String(e) });
        }
      }
      setVideoRows(currentRows);
      if (newlyCompleted) {
        setPlayerMode('h264-file');
        setPlayerData({ video_url: newlyCompleted.video_url, task_id: newlyCompleted.task_id, row: newlyCompleted.row, topic: newlyCompleted.topic });
        setPlayerModalOpen(true);
        setExtractModalOpen(false);  // 关闭进度弹窗，打开播放器
      }
    }, 1500);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      // 清理时标记取消，防止已发出的 fetch 回调写入 stale 数据
      pollCancelledRef.current = true;
    };
  }, [videoRows]);

  // MSE 流式 HEVC 播放逻辑
  useEffect(() => {
    if (!playerModalOpen || !playerData?.use_mse || !videoRef.current) return;

    const video = videoRef.current;
    const mediaSource = new MediaSource();
    const objectUrl = URL.createObjectURL(mediaSource);
    video.src = objectUrl;

    let sourceBuffer = null;
    let reader = null;
    let aborted = false;
    const streamController = new AbortController();
    streamAbortControllerRef.current = streamController;

    // 根据流类型动态选择codec：HEVC用hvc1，H.264用avc1
    const mimeCodec = playerData.mse_codec || 'video/mp4; codecs="hvc1.1.6.L120.B0"';

    const cleanup = () => {
      if (aborted) return;
      aborted = true;
      // 1. 彻底断开 video 与 MediaSource / object URL 的绑定
      try { video.pause(); } catch (e) {}
      try {
        video.removeAttribute('src');
        video.load();
      } catch (e) {}
      try { URL.revokeObjectURL(objectUrl); } catch (e) {}
      // 2. abort SourceBuffer 上 pending 的 append
      if (sourceBuffer) {
        try { sourceBuffer.abort(); } catch (e) {}
      }
      // 3. 结束 MediaSource
      try {
        if (mediaSource.readyState === 'open') {
          mediaSource.endOfStream();
        }
      } catch (e) {}
      // 4. abort fetch，彻底关闭底层 TCP 连接
      // 只 abort controller；reader.read() 会因此 reject，被外层 try-catch 处理。
      // 不要单独调用 reader.cancel()，否则会触发未捕获的 AbortError promise rejection。
      try { streamController.abort(); } catch (e) {}
    };

    const onMseError = (source, detail) => {
      if (aborted) return; // 已 cleanup 后不再 setState，避免卸载后报错
      const videoErr = video.error;
      const msState = mediaSource.readyState;
      const sbState = sourceBuffer ? {
        updating: sourceBuffer.updating,
        buffered: sourceBuffer.buffered?.length,
      } : null;
      const diagnostics = {
        source,
        detail: detail || '未知错误',
        videoErrorCode: videoErr?.code,
        videoErrorMessage: videoErr?.message,
        mediaSourceState: msState,
        sourceBufferState: sbState,
      };
      console.error('[MSE诊断] MSE错误:', diagnostics);
      const msg = `[${source}] ${detail || '未知错误'} | video.error=${videoErr?.code || 'none'} | msState=${msState}`;
      setPlayerError('流式播放失败: ' + msg);
      cleanup();
    };

    const onSourceBufferError = (e) => {
      onMseError('SourceBuffer', e.message || 'SourceBuffer error');
    };
    const onMediaSourceError = (e) => {
      onMseError('MediaSource', e.message || 'MediaSource error');
    };
    const onVideoError = () => {
      if (aborted) return;
      const ve = video.error;
      if (ve) {
        const codes = { 1: 'MEDIA_ERR_ABORTED', 2: 'MEDIA_ERR_NETWORK', 3: 'MEDIA_ERR_DECODE', 4: 'MEDIA_ERR_SRC_NOT_SUPPORTED' };
        onMseError('VideoElement', `${codes[ve.code] || 'UNKNOWN'}: ${ve.message || ''}`);
      }
    };

    mediaSource.addEventListener('error', onMediaSourceError);
    video.addEventListener('error', onVideoError);

    mediaSource.addEventListener('sourceopen', async () => {
      if (aborted) return;
      try {
        // 预先设置预期总时长，避免进度条在加载过程中跳动
        if (playerData.durationSec && playerData.durationSec > 0) {
          try {
            mediaSource.duration = playerData.durationSec;
            console.log('[HEVC诊断] 预设视频时长:', playerData.durationSec, '秒');
          } catch (e) {
            console.warn('[HEVC诊断] 设置 duration 失败:', e);
          }
        }

        sourceBuffer = mediaSource.addSourceBuffer(mimeCodec);
        sourceBuffer.addEventListener('error', onSourceBufferError);

        const response = await authFetch(playerData.stream_url, { signal: streamController.signal });
        if (!response.ok) {
          throw new Error(`Stream HTTP ${response.status}`);
        }
        reader = response.body.getReader();

        const queue = [];
        let isUpdating = false;

        const processQueue = () => {
          if (aborted || isUpdating || queue.length === 0) return;
          const chunk = queue.shift();
          try {
            sourceBuffer.appendBuffer(chunk);
            isUpdating = true;
          } catch (e) {
            console.error('appendBuffer failed:', e);
            cleanup();
          }
        };

        const onUpdateEnd = () => {
          isUpdating = false;
          if (queue.length === 0 && reader === null) {
            try { mediaSource.endOfStream(); } catch (e) {}
            return;
          }
          processQueue();
        };
        sourceBuffer.addEventListener('updateend', onUpdateEnd);

        while (!aborted) {
          const { done, value } = await reader.read();
          if (done) {
            reader = null;
            if (!isUpdating && queue.length === 0) {
              try { mediaSource.endOfStream(); } catch (e) {}
            }
            break;
          }
          queue.push(value);
          processQueue();
        }

        sourceBuffer.removeEventListener('updateend', onUpdateEnd);
      } catch (e) {
        onMseError('Fetch/Setup', e.message || String(e));
      }
    });

    // 把 cleanup 暴露给外部，方便 startVisualization 里立即同步调用
    mseCleanupRef.current = cleanup;

    return () => {
      video.removeEventListener('error', onVideoError);
      mediaSource.removeEventListener('error', onMediaSourceError);
      if (sourceBuffer) {
        sourceBuffer.removeEventListener('error', onSourceBufferError);
      }
      cleanup();
      mseCleanupRef.current = null;
    };
  }, [playerModalOpen, playerData]);

  // 双向滚动条同步：顶部 + 底部
  useEffect(() => {
    const topBar = document.getElementById('top-scrollbar');
    const topContent = document.getElementById('top-scrollbar-content');
    const bottomBar = document.getElementById('tbl-scroll-container');
    if (!topBar || !topContent || !bottomBar) return;

    // 让顶部滚动条内容与表格等宽
    const syncWidth = () => {
      topContent.style.width = bottomBar.scrollWidth + 'px';
    };
    syncWidth();

    let isSyncing = false;
    const onTopScroll = () => {
      if (isSyncing) return;
      isSyncing = true;
      bottomBar.scrollLeft = topBar.scrollLeft;
      isSyncing = false;
    };
    const onBottomScroll = () => {
      if (isSyncing) return;
      isSyncing = true;
      topBar.scrollLeft = bottomBar.scrollLeft;
      isSyncing = false;
    };

    topBar.addEventListener('scroll', onTopScroll);
    bottomBar.addEventListener('scroll', onBottomScroll);
    window.addEventListener('resize', syncWidth);

    return () => {
      topBar.removeEventListener('scroll', onTopScroll);
      bottomBar.removeEventListener('scroll', onBottomScroll);
      window.removeEventListener('resize', syncWidth);
    };
  }, [allRows, page]);  // 结果变化时重新绑定

  const hasResults = allRows.length > 0;

  // 客户端分页：从全量数据中切片当前页
  const displayRows = allRows.slice((page - 1) * pageSize, page * pageSize);

  // Arrow 下载：调用 /execute-sql-arrow 获取二进制并下载
  const handleArrowDownload = async () => {
    const sql = sqlEditor.trim();
    if (!sql) return;
    try {
      const payload = {
        sql,
        db_path: dbPath || undefined,
        batch_id: batchId || undefined,
        query_mode: queryMode || undefined,
        result_limit: getResultLimit(),
      };
      const res = await authFetch(`${API_BASE}/api/agent/execute-sql-arrow`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.headers.get('content-type')?.includes('application/vnd.apache.arrow.stream')) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `query_result_${Date.now()}.arrow`;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        // 降级为 JSON 下载
        const data = await res.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `query_result_${Date.now()}.json`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (e) {
      setError('Arrow 下载失败: ' + e.message);
    }
  };
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
            style={{ flex: 1, padding: '10px', fontSize: 14, borderRadius: 4, border: '1px solid #ccc' }}
          />
          {dbPath.trim() && (
            <button
              onClick={() => setDbPath('')}
              style={{ padding: '10px 16px', fontSize: 13, borderRadius: 4, border: '1px solid #d9d9d9', background: '#fff', cursor: 'pointer', whiteSpace: 'nowrap' }}
            >
              清空路径
            </button>
          )}
        </div>

        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <input
            type="text"
            inputMode="numeric"
            value={resultLimitUnlimited ? '' : resultLimitInput}
            disabled={resultLimitUnlimited}
            onChange={(e) => {
              const val = e.target.value;
              if (val === '' || /^\d+$/.test(val)) {
                setResultLimitInput(val);
              }
            }}
            onBlur={(e) => {
              const val = e.target.value.trim();
              const n = parseInt(val, 10);
              if (!val || !Number.isFinite(n) || n <= 0) {
                setResultLimitInput('100');
              } else {
                setResultLimitInput(String(n));
              }
            }}
            placeholder="结果行数限制"
            title="单条 SQL 返回的最大行数（聚焦时允许清空以便输入）"
            style={{ width: 110, padding: '10px', fontSize: 14, borderRadius: 4, border: '1px solid #ccc' }}
          />
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#666', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={resultLimitUnlimited}
              onChange={(e) => setResultLimitUnlimited(e.target.checked)}
              style={{ cursor: 'pointer' }}
            />
            不限制结果数量
          </label>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <label style={{ fontSize: 13, color: '#666', whiteSpace: 'nowrap' }}>每页显示:</label>
            <input
              type="number"
              value={pageSize}
              min={5}
              max={500}
              onChange={(e) => {
                const v = parseInt(e.target.value, 10);
                if (v >= 5 && v <= 500) { setPageSize(v); setPage(1); }
              }}
              title="每页显示行数 (5-500)"
              style={{ width: 70, padding: '10px', fontSize: 14, borderRadius: 4, border: '1px solid #ccc' }}
            />
          </div>
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

      {/* 流式 SQL 生成实时显示 */}
      {streamingSql && (
        <div style={{ marginTop: 12, padding: 10, background: '#f6ffed', border: '1px solid #b7eb8f', borderRadius: 4 }}>
          <div style={{ fontSize: 12, color: '#52c41a', fontWeight: 600, marginBottom: 6 }}>✨ SQL 生成中...</div>
          <pre style={{ margin: 0, fontSize: 13, fontFamily: 'Consolas, Monaco, monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all', color: '#333' }}>{streamingSql}</pre>
        </div>
      )}

      {error && (
        <div style={{ marginTop: 12, padding: 10, background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 4, color: '#cf1322' }}>
          {error}
        </div>
      )}

      {/* SQL 编辑器 */}
      <div style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <span style={{ fontSize: 12, color: '#888' }}>SQL 编辑器</span>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={() => { setStrategyListOpen(!strategyListOpen); loadStrategyList(); }}
              style={{ padding: '2px 8px', fontSize: 11, borderRadius: 3, border: '1px solid #1890ff', background: 'transparent', color: '#1890ff', cursor: 'pointer' }}
            >
              我的策略 ({strategyList.length})
            </button>
            <button
              onClick={() => {
                // 预填关键词：从 SQL 提取 tag_name 作为默认关键词
                const m = sqlEditor.match(/(?:AS\s+tag_name|tag_name\s*=\s*)['"]([^'"]+)['"]/i)
                  || sqlEditor.match(/['"]([A-Z][A-Za-z_]+)['"]\s+AS\s+tag_name/i);
                const kw = m ? m[1] : '';
                setStrategyForm(prev => ({ ...prev, keywords: prev.keywords || kw }));
                setSaveStrategyModalOpen(true);
              }}
              disabled={!sqlEditor.trim()}
              style={{ padding: '2px 8px', fontSize: 11, borderRadius: 3, border: '1px solid #52c41a', background: sqlEditor.trim() ? 'transparent' : '#f5f5f5', color: sqlEditor.trim() ? '#52c41a' : '#ccc', cursor: sqlEditor.trim() ? 'pointer' : 'not-allowed' }}
            >
              保存为策略
            </button>
          </div>
        </div>
        <SqlEditor
          value={sqlEditor}
          onChange={setSqlEditor}
          onExecute={handleExecuteSql}
          disabled={loading}
          placeholder="在此输入或编辑 SQL，也可以由 LLM 生成后填入...&#10;&#10;例如：SELECT bag_id, tag_name, start_ts, end_ts FROM range_tag WHERE tag_name = 'intersection_left_turn' LIMIT 100;"
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
                Results ({totalRows} rows{(!resultLimitUnlimited && totalRows >= getResultLimit()) ? ' — may be truncated, increase result_limit' : ''}, showing page {page})
                {result?.scanned_dbs > 0 && (
                  <span style={{ marginLeft: 12, color: '#999' }}>
                    扫描 {result.scanned_dbs} 个 DB，命中 {result.matched_dbs} 个
                  </span>
                )}
                <button
                  onClick={handleArrowDownload}
                  style={{ marginLeft: 12, padding: '2px 8px', fontSize: 11, borderRadius: 3, border: '1px solid #52c41a', background: 'transparent', color: '#52c41a', cursor: 'pointer' }}
                  title="下载 Arrow IPC 二进制文件（高效传输，pyarrow 不可用时自动降级为 JSON）"
                >
                  ⬇ Arrow 下载
                </button>
              </div>
              {/* 顶部滚动条：与底部同步 */}
              <div id="top-scrollbar" style={{ overflowX: 'auto', height: 17, borderBottom: '1px solid #e8e8e8' }}>
                <div id="top-scrollbar-content" style={{ height: 1 }}></div>
              </div>
              <div style={{ overflowX: 'auto' }} id="tbl-scroll-container">
                <table style={{ width: '100%', borderCollapse: 'separate', borderSpacing: 0, fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: '#fafafa' }}>
                      {columns.map((col) => (
                        <th key={col} style={{ border: '1px solid #e8e8e8', padding: '8px 12px', textAlign: 'left', fontWeight: 600 }}>
                          {col}
                        </th>
                      ))}
                      <th style={{
                        border: '1px solid #e8e8e8', padding: '8px 12px', textAlign: 'left', fontWeight: 600,
                        position: 'sticky', right: 0, background: '#fafafa', zIndex: 2,
                        boxShadow: '-2px 0 4px rgba(0,0,0,0.05)',
                      }}>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {displayRows.map((row, idx) => {
                      // 用行内容的唯一标识做去重key，保证翻页后也能识别
                      const rowKey = `${row.bag_path || ''}|${row.topic || ''}|${row.start_ts || ''}|${row.tag_name || ''}`;
                      const isVisualized = visualizedRows.has(rowKey);
                      return (
                      <tr key={idx} style={{
                        background: isVisualized ? '#e6f7ff' : (idx % 2 === 0 ? '#fff' : '#fafafa'),
                        borderLeft: isVisualized ? '3px solid #1890ff' : 'none',
                      }}>
                        {columns.map((col) => (
                          <td key={col} style={{ border: '1px solid #e8e8e8', padding: '8px 12px' }}>
                            {typeof row[col] === 'object' ? JSON.stringify(row[col]) : String(row[col] ?? '')}
                          </td>
                        ))}
                        <td style={{
                          border: '1px solid #e8e8e8', padding: '8px 12px',
                          position: 'sticky', right: 0, background: isVisualized ? '#e6f7ff' : (idx % 2 === 0 ? '#fff' : '#fafafa'), zIndex: 1,
                          boxShadow: '-2px 0 4px rgba(0,0,0,0.05)',
                        }}>
                          <button
                            onClick={() => {
                              // 标记该行已可视化
                              setVisualizedRows(prev => new Set(prev).add(rowKey));
                              startVisualization(row);
                            }}
                            disabled={topicModalOpen || playerModalOpen}
                            title={topicModalOpen || playerModalOpen ? '请先关闭当前弹窗' : '播包可视化'}
                            style={{
                              padding: '4px 10px', fontSize: 12, borderRadius: 4, border: 'none',
                              background: (topicModalOpen || playerModalOpen) ? '#ccc' : '#1890ff',
                              color: '#fff', cursor: (topicModalOpen || playerModalOpen) ? 'not-allowed' : 'pointer',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            📹 播包可视化
                          </button>
                        </td>
                      </tr>
                    );
                    })}
                  </tbody>
                </table>
              </div>
              {/* Pagination controls — 纯前端分页，不发请求 */}
              {totalRows > 0 && (
                <PaginationControls
                  page={page}
                  pageSize={pageSize}
                  totalRows={totalRows}
                  onPageChange={(p) => setPage(p)}
                />
              )}
            </div>
          )}

          {allRows.length === 0 && result && !error && (
            <div style={{ color: '#666', fontSize: 14 }}>No rows returned.</div>
          )}
        </div>
      )}

      {/* 视频提取进度弹窗（替代底部堆积面板） */}
      {extractModalOpen && videoRows.length > 0 && (() => {
        const v = videoRows[videoRows.length - 1];  // 显示最新一条的状态
        const isFailed = v.status === 'failed';
        return (
          <div
            style={{
              position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
              background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            }}
          >
            <div style={{ background: '#fff', borderRadius: 8, padding: 24, minWidth: 360, maxWidth: 450, textAlign: 'center' }}>
              <h3 style={{ margin: '0 0 16px 0', fontSize: 16 }}>
                {isFailed ? '❌ 视频提取失败' : '📹 视频提取中'}
              </h3>
              <div style={{ fontSize: 13, color: '#555', marginBottom: 8 }}>
                <b>Bag:</b> {v.row.bag_id || v.row.db_file} &nbsp;|&nbsp;
                <b>Topic:</b> {v.topic}
              </div>
              {!isFailed && (
                <div style={{ margin: '16px 0' }}>
                  <div style={{ fontSize: 14, color: '#1890ff', marginBottom: 8 }}>
                    {v.status === 'pending' && '⏳ 排队中...'}
                    {v.status === 'processing' && `⏳ 提取中... ${v.progress.toFixed(1)}%`}
                    {v.status === 'completed' && '✅ 提取完成，即将播放...'}
                  </div>
                  {v.status === 'processing' && (
                    <div style={{ width: '100%', height: 8, background: '#f0f0f0', borderRadius: 4, overflow: 'hidden' }}>
                      <div style={{ width: `${v.progress}%`, height: '100%', background: '#1890ff', borderRadius: 4, transition: 'width 0.3s' }} />
                    </div>
                  )}
                </div>
              )}
              {isFailed && (
                <div style={{ fontSize: 13, color: '#cf1322', margin: '12px 0', padding: 10, background: '#fff2f0', borderRadius: 4 }}>
                  {v.message}
                </div>
              )}
              <button
                onClick={() => { setExtractModalOpen(false); if (isFailed) setVideoRows([]); }}
                style={{
                  marginTop: 12, padding: '8px 24px', fontSize: 13, borderRadius: 4,
                  border: '1px solid #d9d9d9', background: '#fff', cursor: 'pointer',
                }}
              >
                {isFailed ? '关闭' : '取消提取'}
              </button>
            </div>
          </div>
        );
      })()}

      {/* Topic 选择弹窗 */}
      {topicModalOpen && topicModalData && (
        <div
          style={{
            position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
            background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
          }}
          onClick={(e) => { if (e.target === e.currentTarget) { setTopicModalOpen(false); setTopicModalData(null); } }}
        >
          <div style={{ background: '#fff', borderRadius: 8, padding: 24, minWidth: 400, maxWidth: 500 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h3 style={{ margin: 0, fontSize: 16 }}>📹 播包可视化</h3>
              <button
                onClick={() => { setTopicModalOpen(false); setTopicModalData(null); }}
                style={{ padding: '4px 10px', fontSize: 16, borderRadius: 4, border: '1px solid #d9d9d9', background: '#fff', cursor: 'pointer', lineHeight: 1 }}
                title="关闭"
              >✕</button>
            </div>
            <div style={{ fontSize: 13, color: '#555', marginBottom: 12 }}>
              <b>Bag:</b> {topicModalData.bagPath}<br/>
              <b>时间范围:</b> {topicModalData.row.start_ts} ~ {topicModalData.row.end_ts} (秒)
            </div>
            {topicModalData.loading ? (
              <div style={{ padding: '20px 0', textAlign: 'center' }}>
                <div style={{ fontSize: 14, color: '#1890ff', marginBottom: 12 }}>{topicModalData.loadingMsg || '加载中...'}</div>
                <div style={{ width: '100%', height: 6, background: '#f0f0f0', borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{ width: '100%', height: '100%', background: '#1890ff', borderRadius: 3, animation: 'progress-pulse 1.5s ease-in-out infinite' }} />
                </div>
                <style>{`@keyframes progress-pulse { 0%, 100% { opacity: 0.4; width: 30%; } 50% { opacity: 1; width: 70%; } }`}</style>
              </div>
            ) : (
              topicModalData.cameraTopics.length > 0 ? (
                <div style={{ marginBottom: 16 }}>
                  <label style={{ fontSize: 13, color: '#555', fontWeight: 500, display: 'block', marginBottom: 6 }}>选择 Camera Topic:</label>
                  <select
                    value={selectedTopic}
                    onChange={(e) => setSelectedTopic(e.target.value)}
                    style={{ width: '100%', padding: '8px 12px', fontSize: 13, borderRadius: 4, border: '1px solid #ccc' }}
                  >
                    {topicModalData.cameraTopics.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
              ) : (
                <div style={{ marginBottom: 16 }}>
                  <label style={{ fontSize: 13, color: '#555', fontWeight: 500, display: 'block', marginBottom: 6 }}>输入 Camera Topic:</label>
                  <input
                    type="text"
                    value={selectedTopic}
                    onChange={(e) => setSelectedTopic(e.target.value)}
                    placeholder="/camera/front_center"
                    style={{ width: '100%', padding: '8px 12px', fontSize: 13, borderRadius: 4, border: '1px solid #ccc' }}
                  />
                </div>
              )
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                <input
                  type="checkbox"
                  id="forceH264"
                  checked={forceH264}
                  onChange={(e) => setForceH264(e.target.checked)}
                  style={{ cursor: 'pointer' }}
                />
                <label htmlFor="forceH264" style={{ fontSize: 13, color: '#555', cursor: 'pointer' }}>
                  ⚙️ 强制使用 H.264 转码（兼容性更好，用于调试）
                </label>
              </div>
              <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
                <button
                  onClick={() => { setTopicModalOpen(false); setTopicModalData(null); }}
                  style={{ padding: '8px 16px', fontSize: 13, borderRadius: 4, border: '1px solid #d9d9d9', background: '#fff', cursor: 'pointer' }}
                >
                  取消
                </button>
                <button
                  onClick={handleExtractVideo}
                  disabled={!selectedTopic || topicModalData.loading}
                  style={{
                    padding: '8px 16px', fontSize: 13, borderRadius: 4, border: 'none',
                    background: (!selectedTopic || topicModalData.loading) ? '#ccc' : '#1890ff', color: '#fff', cursor: (!selectedTopic || topicModalData.loading) ? 'not-allowed' : 'pointer',
                  }}
                >
                  确认提取
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* SQL 执行进度弹窗 */}
      {sqlExecModalOpen && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }}>
          <div style={{ background: '#fff', borderRadius: 8, padding: 28, minWidth: 360, maxWidth: 480, textAlign: 'center' }}>
            <h3 style={{ margin: '0 0 16px 0', fontSize: 16 }}>⏳ 正在执行 SQL</h3>
            <div style={{ fontSize: 14, color: '#555', marginBottom: 12 }}>
              已耗时 <b>{sqlExecElapsed}</b> 秒
            </div>
            <div style={{ fontSize: 13, color: '#666', marginBottom: 16, minHeight: 60, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              {sqlExecStatus === 'pending' && <span>已发送请求，等待后端响应...</span>}
              {sqlExecStatus === 'slow' && <span style={{ color: '#d48806' }}>后端响应较慢，可能正在遍历大量 DB，请耐心等待</span>}
              {sqlExecStatus === 'stuck' && (
                <span style={{ color: '#cf1322' }}>
                  ⚠️ 超过 15 秒未收到后端响应，请求很可能已卡住。<br/>
                  建议点击「取消执行」后检查后端日志。
                </span>
              )}
              {sqlExecStatus === 'loading_body' && <span style={{ color: '#52c41a' }}>后端已开始返回结果，正在接收数据...</span>}
              {sqlExecStatus === 'error' && <span style={{ color: '#cf1322' }}>请求出错，关闭弹窗后查看错误信息</span>}
            </div>
            <div style={{ width: '100%', height: 8, background: '#f0f0f0', borderRadius: 4, overflow: 'hidden', marginBottom: 16 }}>
              <div style={{
                width: `${Math.min(100, (sqlExecElapsed / 10) * 100)}%`,
                height: '100%',
                background: sqlExecStatus === 'stuck' ? '#cf1322' : (sqlExecStatus === 'slow' ? '#faad14' : (sqlExecStatus === 'loading_body' ? '#52c41a' : '#1890ff')),
                borderRadius: 4,
                transition: 'width 1s linear',
              }} />
            </div>
            <button
              onClick={() => {
                if (abortControllerRef.current) abortControllerRef.current.abort();
                setSqlExecModalOpen(false);
                if (sqlExecTimerRef.current) clearInterval(sqlExecTimerRef.current);
                if (sqlExecSlowTimerRef.current) clearTimeout(sqlExecSlowTimerRef.current);
                if (sqlExecStuckTimerRef.current) clearTimeout(sqlExecStuckTimerRef.current);
                sqlExecTimerRef.current = null;
                sqlExecSlowTimerRef.current = null;
                sqlExecStuckTimerRef.current = null;
                setLoading(false);
              }}
              style={{ marginTop: 16, padding: '8px 24px', fontSize: 13, borderRadius: 4, border: '1px solid #d9d9d9', background: '#fff', cursor: 'pointer' }}
            >
              取消执行
            </button>
          </div>
        </div>
      )}

      {/* 视频播放弹窗 */}
      {playerModalOpen && playerData && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1001,
        }}>
          <div style={{ background: '#000', borderRadius: 8, padding: 16, maxWidth: '90vw', maxHeight: '90vh', display: 'flex', flexDirection: 'column' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ color: '#fff', fontSize: 14 }}>
                📹 {playerData.row.bag_id || '未知'} | {playerData.topic}
                {playerMode && (
                  <span style={{
                    marginLeft: 12,
                    padding: '2px 10px',
                    borderRadius: 4,
                    fontSize: 12,
                    background: playerMode === 'hevc-stream' ? '#52c41a' : '#fa8c16',
                    color: '#fff',
                  }}>
                    {playerMode === 'hevc-stream' ? 'HEVC 直传' : 'H.264 转码'}
                  </span>
                )}
              </span>
              <button
                onClick={() => { setPlayerModalOpen(false); setPlayerData(null); setVideoRows([]); setPlayerError(null); setExtractModalOpen(false); pollCancelledRef.current = true; if (intervalRef.current) { clearInterval(intervalRef.current); intervalRef.current = null; } }}
                style={{ padding: '4px 12px', fontSize: 13, borderRadius: 4, border: '1px solid #555', background: 'transparent', color: '#fff', cursor: 'pointer' }}
              >
                ✕ 关闭
              </button>
            </div>

            {playerError && (
              <div style={{
                background: '#fff2f0',
                border: '1px solid #ffccc7',
                color: '#cf1322',
                padding: 12,
                borderRadius: 4,
                marginBottom: 10,
                fontSize: 13,
              }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>⚠️ 播放失败</div>
                <div>{playerError}</div>
                <button
                  onClick={() => {
                    setPlayerModalOpen(false);
                    setPlayerError(null);
                    setForceH264(true);
                    setTimeout(() => handleExtractVideo(), 100);
                  }}
                  style={{
                    marginTop: 8,
                    padding: '5px 14px',
                    fontSize: 12,
                    borderRadius: 4,
                    border: '1px solid #cf1322',
                    background: '#fff',
                    color: '#cf1322',
                    cursor: 'pointer',
                  }}
                >
                  🔄 改用 H.264 转码重试
                </button>
              </div>
            )}

            {playerData.video_url ? (
              <video
                src={playerData.video_url}
                controls
                autoPlay
                style={{ maxWidth: '85vw', maxHeight: '80vh', borderRadius: 4 }}
              />
            ) : (
              <video
                ref={videoRef}
                controls
                autoPlay
                style={{ maxWidth: '85vw', maxHeight: '80vh', borderRadius: 4 }}
              />
            )}
          </div>
        </div>
      )}

      {/* ── 保存策略弹窗 ── */}
      {saveStrategyModalOpen && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.3)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ background: '#fff', borderRadius: 8, padding: 24, minWidth: 400, boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}>
            <h3 style={{ margin: '0 0 16px' }}>保存为策略</h3>
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: '#666' }}>策略名</label>
              <input value={strategyForm.name} onChange={e => setStrategyForm(p => ({...p, name: e.target.value}))} placeholder="如: high_speed_cutin" style={{ width: '100%', padding: 6, marginTop: 4, border: '1px solid #d9d9d9', borderRadius: 4 }} />
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: '#666' }}>触发关键词（逗号分隔，用户输入含关键词时自动匹配此策略）</label>
              <input value={strategyForm.keywords} onChange={e => setStrategyForm(p => ({...p, keywords: e.target.value}))} placeholder="如: 高速切入,高速变道" style={{ width: '100%', padding: 6, marginTop: 4, border: '1px solid #d9d9d9', borderRadius: 4 }} />
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: '#666' }}>tag_name（可选，留空自动从SQL提取）</label>
              <input value={strategyForm.tag_name} onChange={e => setStrategyForm(p => ({...p, tag_name: e.target.value}))} placeholder="如: high_speed_cutin" style={{ width: '100%', padding: 6, marginTop: 4, border: '1px solid #d9d9d9', borderRadius: 4 }} />
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12, color: '#666' }}>备注</label>
              <input value={strategyForm.description} onChange={e => setStrategyForm(p => ({...p, description: e.target.value}))} placeholder="策略说明" style={{ width: '100%', padding: 6, marginTop: 4, border: '1px solid #d9d9d9', borderRadius: 4 }} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button onClick={() => setSaveStrategyModalOpen(false)} style={{ padding: '6px 16px', border: '1px solid #d9d9d9', borderRadius: 4, background: '#fff', cursor: 'pointer' }}>取消</button>
              <button onClick={handleSaveStrategy} style={{ padding: '6px 16px', border: 'none', borderRadius: 4, background: '#52c41a', color: '#fff', cursor: 'pointer' }}>保存</button>
            </div>
          </div>
        </div>
      )}

      {/* ── 策略列表面板 ── */}
      {strategyListOpen && (
        <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(0,0,0,0.3)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ background: '#fff', borderRadius: 8, padding: 24, minWidth: 500, maxHeight: '80vh', overflow: 'auto', boxShadow: '0 4px 12px rgba(0,0,0,0.15)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <h3 style={{ margin: 0 }}>我的策略</h3>
              <button onClick={() => setStrategyListOpen(false)} style={{ border: 'none', background: 'none', fontSize: 18, cursor: 'pointer', color: '#999' }}>✕</button>
            </div>
            {strategyList.length === 0 ? (
              <div style={{ color: '#999', textAlign: 'center', padding: 20 }}>暂无自定义策略</div>
            ) : (
              <div>
                {strategyList.map(s => (
                  <div key={s.name} style={{ padding: 12, borderBottom: '1px solid #f0f0f0' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div>
                        <strong style={{ fontSize: 14 }}>{s.name}</strong>
                        <span style={{ marginLeft: 8, fontSize: 11, color: '#999' }}>
                          关键词: {s.keywords.join(', ')}
                        </span>
                      </div>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button onClick={() => handleLoadStrategy(s)} style={{ padding: '2px 8px', fontSize: 11, border: '1px solid #1890ff', borderRadius: 3, background: 'transparent', color: '#1890ff', cursor: 'pointer' }}>加载</button>
                        <button onClick={() => handleDeleteStrategy(s.name)} style={{ padding: '2px 8px', fontSize: 11, border: '1px solid #ff4d4f', borderRadius: 3, background: 'transparent', color: '#ff4d4f', cursor: 'pointer' }}>删除</button>
                      </div>
                    </div>
                    <div style={{ fontSize: 11, color: '#666', marginTop: 4 }}>{s.description || '无备注'}</div>
                    <pre style={{ fontSize: 10, color: '#888', marginTop: 4, maxHeight: 60, overflow: 'auto', background: '#fafafa', padding: 4, borderRadius: 3 }}>{s.sql.substring(0, 200)}{s.sql.length > 200 ? '...' : ''}</pre>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
