import React, { useState, useEffect, useRef, useCallback } from 'react';
import './App.css';
import AgentPanel from './components/AgentPanel';
import LoginPage from './components/LoginPage';
import BevViewer from './components/BevViewer';
import { API_BASE, authFetch, addTokenParam } from './api';
import { useToast } from './toast';
import { colors, card, cardTitle, cardSubtitle, btn, input, banner, badge } from './theme';
import { useMseStream } from './components/agent/useMseStream';

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
      .then((res) => {
        if (res.ok) {
          setAuthed(true);
        } else {
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

  const handleLogin = useCallback(() => {
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
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <div style={{ color: colors.textTertiary, fontSize: 16 }}>加载中...</div>
      </div>
    );
  }

  if (!authed) {
    return <LoginPage onLoginSuccess={handleLogin} />;
  }

  return <MainApp onLogout={handleLogout} />;
}


function MainApp({ onLogout }) {
  const toast = useToast();
  const [topics, setTopics] = useState([]);
  const [bagInput, setBagInput] = useState('');  // 用户输入（bag_id 或路径）
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedTopic, setSelectedTopic] = useState('');
  const [taskId, setTaskId] = useState('');
  const [taskStatus, setTaskStatus] = useState(null);
  const [videoUrl, setVideoUrl] = useState('');
  const [durationSec, setDurationSec] = useState(0);
  const [videoError, setVideoError] = useState(null);
  const [forceH264, setForceH264] = useState(false);
  const [streamPlayerData, setStreamPlayerData] = useState(null);
  const [fusionMapTopic, setFusionMapTopic] = useState(null);
  const [viewTab, setViewTab] = useState('camera'); // 'camera' | 'bev'
  const intervalRef = useRef(null);
  const videoRef = useRef(null);
  // ── 双路径：get_bag_info 返回的 em_bin_path 和 rosbag_path ──
  const [emBinPath, setEmBinPath] = useState(null);    // BEV 3D 用
  const [rosbagPath, setRosbagPath] = useState(null);  // camera 视频用

  // MSE 流式播放（Bag Loader 直传模式），逻辑与 AgentPanel 共用 useMseStream
  useMseStream({
    videoRef,
    active: !!streamPlayerData,
    streamUrl: streamPlayerData?.stream_url,
    codec: 'video/mp4; codecs="hvc1.1.6.L120.B0"',
    durationSec: streamPlayerData?.durationSec,
    onError: (msg) => setVideoError(msg),
  });

  const loadBag = async () => {
    setLoading(true);
    setError('');
    setTopics([]);
    setVideoUrl('');
    setTaskId('');
    setTaskStatus(null);
    setEmBinPath(null);
    setRosbagPath(null);
    try {
      const res = await authFetch(`${API_BASE}/api/bag/info?bag_path=${encodeURIComponent(bagInput)}`, {
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
      // ── 保存双路径 ──
      setEmBinPath(data.em_bin_path || null);
      setRosbagPath(data.rosbag_path || null);
      if (data.topics && data.topics.length > 0) {
        setSelectedTopic(data.topics[0].name);
      }
      // 自动切换：有 fusion_map → BEV, 有 camera topics → camera
      if (data.topics && data.topics.length > 0) {
        setViewTab('camera');
      } else if (data.fusion_map_topic) {
        setViewTab('bev');
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
        body: JSON.stringify({ bag_path: rosbagPath || bagInput, topic: selectedTopic }),
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
    setVideoUrl('');
    setStreamPlayerData(null);

    const topic = topics.find((t) => t.name === selectedTopic);
    const topicDuration = topic && topic.freq > 0 ? topic.message_count / topic.freq : (durationSec || 0);

    if (forceH264) {
      startH264Extraction();
      return;
    }

    const hevcMime = 'video/mp4; codecs="hvc1.1.6.L120.B0"';
    const canPlayHevc = document.createElement('video').canPlayType(hevcMime);
    const supportsHevcMSE = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(hevcMime);

    if (supportsHevcMSE) {
      // fetch 走 Authorization header 认证（useMseStream → authFetch），URL 不拼 token
      const params = new URLSearchParams({
        bag_path: rosbagPath || bagInput,
        topic: selectedTopic,
      });
      setStreamPlayerData({
        stream_url: `${API_BASE}/api/video/stream-hevc?${params.toString()}`,
        durationSec: topicDuration,
      });
      return;
    }

    toast.warning(`当前浏览器不支持 HEVC 解码（canPlayType=${canPlayHevc || '""'}），将自动使用 H.264 转码方式播放。`, 6000);
    startH264Extraction();
  };

  // 轮询 H.264 转码任务状态
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
          // video_url 喂给 <video src>（无法设 header），必须拼 token 参数
          setVideoUrl(addTokenParam(data.video_url || ''));
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

  const tabBtn = (tab) => ({
    padding: '8px 20px', fontSize: 14, cursor: 'pointer',
    border: `1px solid ${colors.border}`,
    borderBottom: viewTab === tab ? '2px solid #fff' : `1px solid ${colors.border}`,
    borderRadius: '8px 8px 0 0',
    background: viewTab === tab ? '#fff' : colors.bgHover,
    color: viewTab === tab ? colors.primary : colors.textSecondary,
    fontWeight: viewTab === tab ? 600 : 400,
  });

  return (
    <div className="App" style={{ maxWidth: 1400, width: '100%', margin: '0 auto', padding: '16px clamp(12px, 3vw, 32px)' }}>
      {/* ── 顶栏 ── */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
        <h1 style={{ margin: 0, fontSize: 22, color: colors.text }}>🎥 SceneSQL Visualizer</h1>
        <button onClick={onLogout} style={btn.ghost(false)}>
          退出登录
        </button>
      </div>

      {/* ── Bag Loader ── */}
      <div style={card}>
        <h2 style={cardTitle}>📁 Bag Loader</h2>
        <div style={cardSubtitle}>支持 bag_id（如 1002AePBU4WlfnBzNtDbBu202606）或本地 rosbag/em bin 路径</div>
        <div style={{ display: 'flex', gap: 10, marginTop: 12, flexWrap: 'wrap' }}>
          <input
            type="text"
            value={bagInput}
            onChange={(e) => setBagInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && bagInput.trim() && !loading && loadBag()}
            style={{ ...input, flex: 1, minWidth: 240, fontSize: 15, padding: '10px 14px' }}
            placeholder="输入 bag_id 或本地路径（如 1002AePBU4WlfnBzNtDbBu202606）"
          />
          <button
            onClick={loadBag}
            disabled={loading || !bagInput.trim()}
            style={{ ...btn.primary(loading || !bagInput.trim()), padding: '10px 24px', fontSize: 15 }}
          >
            {loading ? 'Loading...' : 'Load'}
          </button>
        </div>
        {error && <div style={banner.error}>{error}</div>}
      </div>

      {topics.length > 0 && (
        <>
          {/* ── View Tab 切换 ── */}
          <div style={{ display: 'flex', gap: 0, marginBottom: -1, position: 'relative', zIndex: 1 }}>
            <button onClick={() => setViewTab('bev')} style={tabBtn('bev')}>
              🗺️ BEV View {fusionMapTopic ? '' : '(无数据)'}
            </button>
            <button onClick={() => setViewTab('camera')} style={tabBtn('camera')}>
              📷 Camera ({topics.length})
            </button>
          </div>

          {/* ── BEV View ── */}
          {viewTab === 'bev' && (
            <BevViewer bagPath={emBinPath || bagInput} />
          )}

          {/* ── Camera View ── */}
          {viewTab === 'camera' && (
            <div style={card}>
              <h2 style={cardTitle}>📷 Camera Topics ({topics.length})</h2>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12, marginTop: 12 }}>
                {topics.map((t) => (
                  <div
                    key={t.name}
                    onClick={() => setSelectedTopic(t.name)}
                    style={{
                      padding: 12,
                      borderRadius: 8,
                      border: selectedTopic === t.name ? `2px solid ${colors.primary}` : `1px solid ${colors.border}`,
                      background: selectedTopic === t.name ? '#e6f4ff' : colors.bgStripe,
                      cursor: 'pointer',
                      transition: 'all 0.15s ease',
                    }}
                  >
                    <div style={{ fontWeight: 600, fontSize: 14, wordBreak: 'break-all' }}>{t.name}</div>
                    <div style={{ fontSize: 12, color: colors.textSecondary, marginTop: 4 }}>
                      {t.message_count} msgs · {t.freq?.toFixed(1)} Hz
                    </div>
                  </div>
                ))}
              </div>

              <div style={{ marginTop: 16, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                <button
                  onClick={extractVideo}
                  disabled={!selectedTopic || (taskStatus && taskStatus.status === 'pending')}
                  style={{ ...btn.success(!selectedTopic || (taskStatus && taskStatus.status === 'pending')), padding: '10px 24px', fontSize: 15 }}
                >
                  🎬 Extract Video
                </button>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: colors.textSecondary, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={forceH264}
                    onChange={(e) => setForceH264(e.target.checked)}
                    style={{ cursor: 'pointer' }}
                  />
                  ⚙️ 强制 H.264 转码
                </label>
                {taskStatus && (
                  <span style={{ fontSize: 14, color: colors.textSecondary }}>
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

      {/* ── H.264 转码播放器 ── */}
      {videoUrl && (
        <div style={card}>
          <h2 style={cardTitle}>
            🎬 Video Player
            <span style={badge(colors.orange)}>H.264 转码</span>
          </h2>
          <video
            src={videoUrl}
            controls
            style={{ width: '100%', maxHeight: 600, background: '#000', borderRadius: 8, marginTop: 12 }}
          />
        </div>
      )}

      {/* ── HEVC 直传播放器 ── */}
      {streamPlayerData && (
        <div style={card}>
          <h2 style={cardTitle}>
            🎬 Video Player
            <span style={badge(colors.success)}>HEVC 直传</span>
          </h2>
          {videoError && (
            <div style={{ ...banner.error, marginTop: 10 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>⚠️ 播放失败</div>
              <div>{videoError}</div>
              <button
                onClick={() => {
                  setVideoError(null);
                  setForceH264(true);
                  setStreamPlayerData(null);
                  setTimeout(() => extractVideo(), 100);
                }}
                style={{ ...btn.outline(colors.error, false), marginTop: 8 }}
              >
                🔄 改用 H.264 转码重试
              </button>
            </div>
          )}
          <video
            ref={videoRef}
            controls
            autoPlay
            style={{ width: '100%', maxHeight: 600, background: '#000', borderRadius: 8, marginTop: 12 }}
          />
        </div>
      )}

      <AgentPanel />
    </div>
  );
}

export default App;
