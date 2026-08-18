import React, { useState, useEffect, useRef } from 'react';
import SqlEditor from './SqlEditor';
import { API_BASE, authFetch, addTokenParam } from '../api';
import { useToast } from '../toast';
import { colors, card, cardTitle, banner, btn } from '../theme';
import QueryBar from './agent/QueryBar';
import ResultTable from './agent/ResultTable';
import HistoryPanel, { saveHistoryEntry } from './agent/HistoryPanel';
import TopicModal from './agent/TopicModal';
import PlayerModal from './agent/PlayerModal';
import StrategyModals from './agent/StrategyModals';
import { SqlExecModal, ExtractProgressModal } from './agent/ProgressModals';
import { useStrategies } from './agent/useStrategies';

// ============================================
// AgentPanel — NL2SQL 查询面板（编排器）
//
// 架构（2026-08 重构后）：
//   本文件只持有 state 和跨组件的 handler，JSX 全部委托给子组件：
//   - agent/QueryBar.jsx        查询控制区（模式/批次/输入/提交）
//   - agent/ResultTable.jsx     结果表格 + 双滚动条 + 分页
//   - agent/HistoryPanel.jsx    历史查询（localStorage）
//   - agent/TopicModal.jsx      播包可视化的 topic 选择弹窗
//   - agent/PlayerModal.jsx     视频/BEV/宫格播放弹窗（内含 useMseStream）
//   - agent/StrategyModals.jsx  策略保存/列表/评测集/验证集弹窗
//   - agent/ProgressModals.jsx  SQL 执行进度 / 视频提取进度弹窗
//   - agent/useStrategies.js    策略与评测标注的全部状态与 API 逻辑
// ============================================
export default function AgentPanel() {
  const toast = useToast();

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

  // Video extraction states
  const [videoRows, setVideoRows] = useState([]);
  const intervalRef = useRef(null);
  const pollCancelledRef = useRef(false);
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
  const [playerMode, setPlayerMode] = useState(null); // 'hevc-stream' | 'h264-file' | 'h264-stream'
  const [playerError, setPlayerError] = useState(null);
  const [forceH264, setForceH264] = useState(false);
  const [playerGridMode, setPlayerGridMode] = useState(false);       // 宫格模式（多topic同时播放）
  const [playerGridTopics, setPlayerGridTopics] = useState([]);     // 宫格模式选中的topics

  // ── 策略与评测标注（状态+handler 全部在 hook 里） ──
  const strategies = useStrategies({
    getSqlEditor: () => sqlEditor,
    setSqlEditor,
    playerData: playerModalOpen ? playerData : null,
  });

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 切换 queryMode 时自动切换到对应模式可用的 batch
  useEffect(() => {
    if (batches.length === 0 || !batchId) return;
    const current = batches.find((b) => b.batch_id === batchId);
    if (queryMode === 'parquet' && current && !current.has_parquet) {
      const firstParquet = batches.find((b) => b.has_parquet);
      if (firstParquet) setBatchId(firstParquet.batch_id);
    }
  }, [queryMode, batches]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // ── 新查询的公共重置 ──
  const resetQueryState = () => {
    setLoading(true);
    setError('');
    setResult(null);
    clearProgress();
    setVideoRows([]);
    setPage(1);
  };

  // 新建 AbortController（取消旧请求）
  const newQueryController = () => {
    if (abortControllerRef.current) abortControllerRef.current.abort();
    const controller = new AbortController();
    abortControllerRef.current = controller;
    return controller;
  };

  // ── P0：取消进行中的查询 ──
  const handleCancelQuery = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setLoading(false);
    setStreamingSql('');
    clearProgress();
    toast.info('已取消查询');
  };

  // 查询成功后写入历史记录
  const recordHistory = (sql, rows) => {
    saveHistoryEntry({
      question: question.trim(),
      sql,
      queryMode,
      batchId: dbPath.trim() ? dbPath.trim() : batchId,
      rowCount: (rows || []).length,
    });
  };

  // 历史回填
  const handleRestoreHistory = (entry) => {
    if (entry.question) setQuestion(entry.question);
    setSqlEditor(entry.sql);
    toast.info('已回填历史查询，可编辑后重新执行');
  };

  // preview 模式：仅生成 SQL 填入编辑器（handleSubmit / handleSubmitStream 共用）
  const runGenerateSqlOnly = async (controller) => {
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
        recordHistory(data.sql, []);
        if (data.validation_error) {
          setError('SQL 校验警告: ' + data.validation_error);
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  // 执行 SQL 编辑器中的 SQL（取回全量数据，翻页由前端完成）
  const handleExecuteSql = async () => {
    const sql = sqlEditor.trim();
    if (!sql) return;
    const controller = newQueryController();

    setLoading(true);
    setError('');
    setSqlExecModalOpen(true);
    setSqlExecElapsed(0);
    setSqlExecStatus('pending');
    if (sqlExecTimerRef.current) clearInterval(sqlExecTimerRef.current);
    if (sqlExecSlowTimerRef.current) clearTimeout(sqlExecSlowTimerRef.current);
    if (sqlExecStuckTimerRef.current) clearTimeout(sqlExecStuckTimerRef.current);
    sqlExecTimerRef.current = setInterval(() => setSqlExecElapsed((prev) => prev + 1), 1000);
    sqlExecSlowTimerRef.current = setTimeout(() => setSqlExecStatus((s) => (s === 'pending' ? 'slow' : s)), 5000);
    sqlExecStuckTimerRef.current = setTimeout(() => setSqlExecStatus((s) => (s === 'pending' || s === 'slow' ? 'stuck' : s)), 15000);

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
      recordHistory(sql, data.rows);
      if (data.error && !data.rows?.length) {
        setError(data.error);
      }
    } catch (e) {
      if (e.name !== 'AbortError') {
        setSqlExecStatus('error');
        setError(e.message);
      }
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

  // 取消 SQL 执行弹窗中的请求
  const handleCancelSqlExec = () => {
    if (abortControllerRef.current) abortControllerRef.current.abort();
    setSqlExecModalOpen(false);
    if (sqlExecTimerRef.current) clearInterval(sqlExecTimerRef.current);
    if (sqlExecSlowTimerRef.current) clearTimeout(sqlExecSlowTimerRef.current);
    if (sqlExecStuckTimerRef.current) clearTimeout(sqlExecStuckTimerRef.current);
    sqlExecTimerRef.current = null;
    sqlExecSlowTimerRef.current = null;
    sqlExecStuckTimerRef.current = null;
    setLoading(false);
  };

  // LLM 查询（auto 模式：直接执行，preview 模式：仅填入编辑器）
  const handleSubmit = async () => {
    if (!question.trim()) return;
    const controller = newQueryController();
    resetQueryState();

    if (sqlEditMode === 'preview') {
      await runGenerateSqlOnly(controller);
      return;
    }

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
      setVisualizedRows(new Set());
      recordHistory(data.sql, data.rows);
      if (data.error && !data.rows?.length) {
        setError(data.error);
      }
    } catch (e) {
      if (e.name !== 'AbortError') setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmitStream = async () => {
    if (!question.trim()) return;
    const controller = newQueryController();
    resetQueryState();

    if (sqlEditMode === 'preview') {
      // Stream 模式下 preview 也走 generate-sql
      await runGenerateSqlOnly(controller);
      return;
    }

    try {
      const response = await authFetch(`${API_BASE}/api/agent/query-stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload()),
        signal: controller.signal,
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${response.status}`);
      }

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
                setVisualizedRows(new Set());
                recordHistory(data.sql, data.rows);
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
      if (e.name !== 'AbortError') setError('Stream failed: ' + e.message);
    } finally {
      setLoading(false);
    }
  };

  const startVisualization = async (row) => {
    // 同步执行旧的 MSE cleanup，确保 TCP 连接立即释放
    if (mseCleanupRef.current) {
      try { mseCleanupRef.current(); } catch (e) { console.error('[MSE] cleanup error:', e); }
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
    setPlayerGridMode(false);
    setPlayerGridTopics([]);

    // 给浏览器/后端一点时间彻底释放旧 video stream 的 TCP 连接和 ffmpeg 进程
    await new Promise((resolve) => setTimeout(resolve, 800));

    // 如果 bag_path 为空，尝试解析
    let bagPath = row.bag_path;
    let emBinPath = '';  // em bin 本地路径（3D BEV 视图用）
    if (!bagPath) {
      if (row.bag_id) {
        try {
          const resolveRes = await authFetch(`${API_BASE}/api/agent/resolve-bag-path?bag_id=${encodeURIComponent(row.bag_id)}`);
          if (resolveRes.ok) {
            const resolveData = await resolveRes.json();
            if (resolveData.bag_path) bagPath = resolveData.bag_path;
            // 3D BEV 视图使用 em bin 路径（fusion_map_plus.bin 在 em bin 目录下）
            if (resolveData.em_bin_local_path) emBinPath = resolveData.em_bin_local_path;
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
    setTopicModalData({ bagPath, emBinPath, row, cameraTopics: [], startTs: null, endTs: null, clampedMsg: '', loading: true, loadingMsg: '正在加载 bag 信息...' });
    setSelectedTopic(localStorage.getItem('lastSelectedTopic') || '');
    setTopicModalOpen(true);

    // 使用 SSE 流式获取 bag info（带进度反馈）
    const controller = new AbortController();
    bagAbortControllerRef.current = controller;
    let bagStartNs = null;
    let bagEndNs = null;
    let clampedMsg = '';
    let cameraTopics = [];
    let fusionMapTopic = null;

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
                setTopicModalData((prev) => prev ? { ...prev, loading: true, loadingMsg: data.message } : prev);
              } else if (data.stage === 'completed' && data.bag_info) {
                const info = data.bag_info;
                bagStartNs = info.start_time_ns;
                bagEndNs = info.end_time_ns;
                cameraTopics = (info.topics || []).map((t) => t.name).filter(Boolean);
                fusionMapTopic = info.fusion_map_topic || null;
                // 统一：fusion_map_plus 加入 topic 列表，过滤 bev_obstacle_raw
                if (fusionMapTopic && !cameraTopics.includes(fusionMapTopic.name)) cameraTopics.push(fusionMapTopic.name);
                cameraTopics = cameraTopics.filter((t) => !t.includes('bev_obstacle_raw'));
              } else if (data.stage === 'error') {
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
          fusionMapTopic = bagInfo.fusion_map_topic || null;
          if (fusionMapTopic && !cameraTopics.includes(fusionMapTopic.name)) cameraTopics.push(fusionMapTopic.name);
          cameraTopics = cameraTopics.filter((t) => !t.includes('bev_obstacle_raw'));
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

    setTopicModalData({ bagPath, emBinPath, row, cameraTopics, fusionMapTopic, startTs, endTs, clampedMsg, loading: false, loadingMsg: '' });
    // 优先使用上次记忆的 topic，如果它在当前可用 topic 列表中；否则取第一个
    const lastTopic = localStorage.getItem('lastSelectedTopic') || '';
    const defaultTopic = (lastTopic && cameraTopics.includes(lastTopic)) ? lastTopic
      : (cameraTopics.length > 0 ? cameraTopics[0] : '');
    setSelectedTopic(defaultTopic);
  };

  // ── 多视图 Tab：切换 camera/BEV topic ──
  const handleSwitchTopic = (newTopic) => {
    if (!playerData?._multiViewMeta || newTopic === playerData.topic) return;
    const { bagPath, startTs, endTs } = playerData._multiViewMeta;

    // ── 切换到 BEV topic → 更新 playerData，渲染 BevViewer ──
    if (newTopic.includes('fusion_map')) {
      if (streamAbortControllerRef.current) {
        streamAbortControllerRef.current.abort();
        streamAbortControllerRef.current = null;
      }
      setPlayerData((prev) => ({
        ...prev,
        stream_url: null,
        topic: newTopic,
        use_mse: false,
        is_bev: true,
      }));
      setPlayerError(null);
      localStorage.setItem('lastSelectedTopic', newTopic);
      return;
    }

    const hevcMime = 'video/mp4; codecs="hvc1.1.6.L120.B0"';
    const h264Mime = 'video/mp4; codecs="avc1.64001f"';
    const buildStreamUrl = (endpoint) => {
      const params = new URLSearchParams({ bag_path: bagPath, topic: newTopic });
      if (startTs !== null) params.append('start_ts', String(startTs));
      if (endTs !== null) params.append('end_ts', String(endTs));
      return `${API_BASE}/api/video/${endpoint}?${params.toString()}`;
    };

    // ── 从 BEV 切换到视频 topic → 重建视频流 ──
    if (playerData.is_bev) {
      const supportsHevcMSE = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(hevcMime);
      const supportsH264MSE = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(h264Mime);
      const codec = supportsHevcMSE ? hevcMime : (supportsH264MSE ? h264Mime : h264Mime);
      const isHevc = codec === hevcMime;
      const endpoint = isHevc ? 'stream-hevc' : 'stream-h264';

      setPlayerData((prev) => ({
        ...prev,
        stream_url: buildStreamUrl(endpoint),
        topic: newTopic,
        use_mse: true,
        mse_codec: codec,
        is_bev: false,
        durationSec: (endTs !== null && startTs !== null) ? (endTs - startTs) / 1e9 : null,
      }));
      setPlayerError(null);
      localStorage.setItem('lastSelectedTopic', newTopic);
      return;
    }

    // ── 视频 topic 之间切换：保持当前编码模式，构建新 stream URL ──
    const codec = playerData.mse_codec || h264Mime;
    const isHevc = codec === hevcMime;
    const endpoint = isHevc ? 'stream-hevc' : 'stream-h264';

    setPlayerData((prev) => ({
      ...prev,
      stream_url: buildStreamUrl(endpoint),
      topic: newTopic,
    }));
    setPlayerError(null);
    localStorage.setItem('lastSelectedTopic', newTopic);
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
      toast.error('启动视频提取失败: ' + e.message);
    }
  };

  const handleExtractVideo = async () => {
    if (!topicModalData || !selectedTopic) return;

    const { bagPath, row, startTs, endTs, clampedMsg, cameraTopics } = topicModalData;

    // 多视图 Tab 所需的公共数据，会存入 playerData 供 Tab 切换时使用
    const _multiViewMeta = { bagPath, emBinPath: topicModalData.emBinPath, startTs, endTs, cameraTopics: cameraTopics || [] };

    // ── 如果选择了 BEV topic → 走统一的 playerModal，视频区域渲染 BevViewer ──
    if (selectedTopic.includes('fusion_map')) {
      setTopicModalOpen(false);
      setTopicModalData(null);
      setPlayerError(null);
      setPlayerMode(null);
      setPlayerData({
        stream_url: null,  // BEV 不需要 stream URL
        row,
        topic: selectedTopic,
        use_mse: false,    // BEV 不走 MSE
        is_bev: true,      // 标记为 BEV 模式
        durationSec: (endTs !== null && startTs !== null) ? (endTs - startTs) / 1e9 : null,
        _multiViewMeta,
      });
      setPlayerModalOpen(true);
      localStorage.setItem('lastSelectedTopic', selectedTopic);
      return;
    }

    // 记忆用户选择的 topic
    localStorage.setItem('lastSelectedTopic', selectedTopic);

    if (clampedMsg) {
      toast.info(`⏱️ 时间范围已自动调整：\n${clampedMsg}\n将按调整后的范围播放视频。`, 6000);
    }

    setPlayerError(null);
    setPlayerMode(null);

    // 检测浏览器是否支持 HEVC in MP4（MSE）
    const hevcMime = 'video/mp4; codecs="hvc1.1.6.L120.B0"';
    const h264Mime = 'video/mp4; codecs="avc1.64001f"';
    const supportsHevcMSE = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(hevcMime);
    const supportsH264MSE = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(h264Mime);

    const durationSec = (endTs !== null && startTs !== null) ? (endTs - startTs) / 1e9 : null;

    // 构建 stream URL 的公共参数（fetch 走 Authorization header，URL 不再拼 token）
    const buildStreamUrl = (endpoint) => {
      const params = new URLSearchParams({ bag_path: bagPath, topic: selectedTopic });
      if (startTs !== null) params.append('start_ts', String(startTs));
      if (endTs !== null) params.append('end_ts', String(endTs));
      return `${API_BASE}/api/video/${endpoint}?${params.toString()}`;
    };

    const openMsePlayer = (mode, codec, endpoint) => {
      setPlayerMode(mode);
      setPlayerData({
        stream_url: buildStreamUrl(endpoint),
        row,
        topic: selectedTopic,
        use_mse: true,
        mse_codec: codec,
        durationSec,
        _multiViewMeta,
      });
      setTopicModalOpen(false);
      setTopicModalData(null);
      setPlayerModalOpen(true);
    };

    // 强制 H.264 模式
    if (forceH264) {
      if (supportsH264MSE) {
        openMsePlayer('h264-stream', h264Mime, 'stream-h264');
      } else {
        setPlayerMode('h264-file');
        startH264Extraction(bagPath, row, startTs, endTs);
      }
      return;
    }

    if (supportsHevcMSE) {
      openMsePlayer('hevc-stream', hevcMime, 'stream-hevc');
      return;
    }

    // 浏览器不支持 HEVC MSE → 尝试 H.264 流式 MSE
    if (supportsH264MSE) {
      openMsePlayer('h264-stream', h264Mime, 'stream-h264');
      return;
    }

    // MSE 完全不支持，降级到全量转码+文件播放
    toast.warning('当前浏览器不支持 MSE 流式播放，将使用 H.264 全量转码（需等待转码完成）。', 6000);
    setPlayerMode('h264-file');
    startH264Extraction(bagPath, row, startTs, endTs);
  };

  // ── 多摄像头宫格入口（TopicModal 的宫格按钮） ──
  const handleOpenGrid = () => {
    const { bagPath, startTs, endTs, cameraTopics, row } = topicModalData;
    const videoTopics = cameraTopics; // 保留全部topic（含BEV），MultiVideoGrid中BEV会渲染BevViewer
    const hevcMime = 'video/mp4; codecs="hvc1.1.6.L120.B0"';
    const h264Mime = 'video/mp4; codecs="avc1.64001f"';
    const codec = forceH264 ? h264Mime : (typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(hevcMime) ? hevcMime : h264Mime);
    const durationSec = (endTs !== null && startTs !== null) ? (endTs - startTs) / 1e9 : null;
    const _multiViewMeta = { bagPath, emBinPath: topicModalData.emBinPath, startTs, endTs, cameraTopics: videoTopics };
    const defaultTopic = videoTopics[0] || '';
    setPlayerData({
      stream_url: '',
      row,
      topic: defaultTopic,
      use_mse: true,
      mse_codec: codec,
      durationSec,
      _multiViewMeta,
    });
    setPlayerGridMode(true);
    setPlayerGridTopics(videoTopics);
    setPlayerError(null);
    setTopicModalOpen(false);
    setTopicModalData(null);
    setPlayerModalOpen(true);
  };

  // 关闭播放器弹窗（清理全部关联状态与轮询）
  const handleClosePlayer = () => {
    setPlayerModalOpen(false);
    setPlayerData(null);
    setVideoRows([]);
    setPlayerError(null);
    setExtractModalOpen(false);
    setPlayerGridMode(false);
    setPlayerGridTopics([]);
    pollCancelledRef.current = true;
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  };

  // 播放失败后改用 H.264 重试
  const handleRetryH264 = () => {
    setPlayerModalOpen(false);
    setPlayerError(null);
    setForceH264(true);
    setTimeout(() => handleExtractVideo(), 100);
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
          // video_url 喂给 <video src>（无法设 header），必须拼 token 参数
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
        setPlayerData({ video_url: newlyCompleted.video_url, task_id: newlyCompleted.task_id, row: newlyCompleted.row, topic: newlyCompleted.topic, _multiViewMeta: { bagPath: '', startTs: null, endTs: null, cameraTopics: [] } });
        setPlayerModalOpen(true);
        setExtractModalOpen(false);  // 关闭进度弹窗，打开播放器
      }
    }, 1500);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      // 清理时标记取消，防止已发出的 fetch 回调写入 stale 数据
      pollCancelledRef.current = true;
    };
  }, [videoRows]); // eslint-disable-line react-hooks/exhaustive-deps

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

  // 客户端分页：从全量数据中切片当前页
  const displayRows = allRows.slice((page - 1) * pageSize, page * pageSize);

  return (
    <div style={card}>
      <h2 style={cardTitle}>🤖 NL2SQL Agent</h2>

      <QueryBar
        queryMode={queryMode} setQueryMode={setQueryMode}
        sqlEditMode={sqlEditMode} setSqlEditMode={setSqlEditMode}
        batches={batches} batchId={batchId} setBatchId={setBatchId}
        dbPath={dbPath} setDbPath={setDbPath}
        resultLimitInput={resultLimitInput} setResultLimitInput={setResultLimitInput}
        resultLimitUnlimited={resultLimitUnlimited} setResultLimitUnlimited={setResultLimitUnlimited}
        pageSize={pageSize} setPageSize={setPageSize} resetPage={() => setPage(1)}
        question={question} setQuestion={setQuestion}
        loading={loading}
        onSubmit={handleSubmit}
        onSubmitStream={handleSubmitStream}
        onCancel={handleCancelQuery}
      />

      {progress.length > 0 && (
        <div style={{ ...banner.info, fontFamily: 'Consolas, Monaco, monospace', fontSize: 12 }}>
          {progress.map((p, i) => (
            <div key={i} style={{ marginBottom: 4 }}>{p}</div>
          ))}
        </div>
      )}

      {/* 流式 SQL 生成实时显示 */}
      {streamingSql && (
        <div style={{ marginTop: 12, padding: 12, background: '#f6ffed', border: '1px solid #b7eb8f', borderRadius: 6 }}>
          <div style={{ fontSize: 12, color: colors.success, fontWeight: 600, marginBottom: 6 }}>✨ SQL 生成中...</div>
          <pre style={{ margin: 0, fontSize: 13, fontFamily: 'Consolas, Monaco, monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all', color: colors.text }}>{streamingSql}</pre>
        </div>
      )}

      {error && (
        <div style={banner.error}>{error}</div>
      )}

      {/* SQL 编辑器 */}
      <div style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6, flexWrap: 'wrap', gap: 6 }}>
          <span style={{ fontSize: 12, color: colors.textTertiary }}>SQL 编辑器</span>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={() => { strategies.setStrategyListOpen(!strategies.strategyListOpen); strategies.loadStrategyList(); }}
              style={btn.outline(colors.primary, false)}
            >
              我的策略 ({strategies.strategyList.length})
            </button>
            <button
              onClick={() => {
                // 预填关键词：从 SQL 提取 tag_name 作为默认关键词
                const m = sqlEditor.match(/(?:AS\s+tag_name|tag_name\s*=\s*)['"]([^'"]+)['"]/i)
                  || sqlEditor.match(/['"]([A-Z][A-Za-z_]+)['"]\s+AS\s+tag_name/i);
                const kw = m ? m[1] : '';
                strategies.setStrategyForm((prev) => ({ ...prev, keywords: prev.keywords || kw }));
                strategies.setSaveStrategyModalOpen(true);
              }}
              disabled={!sqlEditor.trim()}
              style={btn.outline(colors.success, !sqlEditor.trim())}
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
        <HistoryPanel onRestore={handleRestoreHistory} />
      </div>

      <ResultTable
        result={result}
        loading={loading}
        allRows={allRows}
        displayRows={displayRows}
        totalRows={totalRows}
        page={page}
        pageSize={pageSize}
        onPageChange={setPage}
        visualizedRows={visualizedRows}
        onVisualize={(row, rowKey) => {
          setVisualizedRows((prev) => new Set(prev).add(rowKey));
          startVisualization(row);
        }}
        matchedLabels={strategies.matchedLabels}
        labelKey={strategies.labelKey}
        actionDisabled={topicModalOpen || playerModalOpen}
        onArrowDownload={handleArrowDownload}
        mayBeTruncated={!resultLimitUnlimited && totalRows >= getResultLimit()}
      />

      {/* ── 弹窗群 ── */}
      {extractModalOpen && (
        <ExtractProgressModal
          videoRows={videoRows}
          onClose={(isFailed) => {
            setExtractModalOpen(false);
            if (isFailed) setVideoRows([]);
          }}
        />
      )}

      {topicModalOpen && (
        <TopicModal
          topicModalData={topicModalData}
          selectedTopic={selectedTopic}
          setSelectedTopic={setSelectedTopic}
          forceH264={forceH264}
          setForceH264={setForceH264}
          onClose={() => { setTopicModalOpen(false); setTopicModalData(null); }}
          onConfirm={handleExtractVideo}
          onOpenGrid={handleOpenGrid}
        />
      )}

      {sqlExecModalOpen && (
        <SqlExecModal
          elapsed={sqlExecElapsed}
          status={sqlExecStatus}
          onCancel={handleCancelSqlExec}
        />
      )}

      {playerModalOpen && playerData && (
        <PlayerModal
          playerData={playerData}
          playerMode={playerMode}
          playerError={playerError}
          setPlayerError={setPlayerError}
          playerGridMode={playerGridMode}
          setPlayerGridMode={setPlayerGridMode}
          playerGridTopics={playerGridTopics}
          setPlayerGridTopics={setPlayerGridTopics}
          onSwitchTopic={handleSwitchTopic}
          rowLabel={strategies.rowLabel}
          matchedStrategy={strategies.matchedStrategy}
          onLabel={strategies.handleLabel}
          onClose={handleClosePlayer}
          onRetryH264={handleRetryH264}
          streamAbortRef={streamAbortControllerRef}
          mseCleanupRef={mseCleanupRef}
        />
      )}

      <StrategyModals
        saveStrategyModalOpen={strategies.saveStrategyModalOpen}
        setSaveStrategyModalOpen={strategies.setSaveStrategyModalOpen}
        strategyForm={strategies.strategyForm}
        setStrategyForm={strategies.setStrategyForm}
        pendingLabel={strategies.pendingLabel}
        setPendingLabel={strategies.setPendingLabel}
        onSaveStrategy={strategies.handleSaveStrategy}
        strategyListOpen={strategies.strategyListOpen}
        setStrategyListOpen={strategies.setStrategyListOpen}
        strategyList={strategies.strategyList}
        onLoadStrategy={strategies.handleLoadStrategy}
        onDeleteStrategy={strategies.handleDeleteStrategy}
        onOpenValidationSet={strategies.openValidationSet}
        onOpenEvalSync={strategies.openEvalSyncModal}
        onSyncStrategyDm={strategies.handleSyncStrategyDm}
        evalSyncModal={strategies.evalSyncModal}
        setEvalSyncModal={strategies.setEvalSyncModal}
        onSyncEvalset={strategies.handleSyncEvalset}
        validationSetModal={strategies.validationSetModal}
        setValidationSetModal={strategies.setValidationSetModal}
        onRelabelCase={strategies.handleRelabelValidationCase}
        onVisualizeCase={(c) => {
          strategies.setValidationSetModal(null);
          startVisualization({ bag_id: c.bag_id, start_ts: c.start_ts, end_ts: c.end_ts, bag_path: null });
        }}
        syncBusy={strategies.syncBusy}
      />
    </div>
  );
}
