import React, { useState } from 'react';

const API_BASE = 'http://localhost:8000';

export default function BagLoader({ onTopicsLoaded }) {
  const [path, setPath] = useState('/root/data/bags/20260124_085515');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const loadBag = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API_BASE}/api/bag/info?bag_path=${encodeURIComponent(path)}`, {
        method: 'POST',
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to load bag');
      }
      const data = await res.json();
      onTopicsLoaded(data.topics, path);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: '20px', background: '#fff', borderRadius: '8px', marginBottom: '20px' }}>
      <h2>📁 Bag Loader</h2>
      <div style={{ display: 'flex', gap: '10px', marginTop: '10px' }}>
        <input
          type="text"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          style={{ flex: 1, padding: '10px', fontSize: '16px', borderRadius: '4px', border: '1px solid #ccc' }}
          placeholder="Enter bag path..."
        />
        <button
          onClick={loadBag}
          disabled={loading}
          style={{ padding: '10px 20px', fontSize: '16px', cursor: loading ? 'not-allowed' : 'pointer', borderRadius: '4px', border: 'none', background: '#1890ff', color: '#fff' }}
        >
          {loading ? 'Loading...' : 'Load'}
        </button>
      </div>
      {error && <div style={{ color: 'red', marginTop: '10px' }}>{error}</div>}
    </div>
  );
}
