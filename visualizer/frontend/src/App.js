import React, { useState, useEffect, useRef, useCallback } from 'react';
import './App.css';
import AgentPanel from './components/AgentPanel';
import LoginPage from './components/LoginPage';
import BevViewer from './components/BevViewer';

const API_BASE = process.env.REACT_APP_API_BASE || '';

// ── 带认证的 fetch wrapper ──
// 401 时自动清除 token 并刷新页面（跳回登录）
function authFetch(url, options = {}) {
  const token = localStorage.getItem('token');
  const headers = { ...options.headers };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return fetch(url, { ...options, headers }).then(response => {
    if (response.status === 401) {
      localStorage.removeItem('token');
      // 不直接刷新页面，让上层组件通过 token 丢失自然跳回登录
      // 触发一个自定义事件，App 组件可以监听
      window.dispatchEvent(new CustomEvent('auth:401'));
    }
    return response;
  });
}

function App() {
  const [authed, setAuthed] = useState(false);
  const [authChecking, setAuthChecking] = useState(true);

  // ── 启动时检查 localStorage 中是否有有效 token ──
  useEffect(() => {
    const token = localStorage.getItem('token');
    if (!token) {
      setAuthChecking(false);
      return;
    }
    // 验证 token 是否还有效
    fetch(`${API_BASE}/api/auth/verify`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(res => {
        if (res.ok) {
          setAuthed(true);
        } else {
          // token 过期或无效，清除
          localStorage.removeItem('token');
          setAuthed(false);
        }
      })
      .catch(() => {
        // 网络错误 — 仍然尝试用现有 token（可能是临时断网）
        setAuthed(true);
      })
      .finally(() => setAuthChecking(false));
  }, []);

  const handleLogin = useCallback((token) => {
    setAuthed(true);
  }, []);

  const handleLogout = useCallback(() => {
    localStorage.removeItem('token');
    setAuthed(false);
  }, []);

  // 监听 auth:401 事件（authFetch 检测到 401 时触发）
  useEffect(() => {
    const onAuth401 = () => setAuthed(false);
    window.addEventListener('auth:401', onAuth401);
    return () => window.removeEventListener('auth:401', onAuth401);
  }, []);

  // ── 加载中 ──
  if (authChecking) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', fontFamily: 'system-ui' }}>
        <div style={{ color: '#888', fontSize: 16 }}>加载中...</div>
      </div>
    );
  }

  // ── 未登录 → 登录页 ──
  if (!authed) {
    return <LoginPage onLoginSuccess={handleLogin} />;
  }

  // ── 已登录 → 原有内容 ──
  return <MainApp onLogout={handleLogout} />;
}


