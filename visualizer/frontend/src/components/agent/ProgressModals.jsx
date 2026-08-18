import React from 'react';
import { colors, modal, btn, banner } from '../../theme';

// SQL 执行进度弹窗：耗时计时 + 慢查询/卡死分级提示 + 取消执行。
export function SqlExecModal({ elapsed, status, onCancel }) {
  const barColor =
    status === 'stuck' ? colors.error
    : status === 'slow' ? colors.warning
    : status === 'loading_body' ? colors.success
    : colors.primary;

  return (
    <div style={modal.overlay(1000)}>
      <div style={{ ...modal.dialog(360, 480), textAlign: 'center' }}>
        <h3 style={modal.title}>⏳ 正在执行 SQL</h3>
        <div style={{ fontSize: 14, color: colors.textSecondary, marginBottom: 12 }}>
          已耗时 <b>{elapsed}</b> 秒
        </div>
        <div style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 16, minHeight: 60, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {status === 'pending' && <span>已发送请求，等待后端响应...</span>}
          {status === 'slow' && <span style={{ color: '#d48806' }}>后端响应较慢，可能正在遍历大量 DB，请耐心等待</span>}
          {status === 'stuck' && (
            <span style={{ color: '#cf1322' }}>
              ⚠️ 超过 15 秒未收到后端响应，请求很可能已卡住。<br />
              建议点击「取消执行」后检查后端日志。
            </span>
          )}
          {status === 'loading_body' && <span style={{ color: colors.success }}>后端已开始返回结果，正在接收数据...</span>}
          {status === 'error' && <span style={{ color: '#cf1322' }}>请求出错，关闭弹窗后查看错误信息</span>}
        </div>
        <div style={{ width: '100%', height: 8, background: colors.borderLight, borderRadius: 4, overflow: 'hidden', marginBottom: 16 }}>
          <div style={{
            width: `${Math.min(100, (elapsed / 10) * 100)}%`,
            height: '100%',
            background: barColor,
            borderRadius: 4,
            transition: 'width 1s linear',
          }} />
        </div>
        <button onClick={onCancel} style={btn.ghost(false)}>取消执行</button>
      </div>
    </div>
  );
}

// 视频提取进度弹窗（替代底部堆积面板）：显示最新一条提取任务状态。
export function ExtractProgressModal({ videoRows, onClose }) {
  if (videoRows.length === 0) return null;
  const v = videoRows[videoRows.length - 1];
  const isFailed = v.status === 'failed';

  return (
    <div style={modal.overlay(1000)}>
      <div style={{ ...modal.dialog(360, 450), textAlign: 'center' }}>
        <h3 style={modal.title}>{isFailed ? '❌ 视频提取失败' : '📹 视频提取中'}</h3>
        <div style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 8 }}>
          <b>Bag:</b> {v.row.bag_id || v.row.db_file} &nbsp;|&nbsp;
          <b>Topic:</b> {v.topic}
        </div>
        {!isFailed && (
          <div style={{ margin: '16px 0' }}>
            <div style={{ fontSize: 14, color: colors.primary, marginBottom: 8 }}>
              {v.status === 'pending' && '⏳ 排队中...'}
              {v.status === 'processing' && `⏳ 提取中... ${v.progress.toFixed(1)}%`}
              {v.status === 'completed' && '✅ 提取完成，即将播放...'}
            </div>
            {v.status === 'processing' && (
              <div style={{ width: '100%', height: 8, background: colors.borderLight, borderRadius: 4, overflow: 'hidden' }}>
                <div style={{ width: `${v.progress}%`, height: '100%', background: colors.primary, borderRadius: 4, transition: 'width 0.3s' }} />
              </div>
            )}
          </div>
        )}
        {isFailed && (
          <div style={{ ...banner.error, marginTop: 0, margin: '12px 0' }}>{v.message}</div>
        )}
        <button onClick={() => onClose(isFailed)} style={{ ...btn.ghost(false), marginTop: 12 }}>
          {isFailed ? '关闭' : '取消提取'}
        </button>
      </div>
    </div>
  );
}
