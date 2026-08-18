import React from 'react';
import { colors, modal, btn, input, select } from '../../theme';

// Topic 选择弹窗：播包可视化前选择 camera/BEV topic，支持多摄像头宫格入口。
export default function TopicModal({
  topicModalData,
  selectedTopic, setSelectedTopic,
  forceH264, setForceH264,
  onClose, onConfirm, onOpenGrid,
}) {
  if (!topicModalData) return null;

  const isBev = selectedTopic.includes('fusion_map');

  return (
    <div
      style={modal.overlay(1000)}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div style={modal.dialog(400, 500)}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h3 style={{ ...modal.title, margin: 0 }}>📹 播包可视化</h3>
          <button onClick={onClose} style={modal.closeBtn} title="关闭">✕</button>
        </div>

        <div style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 12, wordBreak: 'break-all' }}>
          <b>Bag:</b> {topicModalData.bagPath}<br />
          <b>时间范围:</b> {topicModalData.row.start_ts} ~ {topicModalData.row.end_ts} (秒)
        </div>

        {topicModalData.loading ? (
          <div style={{ padding: '20px 0', textAlign: 'center' }}>
            <div style={{ fontSize: 14, color: colors.primary, marginBottom: 12 }}>{topicModalData.loadingMsg || '加载中...'}</div>
            <div style={{ width: '100%', height: 6, background: colors.borderLight, borderRadius: 3, overflow: 'hidden' }}>
              <div style={{ width: '100%', height: '100%', background: colors.primary, borderRadius: 3, animation: 'progress-pulse 1.5s ease-in-out infinite' }} />
            </div>
          </div>
        ) : (
          <div style={{ marginBottom: 16 }}>
            {topicModalData.cameraTopics.length > 0 ? (
              <div>
                <label style={{ fontSize: 13, color: colors.textSecondary, fontWeight: 500, display: 'block', marginBottom: 6 }}>
                  📹 Topic:
                </label>
                <select
                  value={topicModalData.cameraTopics.includes(selectedTopic) ? selectedTopic : ''}
                  onChange={(e) => { if (e.target.value) setSelectedTopic(e.target.value); }}
                  style={{ ...select, width: '100%' }}
                >
                  <option value="" disabled>{selectedTopic && !topicModalData.cameraTopics.includes(selectedTopic) ? selectedTopic : '-- 选择 Topic --'}</option>
                  {topicModalData.cameraTopics.map((t) => (
                    <option key={t} value={t}>{t.includes('fusion_map') ? '🗺️ ' : ''}{t}</option>
                  ))}
                </select>
              </div>
            ) : (
              <div>
                <label style={{ fontSize: 13, color: colors.textSecondary, fontWeight: 500, display: 'block', marginBottom: 6 }}>输入 Topic:</label>
                <input
                  type="text"
                  value={selectedTopic}
                  onChange={(e) => setSelectedTopic(e.target.value)}
                  placeholder="/camera/front_center"
                  style={input}
                />
              </div>
            )}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap' }}>
          {!isBev ? (
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: colors.textSecondary, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={forceH264}
                onChange={(e) => setForceH264(e.target.checked)}
                style={{ cursor: 'pointer' }}
              />
              ⚙️ 强制 H.264 转码
            </label>
          ) : <span />}
          <div style={{ display: 'flex', gap: 10 }}>
            <button onClick={onClose} style={btn.ghost(false)}>取消</button>
            {topicModalData.cameraTopics.length > 1 && (
              <button
                onClick={onOpenGrid}
                disabled={topicModalData.loading}
                style={btn.purple(topicModalData.loading)}
              >
                📷 多摄像头宫格
              </button>
            )}
            <button
              onClick={onConfirm}
              disabled={!selectedTopic || topicModalData.loading}
              style={btn.primary(!selectedTopic || topicModalData.loading)}
            >
              {isBev ? '🗺️ 打开 BEV 视图' : '确认提取'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
