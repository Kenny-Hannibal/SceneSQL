import React, { useState, useEffect, useRef } from 'react';
import { colors, radius, badge, zIndex } from '../../theme';
import { API_BASE } from '../../api';
import BevViewer from '../BevViewer';
import MultiVideoGrid from '../MultiVideoGrid';
import { useMseStream } from './useMseStream';

// ── 视频播放弹窗 ──
// 三种内容形态：单 topic MSE 视频 / 单 topic BEV（BevViewer compact）/ 多摄像头宫格（MultiVideoGrid）。
// 自带：多视图 Tab 栏、BEV 播放控件、全屏切换、通过/不通过标注按钮、MSE 播放 hook。
export default function PlayerModal({
  playerData,
  playerMode,
  playerError, setPlayerError,
  playerGridMode, setPlayerGridMode,
  playerGridTopics, setPlayerGridTopics,
  onSwitchTopic,
  rowLabel, matchedStrategy, onLabel,
  onClose,
  onRetryH264,
  streamAbortRef, mseCleanupRef,
}) {
  const videoRef = useRef(null);
  const bevViewerRef = useRef(null);
  const [bevProgress, setBevProgress] = useState({ current: 0, playing: false });
  const [isFullscreen, setIsFullscreen] = useState(false);
  const playerContentRef = useRef(null);

  // MSE 流式播放（单 topic 视频模式）
  useMseStream({
    videoRef,
    active: !!playerData?.use_mse && !playerGridMode,
    streamUrl: playerData?.stream_url,
    codec: playerData?.mse_codec,
    durationSec: playerData?.durationSec,
    onError: setPlayerError,
    streamAbortRef,
    mseCleanupRef,
  });

  // BEV 进度轮询（单topic BEV模式下，每200ms从ref读取进度）
  useEffect(() => {
    if (!playerData?.is_bev || playerGridMode || !bevViewerRef.current) return;
    const tick = setInterval(() => {
      try {
        const p = bevViewerRef.current.getProgress();
        if (p) setBevProgress(p);
      } catch (e) {}
    }, 200);
    return () => clearInterval(tick);
  }, [playerData?.is_bev, playerGridMode]);

  // 全屏切换
  const toggleFullscreen = () => {
    if (!playerContentRef.current) return;
    if (!document.fullscreenElement) {
      playerContentRef.current.requestFullscreen().then(() => setIsFullscreen(true)).catch(() => {});
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false)).catch(() => {});
    }
  };
  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', handler);
    return () => document.removeEventListener('fullscreenchange', handler);
  }, []);

  if (!playerData) return null;

  const meta = playerData._multiViewMeta;
  const showTabs = meta?.cameraTopics?.length > 1;

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.75)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: zIndex.player,
    }}>
      <div style={{ background: '#000', borderRadius: radius.lg, padding: 16, maxWidth: '92vw', maxHeight: '92vh', display: 'flex', flexDirection: 'column' }}>
        {/* ── 标题栏 ── */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 12, flexWrap: 'wrap' }}>
          <span style={{ color: '#fff', fontSize: 14, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            📹 {playerData.row.bag_id || '未知'} | {playerGridMode ? '📷 宫格模式' : (playerData.is_bev ? '🗺️ ' + (playerData.topic?.split('/').pop() || playerData.topic) : playerData.topic)}
            {playerMode && (
              <span style={badge(playerMode === 'hevc-stream' ? colors.success : colors.orange)}>
                {playerMode === 'hevc-stream' ? 'HEVC 直传' : 'H.264 转码'}
              </span>
            )}
          </span>
          <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            {rowLabel && (
              <span style={badge(rowLabel === 'pass' ? colors.success : colors.error)}>
                {rowLabel === 'pass' ? '✅ 已通过' : '❌ 不通过'}
              </span>
            )}
            <button
              onClick={() => onLabel('pass')}
              title={matchedStrategy ? `标注通过 → 策略「${matchedStrategy.name}」` : '当前 SQL 未保存为策略，点击后先保存策略'}
              style={{
                padding: '4px 10px', fontSize: 12, borderRadius: radius.sm, cursor: 'pointer',
                border: `1px solid ${rowLabel === 'pass' ? colors.success : '#555'}`,
                background: rowLabel === 'pass' ? colors.success : 'transparent', color: '#fff',
              }}
            >
              ✅ 通过
            </button>
            <button
              onClick={() => onLabel('fail')}
              title={matchedStrategy ? `标注不通过 → 策略「${matchedStrategy.name}」` : '当前 SQL 未保存为策略，点击后先保存策略'}
              style={{
                padding: '4px 10px', fontSize: 12, borderRadius: radius.sm, cursor: 'pointer',
                border: `1px solid ${rowLabel === 'fail' ? colors.error : '#555'}`,
                background: rowLabel === 'fail' ? colors.error : 'transparent', color: '#fff',
              }}
            >
              ❌ 不通过
            </button>
            <button
              onClick={onClose}
              style={{
                padding: '4px 12px', fontSize: 13, borderRadius: radius.sm, cursor: 'pointer',
                border: '1px solid #555', background: 'transparent', color: '#fff',
              }}
            >
              ✕ 关闭
            </button>
          </span>
        </div>

        {/* ── 多视图 Tab 栏 ── */}
        {showTabs && (
          <div style={{ display: 'flex', gap: 4, marginBottom: 8, flexWrap: 'wrap', background: '#1a1a1a', padding: '6px 8px', borderRadius: radius.sm, alignItems: 'center' }}>
            <button
              onClick={() => {
                if (playerGridMode) {
                  setPlayerGridMode(false);
                  const topic = playerGridTopics.length > 0 ? playerGridTopics[0] : meta.cameraTopics[0];
                  onSwitchTopic(topic);
                } else {
                  setPlayerGridMode(true);
                  setPlayerGridTopics(meta.cameraTopics);
                }
              }}
              style={{
                padding: '4px 10px', fontSize: 12, borderRadius: 3, fontWeight: 'bold', marginRight: 4,
                border: `1px solid ${playerGridMode ? colors.purple : '#555'}`,
                background: playerGridMode ? colors.purple : 'transparent',
                color: playerGridMode ? '#fff' : '#aaa', cursor: 'pointer',
              }}
            >
              📷 宫格
            </button>
            <span style={{ color: '#666', fontSize: 11, marginRight: 4 }}>|</span>
            {meta.cameraTopics.map((t) => {
              const shortName = t.split('/').pop() || t;
              if (playerGridMode) {
                const checked = playerGridTopics.includes(t);
                return (
                  <button
                    key={t}
                    onClick={() => setPlayerGridTopics((prev) => checked ? prev.filter((x) => x !== t) : [...prev, t])}
                    style={{
                      padding: '4px 10px', fontSize: 12, borderRadius: 3,
                      border: `1px solid ${checked ? colors.primary : '#555'}`,
                      background: checked ? colors.primary : 'transparent',
                      color: checked ? '#fff' : '#aaa', cursor: 'pointer', transition: 'all 0.2s',
                      display: 'flex', alignItems: 'center', gap: 3,
                    }}
                    title={t}
                  >
                    {checked ? '☑' : '☐'} {shortName}
                  </button>
                );
              }
              const active = t === playerData.topic;
              return (
                <button
                  key={t}
                  onClick={() => onSwitchTopic(t)}
                  style={{
                    padding: '4px 12px', fontSize: 12, borderRadius: 3,
                    border: `1px solid ${active ? colors.primary : '#555'}`,
                    background: active ? colors.primary : 'transparent',
                    color: active ? '#fff' : '#aaa', cursor: active ? 'default' : 'pointer', transition: 'all 0.2s',
                  }}
                  title={t}
                >
                  {shortName}
                </button>
              );
            })}
          </div>
        )}

        {/* ── 播放失败提示 ── */}
        {playerError && (
          <div style={{
            background: '#fff2f0', border: '1px solid #ffccc7', color: '#cf1322',
            padding: 12, borderRadius: radius.md, marginBottom: 10, fontSize: 13,
          }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>⚠️ 播放失败</div>
            <div>{playerError}</div>
            <button
              onClick={onRetryH264}
              style={{
                marginTop: 8, padding: '5px 14px', fontSize: 12, borderRadius: radius.sm,
                border: '1px solid #cf1322', background: '#fff', color: '#cf1322', cursor: 'pointer',
              }}
            >
              🔄 改用 H.264 转码重试
            </button>
          </div>
        )}

        {/* ── 内容区域 ── */}
        <div ref={playerContentRef} style={{ position: 'relative', display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
          {playerGridMode && playerGridTopics.length > 1 ? (
            <MultiVideoGrid
              topics={playerGridTopics}
              bagPath={meta?.bagPath}
              emBinPath={meta?.emBinPath}
              startTs={meta?.startTs}
              endTs={meta?.endTs}
              mode={playerData.mse_codec?.includes('hvc1') ? 'hevc' : 'h264'}
              apiBase={API_BASE}
              streamToken={localStorage.getItem('token')}
            />
          ) : playerData.is_bev ? (
            <>
              <div style={{ flex: 1, position: 'relative', minHeight: 400, background: '#0a0a1a', borderRadius: radius.sm, overflow: 'hidden' }}>
                <BevViewer
                  ref={bevViewerRef}
                  compact
                  bagPath={meta?.emBinPath || meta?.bagPath}
                  startTsNs={meta?.startTs}
                  endTsNs={meta?.endTs}
                />
              </div>
              {/* BEV 共享控件 */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0', flexShrink: 0 }}>
                <button
                  onClick={() => bevViewerRef.current?.togglePlay()}
                  style={{
                    padding: '4px 12px', fontSize: 12, borderRadius: 3, border: 'none',
                    background: bevProgress.playing ? '#da3633' : '#238636', color: '#fff', cursor: 'pointer',
                  }}
                >
                  {bevProgress.playing ? '⏸ 暂停' : '▶ 播放'}
                </button>
                <div
                  onClick={(e) => {
                    const rect = e.currentTarget.getBoundingClientRect();
                    const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                    bevViewerRef.current?.seekToRatio(ratio);
                  }}
                  style={{ flex: 1, height: 6, background: '#333', borderRadius: 3, cursor: 'pointer', position: 'relative' }}
                >
                  <div style={{ width: `${(bevProgress.current || 0) * 100}%`, height: '100%', background: colors.primary, borderRadius: 3 }} />
                  <div style={{
                    position: 'absolute', left: `${(bevProgress.current || 0) * 100}%`, top: '50%', transform: 'translate(-50%,-50%)',
                    width: 10, height: 10, borderRadius: '50%', background: colors.primary, border: '2px solid #fff',
                    pointerEvents: 'none', boxShadow: '0 0 3px rgba(0,0,0,0.5)',
                  }} />
                </div>
                <span style={{ color: '#aaa', fontSize: 11, minWidth: 60, textAlign: 'right' }}>
                  {bevProgress.frameIdx != null ? `${bevProgress.frameIdx + 1}/${bevProgress.totalFrames || 0}` : '--'}
                </span>
                <button
                  onClick={toggleFullscreen}
                  style={{
                    padding: '2px 8px', fontSize: 12, borderRadius: 3, border: '1px solid #555',
                    background: 'transparent', color: '#ccc', cursor: 'pointer',
                  }}
                >
                  {isFullscreen ? '⤓ 退出全屏' : '⤢ 全屏'}
                </button>
              </div>
            </>
          ) : playerData.video_url ? (
            <video
              src={playerData.video_url}
              controls
              autoPlay
              style={{ maxWidth: '85vw', maxHeight: '80vh', borderRadius: radius.sm }}
            />
          ) : (
            <video
              ref={videoRef}
              controls
              autoPlay
              style={{ maxWidth: '85vw', maxHeight: '80vh', borderRadius: radius.sm }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
