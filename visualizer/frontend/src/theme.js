// ── 设计令牌与共享样式 ──
// 全前端统一的视觉语言：配色、圆角、阴影、按钮/输入框/卡片/弹窗。
// 组件内联样式只允许做布局微调，颜色/圆角/阴影必须从这里取。

export const colors = {
  primary: '#1677ff',
  primaryActive: '#0958d9',
  success: '#52c41a',
  error: '#f5222d',
  warning: '#faad14',
  purple: '#722ed1',
  cyan: '#13c2c2',
  orange: '#fa8c16',

  text: '#1f2329',
  textSecondary: '#646a73',
  textTertiary: '#8f959e',

  border: '#e5e6eb',
  borderLight: '#f0f0f0',
  bgPage: '#f3f5f8',
  bgCard: '#ffffff',
  bgHover: '#f5f7fa',
  bgStripe: '#fafbfc',

  // 播放器（深色区）
  darkBg: '#141414',
  darkBorder: '#434343',
  darkText: '#d9d9d9',
};

export const radius = { sm: 4, md: 6, lg: 10 };

export const shadow = {
  card: '0 1px 2px rgba(31,35,41,0.04), 0 4px 16px rgba(31,35,41,0.06)',
  modal: '0 8px 32px rgba(31,35,41,0.18)',
};

// ── 层级规范（全站统一，禁止再写散 z-index） ──
// modal  : 轻量表单/选择类弹窗（保存策略、评测同步、Topic 选择、进度弹窗）
// player : 视频/BEV 播放器弹窗 —— 必须压过一切普通 modal
// toast  : 全局通知，永远最顶
export const zIndex = { modal: 1000, player: 1100, toast: 2000 };

// ── 卡片 ──
export const card = {
  background: colors.bgCard,
  borderRadius: radius.lg,
  padding: 20,
  boxShadow: shadow.card,
  marginBottom: 16,
};

export const cardTitle = {
  margin: '0 0 4px',
  fontSize: 16,
  fontWeight: 600,
  color: colors.text,
  display: 'flex',
  alignItems: 'center',
  gap: 8,
};

export const cardSubtitle = {
  fontSize: 12,
  color: colors.textTertiary,
  marginTop: 2,
};

// ── 按钮 ──
const btnBase = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 6,
  padding: '8px 16px',
  fontSize: 13,
  fontWeight: 500,
  borderRadius: radius.md,
  border: '1px solid transparent',
  cursor: 'pointer',
  transition: 'all 0.15s ease',
  whiteSpace: 'nowrap',
  lineHeight: 1.4,
};

function btnVariant(fg, bg, border) {
  return { ...btnBase, color: fg, background: bg, borderColor: border || bg };
}

export const btn = {
  base: btnBase,
  primary: (disabled) => ({
    ...btnVariant('#fff', disabled ? '#b3d1ff' : colors.primary),
    cursor: disabled ? 'not-allowed' : 'pointer',
  }),
  success: (disabled) => ({
    ...btnVariant('#fff', disabled ? '#b7eb8f' : colors.success),
    cursor: disabled ? 'not-allowed' : 'pointer',
  }),
  danger: (disabled) => ({
    ...btnVariant('#fff', disabled ? '#ffa39e' : colors.error),
    cursor: disabled ? 'not-allowed' : 'pointer',
  }),
  purple: (disabled) => ({
    ...btnVariant('#fff', disabled ? '#d3adf7' : colors.purple),
    cursor: disabled ? 'not-allowed' : 'pointer',
  }),
  cyan: (disabled) => ({
    ...btnVariant('#fff', disabled ? '#87e8de' : colors.cyan),
    cursor: disabled ? 'not-allowed' : 'pointer',
  }),
  ghost: (disabled) => ({
    ...btnVariant(disabled ? colors.textTertiary : colors.textSecondary, '#fff', colors.border),
    cursor: disabled ? 'not-allowed' : 'pointer',
  }),
  // 小尺寸描边按钮（表格行内/列表操作用）
  outline: (color, disabled, filled) => ({
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    padding: '2px 10px',
    fontSize: 12,
    borderRadius: radius.sm,
    border: `1px solid ${disabled ? colors.border : color}`,
    background: filled ? color : 'transparent',
    color: filled ? '#fff' : (disabled ? colors.textTertiary : color),
    cursor: disabled ? 'not-allowed' : 'pointer',
    whiteSpace: 'nowrap',
    transition: 'all 0.15s ease',
  }),
};

// ── 分段切换按钮（查询模式/LLM 行为这类二选一） ──
export function segBtn(active, activeColor) {
  return {
    ...btnBase,
    padding: '6px 14px',
    border: `1px solid ${active ? activeColor : colors.border}`,
    background: active ? activeColor : '#fff',
    color: active ? '#fff' : colors.textSecondary,
    fontWeight: active ? 600 : 400,
  };
}

// ── 输入框 ──
export const input = {
  width: '100%',
  padding: '8px 12px',
  fontSize: 14,
  borderRadius: radius.md,
  border: `1px solid ${colors.border}`,
  outline: 'none',
  boxSizing: 'border-box',
  color: colors.text,
  background: '#fff',
  transition: 'border-color 0.15s ease',
};

export const select = { ...input, width: 'auto' };

export const fieldLabel = {
  fontSize: 13,
  color: colors.textSecondary,
  fontWeight: 500,
  whiteSpace: 'nowrap',
};

// ── 弹窗 ──
export const modal = {
  overlay: (zIndex = 1000) => ({
    position: 'fixed',
    top: 0, left: 0, right: 0, bottom: 0,
    background: 'rgba(15,23,42,0.45)',
    backdropFilter: 'blur(2px)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex,
  }),
  dialog: (minWidth = 400, maxWidth = 520) => ({
    background: '#fff',
    borderRadius: radius.lg,
    padding: 24,
    minWidth,
    maxWidth,
    maxHeight: '85vh',
    overflow: 'auto',
    boxShadow: shadow.modal,
  }),
  title: { margin: '0 0 16px', fontSize: 16, fontWeight: 600, color: colors.text },
  closeBtn: {
    border: 'none',
    background: 'none',
    fontSize: 16,
    cursor: 'pointer',
    color: colors.textTertiary,
    padding: '2px 6px',
    borderRadius: radius.sm,
    lineHeight: 1,
  },
};

// ── 内联错误/警告/信息条 ──
export const banner = {
  error: {
    marginTop: 12, padding: '10px 14px',
    background: '#fff1f0', border: '1px solid #ffccc7',
    borderRadius: radius.md, color: '#cf1322', fontSize: 13,
  },
  warning: {
    marginTop: 12, padding: '10px 14px',
    background: '#fffbe6', border: '1px solid #ffe58f',
    borderRadius: radius.md, color: '#874d00', fontSize: 13,
  },
  info: {
    marginTop: 12, padding: '10px 14px',
    background: '#f0f5ff', border: '1px solid #d6e4ff',
    borderRadius: radius.md, color: '#1d39c4', fontSize: 13,
  },
};

// ── 状态徽标 ──
export function badge(color) {
  return {
    display: 'inline-flex',
    alignItems: 'center',
    padding: '1px 8px',
    borderRadius: 10,
    fontSize: 11,
    fontWeight: 500,
    background: color,
    color: '#fff',
    lineHeight: '18px',
  };
}
