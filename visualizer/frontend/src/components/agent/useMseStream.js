import { useEffect } from 'react';
import { authFetch } from '../../api';

// ── MSE 流式播放 hook ──
// 把 MediaSource + fetch 流式喂数据的逻辑从组件中抽离。
// active=false 或参数变化时自动 cleanup（abort fetch、释放 object URL、endOfStream）。
//
// 参数：
//   videoRef   — <video> 元素的 ref
//   active     — 是否启用（playerModalOpen && playerData.use_mse）
//   streamUrl  — 流地址
//   codec      — mime codec（HEVC hvc1 / H.264 avc1）
//   durationSec— 预设时长（ts 窗口），进度条立即显示完整长度
//   onError    — (msg) => void 播放失败回调
//   streamAbortRef / mseCleanupRef — 父组件持有的 ref，用于外部强制断开（关闭弹窗、切行可视化）
export function useMseStream({ videoRef, active, streamUrl, codec, durationSec, onError, streamAbortRef, mseCleanupRef }) {
  useEffect(() => {
    if (!active || !streamUrl || !videoRef.current) return;

    const video = videoRef.current;
    const mediaSource = new MediaSource();
    const objectUrl = URL.createObjectURL(mediaSource);
    video.src = objectUrl;

    let sourceBuffer = null;
    let reader = null;
    let aborted = false;
    const streamController = new AbortController();
    if (streamAbortRef) streamAbortRef.current = streamController;

    const mimeCodec = codec || 'video/mp4; codecs="hvc1.1.6.L120.B0"';

    const cleanup = () => {
      if (aborted) return;
      aborted = true;
      // 1. 彻底断开 video 与 MediaSource / object URL 的绑定
      try { video.pause(); } catch (e) {}
      try { video.removeAttribute('src'); video.load(); } catch (e) {}
      try { URL.revokeObjectURL(objectUrl); } catch (e) {}
      // 2. abort SourceBuffer 上 pending 的 append
      if (sourceBuffer) { try { sourceBuffer.abort(); } catch (e) {} }
      // 3. 结束 MediaSource
      try { if (mediaSource.readyState === 'open') mediaSource.endOfStream(); } catch (e) {}
      // 4. abort fetch，彻底关闭底层 TCP 连接。
      // 只 abort controller；reader.read() 会因此 reject，被外层 try-catch 处理。
      // 不要单独调用 reader.cancel()，否则会触发未捕获的 AbortError promise rejection。
      try { streamController.abort(); } catch (e) {}
    };

    const onMseError = (source, detail) => {
      if (aborted) return;
      const videoErr = video.error;
      const msState = mediaSource.readyState;
      console.error('[MSE] 播放错误:', { source, detail, videoErrorCode: videoErr?.code, mediaSourceState: msState });
      onError(`流式播放失败: [${source}] ${detail || '未知错误'} | video.error=${videoErr?.code || 'none'} | msState=${msState}`);
      cleanup();
    };

    const onSourceBufferError = (e) => onMseError('SourceBuffer', e.message || 'SourceBuffer error');
    const onMediaSourceError = (e) => onMseError('MediaSource', e.message || 'MediaSource error');
    const onVideoError = () => {
      if (aborted) return;
      const ve = video.error;
      if (ve) {
        const codes = { 1: 'MEDIA_ERR_ABORTED', 2: 'MEDIA_ERR_NETWORK', 3: 'MEDIA_ERR_DECODE', 4: 'MEDIA_ERR_SRC_NOT_SUPPORTED' };
        onMseError('VideoElement', `${codes[ve.code] || 'UNKNOWN'}: ${ve.message || ''}`);
      }
    };

    mediaSource.addEventListener('error', onMediaSourceError);
    video.addEventListener('error', onVideoError);

    mediaSource.addEventListener('sourceopen', async () => {
      if (aborted) return;
      try {
        // 用搜索结果的 start_ts/end_ts 预计算视频总时长，进度条立即显示完整长度，
        // 不依赖流式数据到达后动态增长（endOfStream 不会缩短已预设的 duration）
        if (durationSec && durationSec > 0) {
          try { mediaSource.duration = durationSec; } catch (e) {}
        }

        sourceBuffer = mediaSource.addSourceBuffer(mimeCodec);
        sourceBuffer.addEventListener('error', onSourceBufferError);

        // 收尾：用实际缓冲终点校正 duration 再 endOfStream。
        // ts 窗口预设值可能大于实际流时长（首帧 ts 不精确对齐 start_ts），
        // 而 MSE 的 duration 在 endOfStream 时只会取 max(预设, 缓冲终点)，
        // 不会缩短，必须在 endOfStream 前手动下调，否则进度条与播放进度错位跳动。
        const finishStream = () => {
          try {
            if (sourceBuffer && !sourceBuffer.updating && sourceBuffer.buffered.length > 0) {
              const actualEnd = sourceBuffer.buffered.end(sourceBuffer.buffered.length - 1);
              if (mediaSource.duration > actualEnd + 0.05) {
                mediaSource.duration = actualEnd;
              }
            }
            mediaSource.endOfStream();
          } catch (e) {}
        };

        const response = await authFetch(streamUrl, { signal: streamController.signal });
        if (!response.ok) throw new Error(`Stream HTTP ${response.status}`);
        reader = response.body.getReader();

        const queue = [];
        let isUpdating = false;

        const processQueue = () => {
          if (aborted || isUpdating || queue.length === 0) return;
          const chunk = queue.shift();
          try {
            sourceBuffer.appendBuffer(chunk);
            isUpdating = true;
          } catch (e) {
            console.error('appendBuffer failed:', e);
            cleanup();
          }
        };

        const onUpdateEnd = () => {
          isUpdating = false;
          if (queue.length === 0 && reader === null) {
            finishStream();
            return;
          }
          processQueue();
        };
        sourceBuffer.addEventListener('updateend', onUpdateEnd);

        while (!aborted) {
          const { done, value } = await reader.read();
          if (done) {
            reader = null;
            if (!isUpdating && queue.length === 0) finishStream();
            break;
          }
          queue.push(value);
          processQueue();
        }

        sourceBuffer.removeEventListener('updateend', onUpdateEnd);
      } catch (e) {
        onMseError('Fetch/Setup', e.message || String(e));
      }
    });

    // 把 cleanup 暴露给外部，方便 startVisualization 里立即同步调用
    if (mseCleanupRef) mseCleanupRef.current = cleanup;

    return () => {
      video.removeEventListener('error', onVideoError);
      mediaSource.removeEventListener('error', onMediaSourceError);
      if (sourceBuffer) sourceBuffer.removeEventListener('error', onSourceBufferError);
      cleanup();
      if (mseCleanupRef) mseCleanupRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, streamUrl, codec]);
}
