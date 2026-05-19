import React, { useRef, useState, useEffect } from 'react';

const SPEEDS = [0.5, 1, 2, 4, 8, 10];

export default function VideoPlayer({ videoUrl, durationSec }) {
  const videoRef = useRef(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [speed, setSpeed] = useState(1);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const updateTime = () => setCurrentTime(video.currentTime);
    video.addEventListener('timeupdate', updateTime);
    return () => video.removeEventListener('timeupdate', updateTime);
  }, [videoUrl]);

  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.playbackRate = speed;
    }
  }, [speed]);

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      video.play();
      setIsPlaying(true);
    } else {
      video.pause();
      setIsPlaying(false);
    }
  };

  const handleSeek = (e) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = parseFloat(e.target.value);
    setCurrentTime(video.currentTime);
  };

  const formatTime = (t) => {
    const m = Math.floor(t / 60);
    const s = Math.floor(t % 60);
    const ms = Math.floor((t % 1) * 100);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${ms.toString().padStart(2, '0')}`;
  };

  return (
    <div style={{ background: '#fff', borderRadius: '8px', padding: '20px' }}>
      <h2>🎬 Video Player</h2>
      {videoUrl ? (
        <>
          <video
            ref={videoRef}
            src={videoUrl}
            style={{ width: '100%', maxHeight: '600px', background: '#000', borderRadius: '4px' }}
            onPlay={() => setIsPlaying(true)}
            onPause={() => setIsPlaying(false)}
            controls={false}
          />
          <div style={{ marginTop: '15px', display: 'flex', alignItems: 'center', gap: '15px', flexWrap: 'wrap' }}>
            <button
              onClick={togglePlay}
              style={{ padding: '8px 20px', fontSize: '16px', borderRadius: '4px', border: 'none', background: '#52c41a', color: '#fff', cursor: 'pointer' }}
            >
              {isPlaying ? '⏸ Pause' : '▶ Play'}
            </button>

            <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '10px' }}>
              <span style={{ fontFamily: 'monospace', fontSize: '14px', minWidth: '80px' }}>
                {formatTime(currentTime)}
              </span>
              <input
                type="range"
                min={0}
                max={durationSec || 100}
                step={0.1}
                value={currentTime}
                onChange={handleSeek}
                style={{ flex: 1, cursor: 'pointer' }}
              />
              <span style={{ fontFamily: 'monospace', fontSize: '14px', minWidth: '80px' }}>
                {formatTime(durationSec || 0)}
              </span>
            </div>

            <div style={{ display: 'flex', gap: '5px' }}>
              <span style={{ fontSize: '14px', color: '#666', alignSelf: 'center' }}>Speed:</span>
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  onClick={() => setSpeed(s)}
                  style={{
                    padding: '5px 10px',
                    fontSize: '13px',
                    borderRadius: '4px',
                    border: '1px solid #ccc',
                    background: speed === s ? '#1890ff' : '#fff',
                    color: speed === s ? '#fff' : '#333',
                    cursor: 'pointer',
                  }}
                >
                  {s}x
                </button>
              ))}
            </div>
          </div>
        </>
      ) : (
        <div style={{ padding: '40px', textAlign: 'center', color: '#999' }}>No video loaded</div>
      )}
    </div>
  );
}
