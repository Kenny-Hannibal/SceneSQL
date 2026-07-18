import React, { useRef, useState, useEffect, useCallback } from 'react';

/**
 * MultiCameraPlayer — 多摄像头宫格播放器
 *
 * 改进：
 *   - Topic 多选（checkbox），动态宫格
 *   - 自适应宽度 + 纵向滚动条
 *   - 拖拽调整视频位置
 *   - 全局播放/暂停同步
 *   - 共享 Reader + 二进制复用协议 demux
 *
 * Props:
 *   allTopics: string[]     — 该 bag 所有可用的 camera topic
 *   initialTopics: string[] — 默认选中的 topic（空数组 = 不自动播放）
 *   bagPath: string
 *   mode: 'hevc'|'h264'
 *   startTs, endTs          — 时间范围 (ns)
 *   apiBase: string
 *   streamToken: string
 *   onClose: () => void
 */

const SPEEDS = [0.5, 1, 2, 4];
const COLORS = ['#ff4d4f','#1890ff','#52c41a','#faad14','#722ed1','#13c2c2','#eb2f96','#fa8c16'];

export default function MultiCameraPlayer({
  allTopics,
  initialTopics = [],
  bagPath,
  mode = 'hevc',
  startTs,
  endTs,
  apiBase,
  streamToken,
  onClose,
}) {
  // ── State ──
  const [selected, setSelected] = useState(initialTopics);  // 当前选中的 topic 列表
  const [order, setOrder] = useState(initialTopics);         // 视频格子的显示顺序（可拖拽改变）
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [error, setError] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState('idle'); // idle | connecting | streaming | done | error
  const [dragOverIdx, setDragOverIdx] = useState(null);

  // ── Refs ──
  const videoRefs = useRef({});       // topic → <video> element
  const mseStatesRef = useRef({});    // topic → { mediaSource, sourceBuffer, objectUrl, queue, isUpdating }
  const abortControllerRef = useRef(null);
  const dragSrcIdx = useRef(null);

  // ── Codec ──
  const hevcMime = 'video/mp4; codecs="hvc1.1.6.L120.B0"';
  const h264Mime = 'video/mp4; codecs="avc1.64001f"';
  let mimeCodec;
  if (mode === 'h264') {
    mimeCodec = h264Mime;
  } else {
    const supportsHevcMSE = typeof MediaSource !== 'undefined' && MediaSource.isTypeSupported(hevcMime);
    mimeCodec = supportsHevcMSE ? hevcMime : h264Mime;
  }
  const actualMode = mimeCodec === h264Mime ? 'h264' : 'hevc';

  // ── Grid 列数 ──
  const cols = order.length <= 1 ? 1 : order.length <= 4 ? 2 : order.length <= 9 ? 3 : 4;

  // ── Topic 名称简写 ──
  const shortName = (topic) => {
    const parts = topic.split('/');
    let name = parts[parts.length - 1] || topic;
    return name.replace('_encoded', '');
  };

  // ── Topic 多选 ──
  const toggleTopic = (topic) => {
    setSelected(prev => {
      const next = prev.includes(topic) ? prev.filter(t => t !== topic) : [...prev, topic];
      return next;
    });
  };

  // ── 开始/重启播放 ──
  const startStream = useCallback(() => {
    if (selected.length === 0) return;

    // 清理旧的
    cleanup();

    const topics = [...selected];
    setOrder(topics);
    setLoadingStatus('connecting');

    const controller = new AbortController();
    abortControllerRef.current = controller;

    // 延迟到 video 元素挂载后再初始化 MSE
    // 用 requestAnimationFrame 等一帧
    requestAnimationFrame(() => {
      initMSEAndStream(topics, controller);
    });
  }, [selected, bagPath, actualMode]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── 初始化 MSE + 启动流 ──
  const initMSEAndStream = async (topics, controller) => {
    // 1. 为每个 topic 创建 MediaSource + SourceBuffer
    const states = {};
    for (const topic of topics) {
      const video = videoRefs.current[topic];
      if (!video) {
        console.warn('[MultiCamera] video ref not ready for', topic);
        continue;
      }

      const mediaSource = new MediaSource();
      const objectUrl = URL.createObjectURL(mediaSource);
      video.src = objectUrl;

      states[topic] = {
        mediaSource,
        sourceBuffer: null,
        objectUrl,
        queue: [],
        isUpdating: false,
      };
    }
    mseStatesRef.current = states;

    // 2. 等待所有 MediaSource open → 创建 SourceBuffer
    const openPromises = topics.map(topic => {
      const state = states[topic];
      if (!state) return Promise.resolve();
      return new Promise(resolve => {
        state.mediaSource.addEventListener('sourceopen', () => {
          if (state.mediaSource.readyState === 'open' && !state.sourceBuffer) {
            state.sourceBuffer = state.mediaSource.addSourceBuffer(mimeCodec);
            state.sourceBuffer.mode = 'sequence';
          }
          resolve();
        }, { once: true });
      });
    });

    await Promise.all(openPromises);
    setLoadingStatus('streaming');

    // 3. 绑定 updateend 回调（处理队列）
    topics.forEach(topic => {
      const state = states[topic];
      if (!state?.sourceBuffer) return;
      state.sourceBuffer.addEventListener('updateend', () => {
        state.isUpdating = false;
        drainQueue(topic);
      });
      state.sourceBuffer.addEventListener('error', () => {
        state.isUpdating = false;
      });
    });

    // 4. Fetch /stream-multi + demux
    const params = new URLSearchParams({
      bag_path: bagPath,
      topics: topics.join(','),
      mode: actualMode,
    });
    if (startTs != null) params.append('start_ts', String(startTs));
    if (endTs != null) params.append('end_ts', String(endTs));
    if (streamToken) params.append('token', streamToken);

    const streamUrl = `${apiBase}/api/video/stream-multi?${params.toString()}`;

    try {
      const response = await fetch(streamUrl, { signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body.getReader();
      let buffer = new Uint8Array(0);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        // 合并到 buffer
        const newBuf = new Uint8Array(buffer.length + value.length);
        newBuf.set(buffer);
        newBuf.set(value, buffer.length);
        buffer = newBuf;

        // 解析复用帧: [topic_idx:1byte][data_len:4bytes LE][data]
        let offset = 0;
        while (offset + 5 <= buffer.length) {
          const topicIdx = buffer[offset];
          const dataLen = new DataView(buffer.buffer, buffer.byteOffset + offset + 1, 4).getUint32(0, true);

          if (topicIdx >= topics.length || dataLen > 50 * 1024 * 1024) {
            // 协议错位，跳过1字节重新同步
            offset += 1;
            continue;
          }

          if (offset + 5 + dataLen > buffer.length) break; // 不完整，等更多数据

          const data = buffer.slice(offset + 5, offset + 5 + dataLen);
          const topic = topics[topicIdx];
          enqueueData(topic, data);

          offset += 5 + dataLen;
        }

        // 移除已消费的数据
        if (offset > 0) {
          buffer = buffer.slice(offset);
        }
      }

      setLoadingStatus('done');
    } catch (e) {
      if (e.name === 'AbortError') return;
      console.error('[MultiCamera] Stream error:', e);
      setError(e.message);
      setLoadingStatus('error');
    }
  };

  // ── 向某 topic 的 SourceBuffer 队列推入数据 ──
  const enqueueData = (topic, data) => {
    const state = mseStatesRef.current[topic];
    if (!state?.sourceBuffer) return;
    state.queue.push(data);
    drainQueue(topic);
  };

  // ── 消费某 topic 的 SourceBuffer 队列 ──
  const drainQueue = (topic) => {
    const state = mseStatesRef.current[topic];
    if (!state || !state.sourceBuffer || state.isUpdating || state.queue.length === 0) return;
    const chunk = state.queue.shift();
    try {
      state.sourceBuffer.appendBuffer(chunk);
      state.isUpdating = true;
    } catch (e) {
      console.error(`[MultiCamera] appendBuffer error (${shortName(topic)}):`, e);
      state.queue.unshift(chunk); // 放回队列重试
    }
  };

  // ── 清理 MSE ──
  const cleanup = useCallback(() => {
    Object.values(mseStatesRef.current).forEach(state => {
      if (!state) return;
      try { state.sourceBuffer?.abort(); } catch (e) {}
      try { if (state.mediaSource?.readyState === 'open') state.mediaSource.endOfStream(); } catch (e) {}
      try { if (state.objectUrl) URL.revokeObjectURL(state.objectUrl); } catch (e) {}
    });
    mseStatesRef.current = {};
    // 清除所有 video src
    Object.values(videoRefs.current).forEach(video => {
      try { video?.pause(); } catch (e) {}
      try { video?.removeAttribute('src'); video?.load(); } catch (e) {}
    });
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setLoadingStatus('idle');
    setIsPlaying(false);
    setError(null);
  }, []);

  // ── 播放/暂停（全局同步） ──
  const togglePlay = useCallback(() => {
    const videos = order.map(t => videoRefs.current[t]).filter(Boolean);
    const anyPlaying = videos.some(v => !v.paused);
    videos.forEach(v => {
      if (anyPlaying) { v.pause(); } else { v.play().catch(() => {}); }
    });
    setIsPlaying(!anyPlaying);
  }, [order]);

  // ── 速度同步 ──
  useEffect(() => {
    order.forEach(topic => {
      const v = videoRefs.current[topic];
      if (v) v.playbackRate = speed;
    });
  }, [speed, order]);

  // ── 拖拽：开始 ──
  const handleDragStart = (e, idx) => {
    dragSrcIdx.current = idx;
    e.dataTransfer.effectAllowed = 'move';
    // 设一个透明的拖拽图片避免浏览器默认快照
    const img = new Image();
    img.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAUEBAAAACwAAAAAAQABAAACAkQBADs=';
    e.dataTransfer.setDragImage(img, 0, 0);
  };

  // ── 拖拽：经过 ──
  const handleDragOver = (e, idx) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverIdx(idx);
  };

  // ── 拖拽：放下 ──
  const handleDrop = (e, idx) => {
    e.preventDefault();
    const from = dragSrcIdx.current;
    if (from !== null && from !== idx) {
      setOrder(prev => {
        const next = [...prev];
        [next[from], next[idx]] = [next[idx], next[from]];
        return next;
      });
    }
    setDragOverIdx(null);
    dragSrcIdx.current = null;
  };

  // ── 组件卸载时清理 ──
  useEffect(() => {
    return () => cleanup();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── 渲染 ──
  return (
    <div style={{
      position: 'fixed', inset: 0,
      background: '#0d1117', color: '#e6edf3',
      display: 'flex', flexDirection: 'column',
      zIndex: 9999, fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    }}>
      {/* ═══ 顶部工具栏 ═══ */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 16px', background: '#161b22',
        borderBottom: '1px solid #30363d', flexShrink: 0,
      }}>
        <button onClick={() => { cleanup(); onClose(); }} style={{
          padding: '4px 10px', background: '#21262d', color: '#e6edf3',
          border: '1px solid #30363d', borderRadius: 6, cursor: 'pointer', fontSize: 13,
        }}>
          ← 返回
        </button>
        <span style={{ fontSize: 15, fontWeight: 600 }}>📷 多摄像头宫格</span>
        <span style={{ fontSize: 12, color: '#8b949e' }}>
          {order.length} 路 · {actualMode.toUpperCase()} · {
            loadingStatus === 'streaming' ? '🟢 推流中' :
            loadingStatus === 'done' ? '✅ 完成' :
            loadingStatus === 'error' ? '❌ 错误' :
            loadingStatus === 'connecting' ? '⏳ 连接中...' : '⏹ 停止'
          }
        </span>

        <div style={{ flex: 1 }} />

        {loadingStatus === 'streaming' && (
          <>
            <button onClick={togglePlay} style={{
              padding: '4px 12px', background: isPlaying ? '#da3633' : '#238636',
              color: '#fff', border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13,
            }}>
              {isPlaying ? '⏸ 暂停' : '▶ 播放'}
            </button>
            {SPEEDS.map(s => (
              <button key={s} onClick={() => setSpeed(s)} style={{
                padding: '3px 8px', fontSize: 12, borderRadius: 4, border: '1px solid #30363d',
                background: speed === s ? '#1f6feb' : '#21262d',
                color: speed === s ? '#fff' : '#8b949e', cursor: 'pointer',
              }}>
                {s}x
              </button>
            ))}
          </>
        )}

        <button onClick={startStream} disabled={selected.length === 0 || loadingStatus === 'streaming'} style={{
          padding: '4px 12px', background: selected.length === 0 || loadingStatus === 'streaming' ? '#21262d' : '#1f6feb',
          color: selected.length === 0 ? '#484f58' : '#fff',
          border: '1px solid #30363d', borderRadius: 6, cursor: 'pointer', fontSize: 13,
        }}>
          {loadingStatus === 'streaming' ? '⏹ 停止并重选' : '▶ 开始播放'}
        </button>
      </div>

      {/* ═══ Topic 选择栏 ═══ */}
      <div style={{
        padding: '6px 16px', background: '#0d1117',
        borderBottom: '1px solid #30363d', flexShrink: 0,
        maxHeight: 120, overflowY: 'auto',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
          <span style={{ fontSize: 12, color: '#8b949e' }}>选择摄像头：</span>
          <button onClick={() => setSelected([...allTopics])} style={{
            fontSize: 11, padding: '1px 6px', background: '#21262d', color: '#8b949e',
            border: '1px solid #30363d', borderRadius: 3, cursor: 'pointer',
          }}>全选</button>
          <button onClick={() => setSelected([])} style={{
            fontSize: 11, padding: '1px 6px', background: '#21262d', color: '#8b949e',
            border: '1px solid #30363d', borderRadius: 3, cursor: 'pointer',
          }}>清空</button>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {allTopics.map(topic => (
            <label key={topic} style={{
              display: 'flex', alignItems: 'center', gap: 4,
              cursor: 'pointer', fontSize: 12,
              padding: '2px 8px', borderRadius: 4,
              background: selected.includes(topic) ? '#1f6feb33' : 'transparent',
              border: `1px solid ${selected.includes(topic) ? '#1f6feb' : '#30363d'}`,
              transition: 'all 0.15s',
            }}>
              <input
                type="checkbox"
                checked={selected.includes(topic)}
                onChange={() => toggleTopic(topic)}
                style={{ margin: 0, accentColor: '#1f6feb' }}
              />
              <span style={{ color: selected.includes(topic) ? '#e6edf3' : '#8b949e' }}>
                {shortName(topic)}
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* ═══ 错误提示 ═══ */}
      {error && (
        <div style={{ padding: '8px 16px', background: '#490202', color: '#ff7b72', fontSize: 13 }}>
          ❌ {error}
        </div>
      )}

      {/* ═══ 视频宫格（核心区域，可滚动） ═══ */}
      <div style={{
        flex: 1, overflowY: 'auto', overflowX: 'hidden',
        padding: 4,
      }}>
        {order.length === 0 ? (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            height: '100%', color: '#484f58', fontSize: 16,
          }}>
            ↑ 选择摄像头后点击「开始播放」
          </div>
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: `repeat(${cols}, 1fr)`,
            gap: 4,
            minHeight: 'min-content',
          }}>
            {order.map((topic, idx) => (
              <div
                key={topic}
                draggable
                onDragStart={e => handleDragStart(e, idx)}
                onDragOver={e => handleDragOver(e, idx)}
                onDrop={e => handleDrop(e, idx)}
                onDragLeave={() => setDragOverIdx(null)}
                onDragEnd={() => { setDragOverIdx(null); dragSrcIdx.current = null; }}
                style={{
                  position: 'relative', background: '#000',
                  borderRadius: 4, overflow: 'hidden',
                  border: dragOverIdx === idx ? '2px solid #58a6ff' : '2px solid transparent',
                  aspectRatio: '16/9',
                  cursor: 'grab',
                  transition: 'border-color 0.2s',
                }}
              >
                <video
                  ref={el => { videoRefs.current[topic] = el; }}
                  muted
                  playsInline
                  style={{
                    width: '100%', height: '100%', objectFit: 'contain',
                    display: 'block',
                  }}
                />
                {/* topic 标签 */}
                <div style={{
                  position: 'absolute', top: 4, left: 4,
                  background: COLORS[idx % COLORS.length],
                  padding: '2px 8px', borderRadius: 3,
                  fontSize: 11, fontWeight: 'bold', color: '#fff',
                  textShadow: '0 1px 2px rgba(0,0,0,0.8)',
                  pointerEvents: 'none',
                }}>
                  {shortName(topic)}
                </div>
                {/* 拖拽把手 */}
                <div style={{
                  position: 'absolute', top: 4, right: 4,
                  color: 'rgba(255,255,255,0.25)', fontSize: 14,
                  pointerEvents: 'none', userSelect: 'none',
                }}>
                  ⠿
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
