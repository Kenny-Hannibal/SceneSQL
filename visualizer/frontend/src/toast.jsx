import React, { createContext, useCallback, useContext, useRef, useState } from 'react';
import { colors, radius, shadow } from './theme';

// ── 轻量 Toast 通知系统 ──
// 替代所有裸 alert()。用法：
//   const toast = useToast();
//   toast.success('策略已保存') / toast.error('保存失败: ...') / toast.info('...')

const ToastContext = createContext(null);

const TYPE_STYLE = {
  success: { border: colors.success, icon: '✅' },
  error: { border: colors.error, icon: '❌' },
  info: { border: colors.primary, icon: 'ℹ️' },
  warning: { border: colors.warning, icon: '⚠️' },
};

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback((type, message, duration = 3500) => {
    const id = ++idRef.current;
    setToasts((prev) => [...prev.slice(-4), { id, type, message: String(message) }]);
    if (duration > 0) {
      setTimeout(() => dismiss(id), duration);
    }
    return id;
  }, [dismiss]);

  const api = {
    success: (msg, duration) => push('success', msg, duration),
    error: (msg, duration) => push('error', msg, duration ?? 5000),
    info: (msg, duration) => push('info', msg, duration),
    warning: (msg, duration) => push('warning', msg, duration),
  };

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        style={{
          position: 'fixed',
          top: 20,
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 2000,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          pointerEvents: 'none',
          maxWidth: 'min(560px, 90vw)',
        }}
      >
        {toasts.map((t) => {
          const s = TYPE_STYLE[t.type] || TYPE_STYLE.info;
          return (
            <div
              key={t.id}
              onClick={() => dismiss(t.id)}
              style={{
                pointerEvents: 'auto',
                display: 'flex',
                alignItems: 'flex-start',
                gap: 8,
                padding: '10px 16px',
                background: '#fff',
                borderRadius: radius.md,
                borderLeft: `3px solid ${s.border}`,
                boxShadow: shadow.modal,
                fontSize: 13,
                color: colors.text,
                cursor: 'pointer',
                whiteSpace: 'pre-line',
                animation: 'toast-slide-in 0.2s ease',
              }}
            >
              <span style={{ flexShrink: 0 }}>{s.icon}</span>
              <span style={{ wordBreak: 'break-word' }}>{t.message}</span>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within <ToastProvider>');
  return ctx;
}
