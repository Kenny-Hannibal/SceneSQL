import React, { useRef, useState, useEffect, useCallback } from 'react';

/**
 * MultiVideoGrid — 宫格模式：N个video同时播放
 *
 * 核心逻辑：
 *   - 使用 /stream-multi 端点（共享Reader + N路ffmpeg + 复用协议）
 *   - 二进制demux：[topic_idx:1B][data_len:4B LE][fMP4_data]
 *   - 每个topic独立的MediaSource + SourceBuffer
 *   - 全局播放/暂停/倍速同步
 *   - 进度条：用 buffered.end() 作为总时长（MSE流 duration=Infinity）
 *   - 所有topic都有数据后才一起播放
 */

const COLORS = ['#ff4d4f','#1890ff','#52c41a','#faad14','#722ed1','#13c2c2','#eb2f96','#fa8c16'];

export default function MultiVideoGrid({ topics, bagPath, startTs, endTs, mode, apiBase, streamToken }) {
  const [loadingStatus, setLoadingStatus] = useState('idle');
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [error, setError] = useState(null);
  const [dragOverIdx, setDragOverIdx] = useState(null);

  const videoRefs = useRef({});
  const mseStatesRef = useRef({});
  const abortRef = useRef(null);
  const dragSrcIdx = useRef(null);
  const containerRef = useRef(null);
  const [displayOrder, setDisplayOrder] = useState(topics);
  const [currentTime, setCurrentTime] = useState(0);
  const [bufferedEnd, setBufferedEnd] = useState(0);  // 用bufferedEnd替代duration
  const [isFullscreen, setIsFullscreen] = useState(false);

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

  const cols = displayOrder.length <= 1 ? 1 : displayOrder.length <= 4 ? 2 : displayOrder.length <= 9 ? 3 : 4;

  const shortName = (t) => (t.split('/').pop() || t).replace('_encoded', '');

  // ── 进度条：MSE流 duration=Infinity，改用 buffered.end() ──
  useEffect(() => {
    const tick = setInterval(() => {
      const firstTopic = displayOrder[0];
      const v = videoRefs.current[firstTopic];
      if (!v) return;
      // currentTime 直接读
      if (v.currentTime && isFinite(v.currentTime)) {
        setCurrentTime(v.currentTime);
      }
      // 用 buffered 范围作为"总时长"
      try {
        if (v.buffered && v.buffered.length > 0) {
          const end = v.buffered.end(v.buffered.length - 1);
          if (isFinite(end) && end > 0) {
            setBufferedEnd(end);
          }
        }
      } catch (e) {}
    }, 300);
    return () => clearInterval(tick);
  }, [displayOrder]);

  // ── Seek（所有视频同步跳转，seek后强制暂停） ──
  const seekTo = (ratio) => {
    if (!bufferedEnd) return;
    const t = ratio * bufferedEnd;
    displayOrder.forEach(topic => {
      const v = videoRefs.current[topic];
      if (v) {
        try {
          if (v.buffered && v.buffered.length > 0) {
            const maxTime = v.buffered.end(v.buffered.length - 1);
            v.currentTime = Math.min(t, maxTime);
          }
        } catch (e) {}
        // seek后浏览器可能自动恢复播放，强制暂停
        try { v.pause(); } catch (e) {}
      }
    });
    setCurrentTime(t);
    setIsPlaying(false);
  };

  // ── 全屏 ──
  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (!document.fullscreenElement) {
      containerRef.current.requestFullscreen().then(() => setIsFullscreen(true)).catch(() => {});
    } else {
      document.exitFullscreen().then(() => setIsFullscreen(false)).catch(() => {});
    }
  };
  useEffect(() => {
    const handler = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener('fullscreenchange', handler);
    return () => document.removeEventListener('fullscreenchange', handler);
  }, []);

  // ── 格式化时间 ──
  const fmtTime = (s) => {
    if (!s || !isFinite(s)) return '00:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  };

  // ── 清理 MSE ──
  const cleanup = useCallback(() => {
    Object.values(mseStatesRef.current).forEach(s => {
      if (!s) return;
      try { s.sourceBuffer?.abort(); } catch (e) {}
      try { if (s.mediaSource?.readyState === 'open') s.mediaSource.endOfStream(); } catch (e) {}
      try { if (s.objectUrl) URL.revokeObjectURL(s.objectUrl); } catch (e) {}
    });
    mseStatesRef.current = {};
    Object.values(videoRefs.current).forEach(v => {
      try { v?.pause(); } catch (e) {}
      try { v?.removeAttribute('src'); v?.load(); } catch (e) {}
    });
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
    setLoadingStatus('idle');
    setIsPlaying(false);
    setError(null);
    setCurrentTime(0);
    setBufferedEnd(0);
  }, []);

  // ── SourceBuffer 队列 ──
  const drainQueue = (topic) => {
    const s = mseStatesRef.current[topic];
    if (!s || !s.sourceBuffer || s.isUpdating || s.queue.length === 0) return;
    const chunk = s.queue.shift();
    try {
      s.sourceBuffer.appendBuffer(chunk);
      s.isUpdating = true;
    } catch (e) {
      console.error('[MultiVideoGrid] appendBuffer error:', e);
      s.queue.unshift(chunk);
    }
  };

  const enqueueData = (topic, data) => {
    const s = mseStatesRef.current[topic];
    if (!s?.sourceBuffer) return;
    s.queue.push(data);
    drainQueue(topic);
  };

  // ── 启动流 ──
  const startStream = useCallback(() => {
    if (displayOrder.length === 0) return;
    cleanup();
    setLoadingStatus('connecting');

    const controller = new AbortController();
    abortRef.current = controller;

    // 延迟1帧确保video元素已挂载
    requestAnimationFrame(() => {
      initMSEAndStream(displayOrder, controller);
    });
  }, [displayOrder, cleanup]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── 初始化MSE + 启动流 ──
  const initMSEAndStream = async (topicList, controller) => {
    const states = {};
    for (const topic of topicList) {
      const video = videoRefs.current[topic];
      if (!video) { console.warn('[MultiVideoGrid] no video ref for', topic); continue; }
      const ms = new MediaSource();
      const url = URL.createObjectURL(ms);
      video.src = url;
      states[topic] = { mediaSource: ms, sourceBuffer: null, objectUrl: url, queue: [], isUpdating: false, hasData: false };
    }
    mseStatesRef.current = states;

    // 等待 MediaSource open
    await Promise.all(topicList.map(topic => {
      const s = states[topic];
      if (!s) return Promise.resolve();
      return new Promise(resolve => {
        s.mediaSource.addEventListener('sourceopen', () => {
          if (s.mediaSource.readyState === 'open' && !s.sourceBuffer) {
            s.sourceBuffer = s.mediaSource.addSourceBuffer(mimeCodec);
            s.sourceBuffer.mode = 'sequence';
          }
          resolve();
        }, { once: true });
      });
    }));

    setLoadingStatus('streaming');

    // ── 等所有topic都有数据后才一起播放 ──
    const readyTopics = new Set();
    let autoPlayed = false;
    const tryAutoPlay = () => {
      if (autoPlayed) return;
      // 检查是否所有topic都已收到数据
      const allReady = topicList.every(t => readyTopics.has(t));
      if (!allReady) return;
      autoPlayed = true;
      // 等一小段时间让最后一个SourceBuffer完成append
      setTimeout(() => {
        // 先暂停所有video确保状态干净
        topicList.forEach(topic => {
          const v = videoRefs.current[topic];
          if (v) try { v.pause(); } catch (e) {}
        });
        // 统一play
        let playCount = 0;
        topicList.forEach(topic => {
          const v = videoRefs.current[topic];
          if (v) {
            v.play().then(() => {
              playCount++;
              if (playCount === topicList.length) setIsPlaying(true);
            }).catch(() => {
              console.warn('[MultiVideoGrid] autoplay blocked for', topic);
            });
          }
        });
      }, 300);
    };

    // 绑定updateend — 跟踪每个topic是否已有数据
    topicList.forEach(topic => {
      const s = states[topic];
      if (!s?.sourceBuffer) return;
      s.sourceBuffer.addEventListener('updateend', () => {
        s.isUpdating = false;
        if (!readyTopics.has(topic)) {
          readyTopics.add(topic);
        }
        drainQueue(topic);
        tryAutoPlay();
      });
      s.sourceBuffer.addEventListener('error', () => { s.isUpdating = false; });
    });

    // Fetch /stream-multi + demux
    const params = new URLSearchParams({
      bag_path: bagPath,
      topics: topicList.join(','),
      mode: mode === 'h264' ? 'h264' : 'hevc',
    });
    if (startTs != null) params.append('start_ts', String(startTs));
    if (endTs != null) params.append('end_ts', String(endTs));
    if (streamToken) params.append('token', streamToken);
    const streamUrl = `${apiBase}/api/video/stream-multi?${params.toString()}`;

    try {
      const resp = await fetch(streamUrl, { signal: controller.signal });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      let buffer = new Uint8Array(0);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const newBuf = new Uint8Array(buffer.length + value.length);
        newBuf.set(buffer);
        newBuf.set(value, buffer.length);
        buffer = newBuf;

        let offset = 0;
        while (offset + 5 <= buffer.length) {
          const topicIdx = buffer[offset];
          const dataLen = new DataView(buffer.buffer, buffer.byteOffset + offset + 1, 4).getUint32(0, true);
          if (topicIdx >= topicList.length || dataLen > 50 * 1024 * 1024) { offset += 1; continue; }
          if (offset + 5 + dataLen > buffer.length) break;
          enqueueData(topicList[topicIdx], buffer.slice(offset + 5, offset + 5 + dataLen));
          offset += 5 + dataLen;
        }
        if (offset > 0) buffer = buffer.slice(offset);
      }
      setLoadingStatus('done');
    } catch (e) {
      if (e.name === 'AbortError') return;
      console.error('[MultiVideoGrid] Stream error:', e);
      setError(e.message);
      setLoadingStatus('error');
    }
  };

  // ── 播放/暂停 ──
  const togglePlay = useCallback(() => {
    const videos = displayOrder.map(t => videoRefs.current[t]).filter(Boolean);
    if (videos.length === 0) return;

    // 直接检测实际状态：是否所有video都在播放
    const allPlaying = videos.every(v => !v.paused && !v.ended);

    if (allPlaying) {
      // 暂停全部
      videos.forEach(v => { try { v.pause(); } catch (e) {} });
      setIsPlaying(false);
    } else {
      // 播放全部：逐个play，确保同步
      let anyFailed = false;
      videos.forEach(v => {
        // 先暂停确保状态干净，再play
        try { v.pause(); } catch (e) {}
        const p = v.play();
        if (p) {
          p.catch(() => { anyFailed = true; });
        }
      });
      // 给play()一点时间完成，再检查状态
      setTimeout(() => {
        const nowAllPlaying = videos.every(v => !v.paused);
        setIsPlaying(nowAllPlaying);
        if (!nowAllPlaying) {
          // 有些失败了，重试一次
          videos.forEach(v => {
            if (v.paused) {
              v.play().catch(() => {});
            }
          });
          setTimeout(() => {
            setIsPlaying(videos.every(v => !v.paused));
          }, 200);
        }
      }, 100);
    }
  }, [displayOrder]);

  // ── 速度同步 ──
  useEffect(() => {
    displayOrder.forEach(t => { const v = videoRefs.current[t]; if (v) v.playbackRate = speed; });
  }, [speed, displayOrder]);

  // ── 拖拽 ──
  const handleDragStart = (e, idx) => {
    dragSrcIdx.current = idx;
    const img = new Image();
    img.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAUEBAAAACwAAAAAAQABAAACAkQBADs=';
    e.dataTransfer.setDragImage(img, 0, 0);
  };
  const handleDrop = (e, idx) => {
    e.preventDefault();
    const from = dragSrcIdx.current;
    if (from !== null && from !== idx) {
      setDisplayOrder(prev => { const n = [...prev]; [n[from], n[idx]] = [n[idx], n[from]]; return n; });
    }
    setDragOverIdx(null); dragSrcIdx.current = null;
  };

  // ── topics变化时重启流 ──
  useEffect(() => {
    if (topics.length === 0) return;
    setDisplayOrder(topics);
  }, [topics]);

  // ── 挂载时自动开始流 ──
  useEffect(() => {
    if (displayOrder.length > 0) {
      const raf = requestAnimationFrame(() => startStream());
      return () => { cancelAnimationFrame(raf); cleanup(); };
    }
    return () => cleanup();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── 渲染 ──
  const progress = bufferedEnd > 0 ? currentTime / bufferedEnd : 0;
  return (
    <div ref={containerRef} style={{ display: 'flex', flexDirection: 'column', width: '85vw', maxHeight: '80vh', background: isFullscreen ? '#000' : 'transparent' }}>
      {/* 进度条 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2, flexShrink: 0 }}>
        <span style={{ color: '#aaa', fontSize: 11, minWidth: 42, textAlign: 'right' }}>{fmtTime(currentTime)}</span>
        <div
          onClick={e => {
            const rect = e.currentTarget.getBoundingClientRect();
            seekTo(Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)));
          }}
          style={{
            flex: 1, height: 6, background: '#333', borderRadius: 3, cursor: 'pointer', position: 'relative',
          }}
        >
          <div style={{
            width: `${progress * 100}%`, height: '100%', background: '#1890ff', borderRadius: 3,
            transition: 'width 0.2s linear',
          }} />
          <div style={{
            position: 'absolute', left: `${progress * 100}%`, top: '50%', transform: 'translate(-50%,-50%)',
            width: 10, height: 10, borderRadius: '50%', background: '#1890ff', border: '2px solid #fff',
            pointerEvents: 'none', boxShadow: '0 0 3px rgba(0,0,0,0.5)',
          }} />
        </div>
        <span style={{ color: '#aaa', fontSize: 11, minWidth: 42 }}>{fmtTime(bufferedEnd)}</span>
      </div>

      {/* 工具栏 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4, flexShrink: 0 }}>
        <button onClick={togglePlay} style={{
          padding: '2px 8px', fontSize: 12, borderRadius: 3, border: 'none',
          background: isPlaying ? '#da3633' : '#238636', color: '#fff', cursor: 'pointer',
        }}>
          {isPlaying ? '⏸ 暂停' : '▶ 播放'}
        </button>
        {[0.5, 1, 2, 4].map(s => (
          <button key={s} onClick={() => setSpeed(s)} style={{
            padding: '2px 6px', fontSize: 11, borderRadius: 3,
            border: `1px solid ${speed === s ? '#1890ff' : '#555'}`,
            background: speed === s ? '#1890ff' : 'transparent',
            color: speed === s ? '#fff' : '#aaa', cursor: 'pointer',
          }}>
            {s}x
          </button>
        ))}
        <span style={{ color: '#888', fontSize: 11, marginLeft: 8 }}>
          {displayOrder.length}路 · {
            loadingStatus === 'streaming' ? '🟢 推流中' :
            loadingStatus === 'done' ? '✅ 完成' :
            loadingStatus === 'error' ? '❌' : '⏳ 连接中'
          }
        </span>
        {error && <span style={{ color: '#ff4d4f', fontSize: 11 }}> {error}</span>}
        <div style={{ flex: 1 }} />
        <button onClick={toggleFullscreen} style={{
          padding: '2px 8px', fontSize: 12, borderRadius: 3, border: '1px solid #555',
          background: 'transparent', color: '#ccc', cursor: 'pointer',
        }}>
          {isFullscreen ? '⤓ 退出全屏' : '⤢ 全屏'}
        </button>
      </div>

      {/* 宫格 */}
      <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
        <div style={{
          display: 'grid',
          gridTemplateColumns: `repeat(${cols}, 1fr)`,
          gap: 2,
          minHeight: 'min-content',
        }}>
          {displayOrder.map((topic, idx) => (
            <div
              key={topic}
              draggable
              onDragStart={e => handleDragStart(e, idx)}
              onDragOver={e => { e.preventDefault(); setDragOverIdx(idx); }}
              onDrop={e => handleDrop(e, idx)}
              onDragLeave={() => setDragOverIdx(null)}
              onDragEnd={() => { setDragOverIdx(null); dragSrcIdx.current = null; }}
              style={{
                position: 'relative', background: '#111', borderRadius: 4, overflow: 'hidden',
                border: dragOverIdx === idx ? '2px solid #58a6ff' : '2px solid transparent',
                aspectRatio: '16/9', cursor: 'grab',
              }}
            >
              <video
                ref={el => { videoRefs.current[topic] = el; }}
                muted playsInline
                style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block' }}
              />
              <div style={{
                position: 'absolute', top: 2, left: 2,
                background: COLORS[idx % COLORS.length],
                padding: '1px 6px', borderRadius: 2, fontSize: 10, fontWeight: 'bold', color: '#fff',
                textShadow: '0 1px 2px rgba(0,0,0,0.8)', pointerEvents: 'none',
              }}>
                {shortName(topic)}
              </div>
              <div style={{ position: 'absolute', top: 2, right: 2, color: 'rgba(255,255,255,0.2)', fontSize: 12, pointerEvents: 'none' }}>⠿</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
