import React, { useState } from 'react';

const API_BASE = process.env.REACT_APP_API_BASE || '';

export default function LoginPage({ onLoginSuccess }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const res = await fetch(`${API_BASE}/api/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });

      const data = await res.json();

      if (!res.ok) {
        setError(data.detail || '登录失败');
        return;
      }

      localStorage.setItem('token', data.access_token);
      onLoginSuccess(data.access_token);
    } catch (err) {
      setError('网络错误，请检查连接');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: '100vh',
      background: 'linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)',
      fontFamily: 'system-ui, -apple-system, sans-serif',
    }}>
      <div style={{
        background: '#fff',
        borderRadius: 12,
        padding: '40px 36px',
        width: 380,
        boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
      }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{ fontSize: 48, marginBottom: 8 }}>🎥</div>
          <h1 style={{ margin: 0, fontSize: 24, color: '#1a1a2e' }}>SceneSQL Visualizer</h1>
          <p style={{ color: '#888', fontSize: 13, marginTop: 8 }}>自动驾驶场景数据可视化平台</p>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: 20 }}>
            <label style={{ display: 'block', fontSize: 13, color: '#555', marginBottom: 6 }}>
              用户名
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              style={{
                width: '100%',
                padding: '10px 14px',
                fontSize: 15,
                borderRadius: 6,
                border: '1px solid #d9d9d9',
                outline: 'none',
                boxSizing: 'border-box',
              }}
              onFocus={(e) => e.target.style.borderColor = '#1890ff'}
              onBlur={(e) => e.target.style.borderColor = '#d9d9d9'}
            />
          </div>

          <div style={{ marginBottom: 24 }}>
            <label style={{ display: 'block', fontSize: 13, color: '#555', marginBottom: 6 }}>
              密码
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              style={{
                width: '100%',
                padding: '10px 14px',
                fontSize: 15,
                borderRadius: 6,
                border: '1px solid #d9d9d9',
                outline: 'none',
                boxSizing: 'border-box',
              }}
              onFocus={(e) => e.target.style.borderColor = '#1890ff'}
              onBlur={(e) => e.target.style.borderColor = '#d9d9d9'}
            />
          </div>

          {error && (
            <div style={{
              color: '#cf1322',
              background: '#fff2f0',
              border: '1px solid #ffccc7',
              padding: '8px 12px',
              borderRadius: 4,
              fontSize: 13,
              marginBottom: 16,
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !username || !password}
            style={{
              width: '100%',
              padding: '12px',
              fontSize: 16,
              borderRadius: 6,
              border: 'none',
              background: loading || !username || !password ? '#d9d9d9' : '#1677ff',
              color: '#fff',
              cursor: loading || !username || !password ? 'not-allowed' : 'pointer',
              fontWeight: 600,
              transition: 'background 0.15s ease',
            }}
          >
            {loading ? '登录中...' : '登 录'}
          </button>
        </form>
      </div>
    </div>
  );
}
