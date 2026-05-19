import React, { useState, useEffect, useRef } from 'react';
import './App.css';
import AgentPanel from './components/AgentPanel';

const API_BASE = process.env.REACT_APP_API_BASE || '';

function App() {
  const [topics, setTopics] = useState([]);
  const [bagPath, setBagPath] = useState('/root/data/bags/20260124_085515');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedTopic, setSelectedTopic] = useState('');
  const [taskId, setTaskId] = useState('');
  const [taskStatus, setTaskStatus] = useState(null);
  const [videoUrl, setVideoUrl] = useState('');
  const [durationSec, setDurationSec] = useState(0);
  const intervalRef = useRef(null);

  // Agent states
  const [agentQuestion, setAgentQuestion] = useState('');
  const [agentDbPath, setAgentDbPath] = useState('');
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentResult, setAgentResult] = useState(null);
  const [agentError, setAgentError] = useState('');

  const loadBag = async () => {
    setLoading(true);
    setError('');
    setTopics([]);
    setVideoUrl('');
    setTaskId('');
    setTaskStatus(null);
    try {
      const res = await fetch(`${API_BASE}/api/bag/info?bag_path=${encodeURIComponent(bagPath)}`, {
        method: 'POST',
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to load bag');
      }
      const data = await res.json();
      setTopics(data.topics || []);
      setDurationSec(data.duration_sec || 0);
      if (data.topics && data.topics.length > 0) {
        setSelectedTopic(data.topics[0].name);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const extractVideo = async () => {
    if (!selectedTopic) return;
    setError('');
    try {
      const res = await fetch(`${API_BASE}/api/video/extract`, {
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

  useEffect(() => {
    if (!taskId) return;
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/video/status/${taskId}`);
        if (!res.ok) return;
        const data = await res.json();
        setTaskStatus(data);
        if (data.status === 'completed') {
          setVideoUrl(data.video_url || '');
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

  return (
    <div className="App" style={{ maxWidth: 1200, margin: '0 auto', padding: 20, fontFamily: 'system-ui, -apple-system, sans-serif' }}>
      <h1>🎥 Rosbag Visualizer</h1>

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

          <div style={{ marginTop: 16, display: 'flex', gap: 10, alignItems: 'center' }}>
            <button
              onClick={extractVideo}
              disabled={!selectedTopic || (taskStatus && taskStatus.status === 'pending')}
              style={{ padding: '10px 24px', fontSize: 16, borderRadius: 4, border: 'none', background: '#52c41a', color: '#fff', cursor: 'pointer' }}
            >
              🎬 Extract Video
            </button>
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

      {videoUrl && (
        <div style={{ padding: 20, background: '#fff', borderRadius: 8, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
          <h2>🎬 Video Player</h2>
          <video
            src={videoUrl}
            controls
            style={{ width: '100%', maxHeight: 600, background: '#000', borderRadius: 4, marginTop: 12 }}
          />
        </div>
      )}

      <AgentPanel />
    </div>
  );
}

export default App;
