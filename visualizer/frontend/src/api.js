// ── API 基础设施（全前端唯一入口） ──
// 所有组件从这里导入，禁止再各自实现 authFetch。

export const API_BASE = process.env.REACT_APP_API_BASE || '';

// 带认证的 fetch wrapper：自动注入 Bearer token；
// 401 时清除 token 并广播 auth:401 事件（App 监听后跳回登录页）。
export function authFetch(url, options = {}) {
  const token = localStorage.getItem('token');
  const headers = { ...options.headers };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return fetch(url, { ...options, headers }).then((response) => {
    if (response.status === 401) {
      localStorage.removeItem('token');
      window.dispatchEvent(new CustomEvent('auth:401'));
    }
    return response;
  });
}

// 给 URL 拼接 token 查询参数。
// 仅用于 <video src> / <img src> 等浏览器原生标签无法设置 header 的场景；
// fetch 场景一律走 authFetch 的 Authorization header，不要再用此函数。
export function addTokenParam(url) {
  const token = localStorage.getItem('token');
  if (!url || !token) return url || '';
  const sep = url.includes('?') ? '&' : '?';
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}
