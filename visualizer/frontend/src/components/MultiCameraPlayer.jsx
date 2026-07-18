import React, { useRef, useState, useEffect, useCallback } from 'react';

/**
 * MultiCameraPlayer — 多摄像头宫格 + 共享Reader流式播放
 *
 * Props:
 *   topics: string[]        — camera topic 名称列表
 *   bagPath: string         — rosbag 路径
 *   mode: 'hevc'|'h264'    — 流模式
 *   startTs, endTs          — 时间范围 (ns)
 *   durationSec: number     — 预期时长
 *   apiBase: string         — API 前缀
 *   streamToken: string     — auth token
 *   onClose: () => void
 */

const SPEEDS = [0.5, 1, 2, 4];

export default function MultiCameraPlayer({
  topics,
  bagPath,
  mode = 'hevc',
  startTs,
  endTs,
  durationSec,
  apiBase,
  streamToken,
  onClose,
}) {
  // ── 为每个 topic 维护独立的 video ref ──
  const videoRefs = useRef(topics.map(() => React.createRef()));
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [error, setError] = useState(null);
  const [loadingStatus, setLoadingStatus] = useState('connecting'); // connecting | streaming | done | error
  const abortControllerRef = useRef(null);
  const mseStatesRef = useRef([]); // 每个 topic 的 { mediaSource, sourceBuffer, objectUrl, queue, isUpdating }

  // 格子布局: 2列
  const cols = 2;
  const rows = Math.ceil(topics.length / cols);

  // ── codec 选择 ──
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

  // ── 清理函数 ──
  const cleanup = useCallback(() => {
    mseStatesRef.current.forEach((state, i) => {
      if (!state) return;
      const video = videoRefs.current[i]?.current;
      try { video?.pause(); } catch (e) {}
      try { video?.removeAttribute('src'); video?.load(); } catch (e) {}
      try { if (state.objectUrl) URL.revokeObjectURL(state.objectUrl); } catch (e) {}
      try { state.sourceBuffer?.abort(); } catch (e) {}
      try { if (state.mediaSource?.readyState === 'open') state.mediaSource.endOfStream(); } catch (e) {}
    });
    mseStatesRef.current = [];
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
  }, []);

  // ── 播放/暂停（全局同步） ──
  const togglePlay = () => {
    const anyPlaying = videoRefs.current.some(ref => ref.current && !ref.current.paused);
    videoRefs.current.forEach(ref => {
      const v = ref.current;
      if (!v) return;
      if (anyPlaying) { v.pause(); } else { v.play().catch(() => {}); }
    });
    setIsPlaying(!anyPlaying);
  };

  // 速度同步
  useEffect(() => {
    videoRefs.current.forEach(ref => {
      if (ref.current) ref.current.playbackRate = speed;
    });
  }, [speed]);

  // ── 核心逻辑：fetch /stream-multi + demux + N路MSE ──
  useEffect(() => {
    if (topics.length === 0) return;

    const controller = new AbortController();
    abortControllerRef.current = controller;

    // 1. 为每个 topic 创建 MediaSource + SourceBuffer
    const states = topics.map((topic, i) => {
      const video = videoRefs.current[i]?.current;
      if (!video) return null;

      const mediaSource = new MediaSource();
      const objectUrl = URL.createObjectURL(mediaSource);
      video.src = objectUrl;

      return {
        mediaSource,
        sourceBuffer: null,
        objectUrl,
        queue: [],
        isUpdating: false,
        topic,
      };
    });
    mseStatesRef.current = states;

    // 2. 等待所有 MediaSource open，然后创建 SourceBuffer
    let allReady = false;

    const openPromises = states.map((state, i) => {
      if (!state) return Promise.resolve();
      return new Promise((resolve) => {
        state.mediaSource.addEventListener('sourceopen', () => {
          const video = videoRefs.current[i]?.current;
          if (state.mediaSource.readyState === 'open' && !state.sourceBuffer) {
            state.sourceBuffer = state.mediaSource.addSourceBuffer(mimeCodec);
          }
          resolve();
        }, { once: true });
      });
    });

    (async () => {
      await Promise.all(openPromises);
      allReady = true;
      setLoadingStatus('streaming');

      // 3. 构造 /stream-multi URL
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
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const reader = response.body.getReader();
        let buffer = new Uint8Array(0);

        // ── demux + append 逻辑 ──
        const processQueue = (idx) => {
          const state = states[idx];
          if (!state || !state.sourceBuffer) return;
          if (state.isUpdating || state.queue.length === 0) return;
          const chunk = state.queue.shift();
          try {
            state.sourceBuffer.appendBuffer(chunk);
            state.isUpdating = true;
          } catch (e) {
            console.error(`[MultiCamera] appendBuffer error for topic ${idx}:`, e);
          }
        };

        // 为每个 sourceBuffer 绑定 updateend
        states.forEach((state, idx) => {
          if (!state?.sourceBuffer) return;
          state.sourceBuffer.addEventListener('updateend', () => {
            state.isUpdating = false;
            if (state.queue.length === 0) {
              // 当前 topic 数据已全部喂入
            } else {
              processQueue(idx);
            }
          });
        });

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
            if (offset + 5 + dataLen > buffer.length) break; // 不完整，等更多数据

            const data = buffer.slice(offset + 5, offset + 5 + dataLen);

            if (topicIdx < topics.length && states[topicIdx]) {
              states[topicIdx].queue.push(data);
              processQueue(topicIdx);
            }

            offset += 5 + dataLen;
          }

          // 消费过的部分移除
          if (offset > 0) {
            buffer = buffer.slice(offset);
          }
        }

        // 流结束，所有剩余数据喂入
        setLoadingStatus('done');
      } catch (e) {
        if (e.name === 'AbortError') return;
        console.error('[MultiCamera] Stream error:', e);
        setError(e.message);
        setLoadingStatus('error');
      }
    })();

    return () => {
      cleanup();
    };
  }, [topics, bagPath, actualMode]); // eslint-disable-line react-hooks/exhaustive-deps

  // 主题简称（取最后一段）
  const shortName = (topic) => {
    const parts = topic.split('/');
    return parts[parts.length - 1] || topic;
  };

  // 颜色标签
  const COLORS = ['#ff4d4f', '#1890ff', '#52c41a', '#faad14', '#722ed1', '#13c2c2', '#eb2f96', '#fa8c16'];

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.85)', zIndex: 9999,
      display: 'flex', flexDirection: 'column', color: '#fff',
    }}>
      {/* 标题栏 */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 16px', background: '#1a1a2e', borderBottom: '1px solid #333',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 16, fontWeight: 'bold' }}>📷 多摄像头宫格</span>
          <span style={{ fontSize: 12, color: '#999' }}>
            {topics.length} 路 · {actualMode.toUpperCase()} · {loadingStatus === 'streaming' ? '🟢 推流中' : loadingStatus === 'done' ? '✅ 完成' : loadingStatus === 'error' ? '❌ 错误' : '⏳ 连接中...'}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button onClick={togglePlay} style={{
            padding: '4px 12px', background: isPlaying ? '#ff4d4f' : '#52c41a',
            color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer',
          }}>
            {isPlaying ? '⏸ 暂停' : '▶ 播放'}
          </button>
          {SPEEDS.map(s => (
            <button key={s} onClick={() => setSpeed(s)} style={{
              padding: '3px 8px', fontSize: 12, borderRadius: 3, border: 'none',
              background: speed === s ? '#1890ff' : '#444', color: speed === s ? '#fff' : '#ccc', cursor: 'pointer',
            }}>
              {s}x
            </button>
          ))}
          <button onClick={() => { cleanup(); onClose(); }} style={{
            padding: '4px 12px', background: '#666', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer',
          }}>
            ✕ 关闭
          </button>
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div style={{ padding: '8px 16px', background: '#440000', color: '#ff4d4f', fontSize: 13 }}>
          ❌ {error}
        </div>
      )}

      {/* 宫格视频区 */}
      <div style={{
        flex: 1, display: 'grid',
        gridTemplateColumns: `repeat(${cols}, 1fr)`,
        gridTemplateRows: `repeat(${rows}, 1fr)`,
        gap: 2, padding: 2,
      }}>
        {topics.map((topic, i) => (
          <div key={topic} style={{
            position: 'relative', background: '#000',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            overflow: 'hidden',
          }}>
            <video
              ref={videoRefs.current[i]}
              style={{ width: '100%', height: '100%', objectFit: 'contain' }}
              muted
              playsInline
            />
            {/* topic 标签 */}
            <div style={{
              position: 'absolute', top: 4, left: 4,
              background: COLORS[i % COLORS.length],
              padding: '2px 8px', borderRadius: 3,
              fontSize: 11, fontWeight: 'bold', color: '#fff',
              textShadow: '0 1px 2px rgba(0,0,0,0.8)',
            }}>
              {shortName(topic)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
