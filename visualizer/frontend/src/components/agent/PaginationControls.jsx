import React, { useState, useRef } from 'react';
import { colors, radius } from '../../theme';

// 分页控件：页码 + 省略号跳页。纯前端分页，onPageChange 不发请求。
export default function PaginationControls({ page, pageSize, totalRows, onPageChange }) {
  const totalPages = Math.max(1, Math.ceil(totalRows / pageSize));
  const [jumpInput, setJumpInput] = useState('');
  const [showJump, setShowJump] = useState(false);
  const jumpRef = useRef(null);

  // 生成页码列表：首页 ... 当前页附近 ... 尾页
  function getPageNumbers() {
    const pages = [];
    const delta = 2;
    pages.push(1);
    const rangeStart = Math.max(2, page - delta);
    const rangeEnd = Math.min(totalPages - 1, page + delta);
    if (rangeStart > 2) pages.push('left-ellipsis');
    for (let i = rangeStart; i <= rangeEnd; i++) pages.push(i);
    if (rangeEnd < totalPages - 1) pages.push('right-ellipsis');
    if (totalPages > 1) pages.push(totalPages);
    return pages;
  }

  const handleJumpSubmit = () => {
    const num = parseInt(jumpInput, 10);
    if (isNaN(num) || num < 1) onPageChange(1);
    else if (num > totalPages) onPageChange(totalPages);
    else onPageChange(num);
    setJumpInput('');
    setShowJump(false);
  };

  const handleJumpInput = (e) => {
    if (/^\d*$/.test(e.target.value)) setJumpInput(e.target.value);
  };

  const handleEllipsisClick = (side) => {
    setShowJump(true);
    setJumpInput(String(side === 'left' ? Math.max(1, page - 5) : Math.min(totalPages, page + 5)));
    setTimeout(() => jumpRef.current?.focus(), 50);
  };

  const btnStyle = (active, disabled) => ({
    minWidth: 32,
    height: 32,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: radius.sm,
    border: active ? `1px solid ${colors.primary}` : `1px solid ${colors.border}`,
    background: active ? colors.primary : disabled ? '#f5f5f5' : '#fff',
    color: active ? '#fff' : disabled ? '#bbb' : colors.text,
    cursor: disabled ? 'not-allowed' : 'pointer',
    fontSize: 13,
    padding: '0 6px',
    fontWeight: active ? 600 : 400,
    transition: 'all 0.15s ease',
  });

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', gap: 6, marginTop: 12, fontSize: 13, flexWrap: 'wrap' }}>
      <button disabled={page <= 1} onClick={() => onPageChange(page - 1)} style={btnStyle(false, page <= 1)}>‹</button>

      {getPageNumbers().map((p) => {
        if (p === 'left-ellipsis' || p === 'right-ellipsis') {
          return (
            <span key={p} style={{ display: 'inline-flex', alignItems: 'center' }}>
              <button
                onClick={() => handleEllipsisClick(p === 'left-ellipsis' ? 'left' : 'right')}
                style={{
                  minWidth: 32, height: 32, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  borderRadius: radius.sm, border: `1px solid ${colors.border}`, background: '#fff',
                  color: colors.primary, cursor: 'pointer', fontSize: 13, padding: '0 6px',
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
                    borderRadius: radius.sm, border: `1px solid ${colors.primary}`, marginLeft: 4, padding: '0 4px',
                  }}
                />
              )}
            </span>
          );
        }
        return (
          <button key={p} onClick={() => onPageChange(p)} style={btnStyle(p === page, false)}>{p}</button>
        );
      })}

      <button disabled={page >= totalPages} onClick={() => onPageChange(page + 1)} style={btnStyle(false, page >= totalPages)}>›</button>

      <span style={{ color: colors.textTertiary, fontSize: 12, marginLeft: 8 }}>
        共 {totalRows} 行 · 第 {page}/{totalPages} 页
      </span>
    </div>
  );
}