function MainApp({ onLogout }) {
  const [topics, setTopics] = useState([]);
  const [bagPath, setBagPath] = useState('/root/data/bags/20260124_085515');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedTopic, setSelectedTopic] = useState('');
  const [taskId, setTaskId] = useState('');
  const [taskStatus, setTaskStatus] = useState(null);
  const [videoUrl, setVideoUrl] = useState('');
  const [durationSec, setDurationSec] = useState(0);
  const [videoMode, setVideoMode] = useState(null);
  const [videoError, setVideoError] = useState(null);
  const [forceH264, setForceH264] = useState(false);
  const [streamPlayerData, setStreamPlayerData] = useState(null);
  const [fusionMapTopic, setFusionMapTopic] = useState(null);
  const [viewTab, setViewTab] = useState('camera'); // 'camera' | 'bev'
  const intervalRef = useRef(null);
  const videoRef = useRef(null);

  const loadBag = async () => {
    setLoading(true);
    setError('');
    setTopics([]);
    setVideoUrl('');
    setTaskId('');
    setTaskStatus(null);
    try {
      const res = await authFetch(`${API_BASE}/api/bag/info?bag_path=${encodeURIComponent(bagPath)}`, {
        method: 'POST',
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to load bag');
      }
      const data = await res.json();
      setTopics(data.topics || []);
      setDurationSec(data.duration_sec || 0);
      setFusionMapTopic(data.fusion_map_topic || null);
      if (data.topics && data.topics.length > 0) {
        setSelectedTopic(data.topics[0].name);
      }
      // 自动切换到 BEV tab 如果有 fusion_map 数据
      if (data.fusion_map_topic) {
        setViewTab('bev');
      } else {
        setViewTab('camera');
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const startH264Extraction = async () => {
    try {
      const res = await authFetch(`${API_BASE}/api/video/extract`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bag_path: bagPath, topic: selectedTopic }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Extraction failed');
      }
      const data = await res.json();
      setTaskId(data.task_id);
      setTaskStatus(data);
      setVideoUrl('');
    } catch (e) {
      setError(e.message);
    }
  };

  const extractVideo = async () => {
    if (!selectedTopic) return;
    setError('');
    setVideoError(null);
    setVideoMode(null);
    setVideoUrl('');
    setStreamPlayerData(null);

    const topic = topics.find((t) => t.name === selectedTopic);
    const topicDuration = topic && topic.freq > 0 ? topic.message_count / topic.freq : (durationSec || 0);

    if (forceH264) {
      console.log('[HEVC诊断] 用户强制使用 H.264 转码');
      setVideoMode('h264-file');
      startH264Extraction();
      return;
    }

    const hevcMime = 'video/mp4; codecs="hvc1.1.6.L120.B0"';
    const canPlayHevc = document.createElement('video').canPlayType(hevcMime);
    const supportsHevcMSE = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(hevcMime);

    console.log('[HEVC诊断] canPlayType:', canPlayHevc, '| MSE支持:', supportsHevcMSE);

    if (supportsHevcMSE) {
      console.log('[HEVC诊断] 浏览器支持HEVC MSE，尝试流式播放');
      setVideoMode('hevc-stream');
      const params = new URLSearchParams({
        bag_path: bagPath,
        topic: selectedTopic,
      });
      // 流式播放 URL 需要带 token 作为查询参数（因为 MSE fetch 不支持自定义 header）
      const token = localStorage.getItem('token');
      params.set('token', token || '');
      setStreamPlayerData({
        stream_url: `${API_BASE}/api/video/stream-hevc?${params.toString()}`,
        durationSec: topicDuration,
      });
      return;
    }

    console.log('[HEVC诊断] 浏览器不支持HEVC MSE，降级到H.264转码');
    alert('当前浏览器不支持 HEVC 解码（canPlayType=' + canPlayHevc + '），将自动使用 H.264 转码方式播放。');
    setVideoMode('h264-file');
    startH264Extraction();
  };

  useEffect(() => {
    if (!taskId) return;
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(async () => {
      try {
        const res = await authFetch(`${API_BASE}/api/video/status/${taskId}`);
        if (!res.ok) return;
        const data = await res.json();
        setTaskStatus(data);
        if (data.status === 'completed') {
          // video_url 是 /api/video/file/{task_id}，需要拼 token（浏览器 <video> 不带 Authorization header）
          let vurl = data.video_url || '';
          const token = localStorage.getItem('token');
          if (vurl && token) {
            const sep = vurl.includes('?') ? '&' : '?';
            vurl = `${vurl}${sep}token=${encodeURIComponent(token)}`;
          }
          setVideoUrl(vurl);
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        } else if (data.status === 'failed' || data.status === 'not_found') {
          clearInterval(intervalRef.current);
          intervalRef.current = null;
        }
      } catch (e) {
        console.error(e);
      }
    }, 1000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [taskId]);

  // MSE 流式 HEVC 播放逻辑（Bag Loader）
  useEffect(() => {
    if (!streamPlayerData || !videoRef.current) return;

    const video = videoRef.current;
    const mediaSource = new MediaSource();
    const objectUrl = URL.createObjectURL(mediaSource);
    video.src = objectUrl;

    let sourceBuffer = null;
    let reader = null;
    let aborted = false;

    const mimeCodec = 'video/mp4; codecs="hvc1.1.6.L120.B0"';

    const cleanup = () => {
      if (aborted) return;
      aborted = true;
      URL.revokeObjectURL(objectUrl);
      if (mediaSource.readyState === 'open') {
        try { mediaSource.endOfStream(); } catch (e) {}
      }
      if (reader) {
        reader.cancel().catch(() => {});
      }
    };

    const onMseError = (source, detail) => {
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
      console.error('[HEVC诊断] MSE错误:', diagnostics);
      const msg = `[${source}] ${detail || '未知错误'} | video.error=${videoErr?.code || 'none'} | msState=${msState}`;
      setVideoError('HEVC流式播放失败: ' + msg);
      cleanup();
    };

    mediaSource.addEventListener('sourceopen', async () => {
      if (aborted) return;
      try {
        if (streamPlayerData.durationSec && streamPlayerData.durationSec > 0) {
          try {
            mediaSource.duration = streamPlayerData.durationSec;
            console.log('[HEVC诊断] 预设视频时长:', streamPlayerData.durationSec, '秒');
          } catch (e) {
            console.warn('[HEVC诊断] 设置 duration 失败:', e);
          }
        }

        sourceBuffer = mediaSource.addSourceBuffer(mimeCodec);

        const response = await authFetch(streamPlayerData.stream_url);
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

        sourceBuffer.addEventListener('updateend', () => {
          isUpdating = false;
          if (queue.length === 0 && reader === null) {
            try { mediaSource.endOfStream(); } catch (e) {}
            return;
          }
          processQueue();
        });

        sourceBuffer.addEventListener('error', (e) => {
          onMseError('SourceBuffer', e.message || 'SourceBuffer error');
        });

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
      } catch (e) {
        onMseError('Fetch/Setup', e.message || String(e));
      }
    });

    const onVideoError = () => {
      const ve = video.error;
      if (ve) {
        const codes = { 1: 'MEDIA_ERR_ABORTED', 2: 'MEDIA_ERR_NETWORK', 3: 'MEDIA_ERR_DECODE', 4: 'MEDIA_ERR_SRC_NOT_SUPPORTED' };
        onMseError('VideoElement', `${codes[ve.code] || 'UNKNOWN'}: ${ve.message || ''}`);
      }
    };
    video.addEventListener('error', onVideoError);

    mediaSource.addEventListener('error', (e) => {
      onMseError('MediaSource', e.message || 'MediaSource error');
    });

    return () => {
      video.removeEventListener('error', onVideoError);
      cleanup();
    };
  }, [streamPlayerData]);

  return (
    <div className="App" style={{ maxWidth: 1200, margin: '0 auto', padding: 20, fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h1 style={{ margin: 0 }}>🎥 Rosbag Visualizer</h1>
        <button
          onClick={onLogout}
          style={{
            padding: '6px 16px',
            fontSize: 13,
            borderRadius: 4,
            border: '1px solid #d9d9d9',
            background: '#fff',
            color: '#555',
            cursor: 'pointer',
          }}
        >
          退出登录
        </button>
      </div>

      <div style={{ padding: 20, background: '#fff', borderRadius: 8, marginBottom: 20, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
        <h2>📁 Bag Loader</h2>
        <div style={{ display: 'flex', gap: 10, marginTop: 10 }}>
          <input
            type="text"
            value={bagPath}
            onChange={(e) => setBagPath(e.target.value)}
            style={{ flex: 1, padding: '10px', fontSize: 16, borderRadius: 4, border: '1px solid #ccc' }}
            placeholder="Enter bag path..."
          />
          <button
            onClick={loadBag}
            disabled={loading}
            style={{ padding: '10px 20px', fontSize: 16, cursor: loading ? 'not-allowed' : 'pointer', borderRadius: 4, border: 'none', background: '#1890ff', color: '#fff' }}
          >
            {loading ? 'Loading...' : 'Load'}
          </button>
        </div>
        {error && <div style={{ color: 'red', marginTop: 10 }}>{error}</div>}
      </div>

      {topics.length > 0 && (
        <>
          {/* ── View Tab 切换 ── */}
          <div style={{ display: 'flex', gap: 0, marginBottom: -1, position: 'relative', zIndex: 1 }}>
            <button
              onClick={() => setViewTab('bev')}
              style={{
                padding: '8px 20px', fontSize: 14, cursor: 'pointer',
                border: '1px solid #d9d9d9', borderBottom: viewTab === 'bev' ? '2px solid #fff' : '1px solid #d9d9d9',
                borderRadius: '8px 8px 0 0',
                background: viewTab === 'bev' ? '#fff' : '#fafafa',
                color: viewTab === 'bev' ? '#1890ff' : '#666',
                fontWeight: viewTab === 'bev' ? 600 : 400,
              }}
            >
              🗺️ BEV View {fusionMapTopic ? '' : '(无数据)'}
            </button>
            <button
              onClick={() => setViewTab('camera')}
              style={{
                padding: '8px 20px', fontSize: 14, cursor: 'pointer',
                border: '1px solid #d9d9d9', borderBottom: viewTab === 'camera' ? '2px solid #fff' : '1px solid #d9d9d9',
                borderRadius: '8px 8px 0 0',
                background: viewTab === 'camera' ? '#fff' : '#fafafa',
                color: viewTab === 'camera' ? '#1890ff' : '#666',
                fontWeight: viewTab === 'camera' ? 600 : 400,
              }}
            >
              📷 Camera ({topics.length})
            </button>
          </div>

          {/* ── BEV View ── */}
          {viewTab === 'bev' && (
            <BevViewer bagPath={bagPath} authFetch={authFetch} />
          )}

          {/* ── Camera View ── */}
          {viewTab === 'camera' && (
        <div style={{ padding: 20, background: '#fff', borderRadius: 8, marginBottom: 20, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
          <h2>📷 Camera Topics ({topics.length})</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12, marginTop: 12 }}>
            {topics.map((t) => (
              <div
                key={t.name}
                onClick={() => setSelectedTopic(t.name)}
                style={{
                  padding: 12,
                  borderRadius: 6,
                  border: selectedTopic === t.name ? '2px solid #1890ff' : '1px solid #e8e8e8',
                  background: selectedTopic === t.name ? '#e6f7ff' : '#fafafa',
                  cursor: 'pointer',
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 14 }}>{t.name}</div>
                <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
                  {t.message_count} msgs · {t.freq?.toFixed(1)} Hz
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 16, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <button
              onClick={extractVideo}
              disabled={!selectedTopic || (taskStatus && taskStatus.status === 'pending')}
              style={{ padding: '10px 24px', fontSize: 16, borderRadius: 4, border: 'none', background: '#52c41a', color: '#fff', cursor: 'pointer' }}
            >
              🎬 Extract Video
            </button>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#555', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={forceH264}
                onChange={(e) => setForceH264(e.target.checked)}
                style={{ cursor: 'pointer' }}
              />
              ⚙️ 强制 H.264 转码
            </label>
            {taskStatus && (
              <span style={{ fontSize: 14, color: '#555' }}>
                {taskStatus.status === 'processing' && `⏳ ${taskStatus.message} (${taskStatus.progress.toFixed(1)}%)`}
                {taskStatus.status === 'completed' && '✅ Done'}
                {taskStatus.status === 'failed' && `❌ ${taskStatus.message}`}
                {taskStatus.status === 'pending' && '⏳ Pending...'}
              </span>
            )}
          </div>
        </div>
          )}
        </>
      )}

      {videoUrl && (
        <div style={{ padding: 20, background: '#fff', borderRadius: 8, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
          <h2>
            🎬 Video Player
            <span style={{ fontSize: 12, marginLeft: 8, padding: '2px 8px', borderRadius: 4, background: '#fa8c16', color: '#fff' }}>
              H.264 转码
            </span>
          </h2>
          <video
            src={videoUrl}
            controls
            style={{ width: '100%', maxHeight: 600, background: '#000', borderRadius: 4, marginTop: 12 }}
          />
        </div>
      )}

      {streamPlayerData && (
        <div style={{ padding: 20, background: '#fff', borderRadius: 8, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
          <h2>
            🎬 Video Player
            <span style={{ fontSize: 12, marginLeft: 8, padding: '2px 8px', borderRadius: 4, background: '#52c41a', color: '#fff' }}>
              HEVC 直传
            </span>
          </h2>
          {videoError && (
            <div style={{ background: '#fff2f0', border: '1px solid #ffccc7', color: '#cf1322', padding: 12, borderRadius: 4, marginTop: 10, fontSize: 13 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>⚠️ 播放失败</div>
              <div>{videoError}</div>
              <button
                onClick={() => {
                  setVideoError(null);
                  setForceH264(true);
                  setStreamPlayerData(null);
                  setTimeout(() => extractVideo(), 100);
                }}
                style={{ marginTop: 8, padding: '5px 14px', fontSize: 12, borderRadius: 4, border: '1px solid #cf1322', background: '#fff', color: '#cf1322', cursor: 'pointer' }}
              >
                🔄 改用 H.264 转码重试
              </button>
            </div>
          )}
          <video
            ref={videoRef}
            controls
            autoPlay
            style={{ width: '100%', maxHeight: 600, background: '#000', borderRadius: 4, marginTop: 12 }}
          />
        </div>
      )}

      <AgentPanel authFetch={authFetch} />
    </div>
  );
}

export default App;
