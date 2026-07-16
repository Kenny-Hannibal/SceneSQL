import React, { useRef, useState, useEffect, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

const API_BASE = process.env.REACT_APP_API_BASE || '';
const PLAYBACK_FPS = 10;  // fusion_map_plus 固定 10fps (帧间100ms)
const PLAYBACK_INTERVAL = 1000 / PLAYBACK_FPS;  // 100ms

/**
 * BEV坐标系 → Three.js 映射:
 *   vehicle frame: x=纵向(前), y=横向(左正右负)
 *   Three.js (camera up=-z):  screen_up = -z = 前方, screen_right = +x
 *   映射: Three.js_x = -veh_y,  Three.js_z = -veh_x
 *   heading: rotation.y = π + heading (heading=0 → 前方 → -z → 屏幕上方)
 */
function vehToThree(vx, vy) {
  // veh frame (x=forward, y=lateral) → Three.js (x=right, z=forward→-z=up)
  return [-(vy || 0), -(vx || 0)];
}

export default function BevViewer({ bagPath, authFetch, startTsNs, endTsNs }) {
  const canvasRef = useRef(null);
  const [info, setInfo] = useState(null);
  const [currentFrame, setCurrentFrame] = useState(0);
  const [startFrameIdx, setStartFrameIdx] = useState(0);
  const [endFrameIdx, setEndFrameIdx] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [indexingMsg, setIndexingMsg] = useState('');
  const [error, setError] = useState('');
  const [frameStats, setFrameStats] = useState(null);

  // ── 用 ref 追踪播放状态，避免 React stale closure ──
  const playIdxRef = useRef(0);
  const playTimerRef = useRef(null);
  const playingRef = useRef(false);
  const lastPlayTimeRef = useRef(0);  // 上次播放的时间戳(ms)

  const threeRef = useRef({
    scene: null, camera: null, renderer: null, controls: null, groups: {}, animId: null,
  });
  const pendingDataRef = useRef(null);
  const threeInitializedRef = useRef(false);

  // ── 初始化 Three.js 场景 ──
  const initThreeJS = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || threeInitializedRef.current) return;
    threeInitializedRef.current = true;

    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 450;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a1a);

    // 正交相机 — -z = screen UP = 前方
    // viewSize=20 → 可见±20m=40m, 自车2m宽占5%, 合适
    const aspect = w / h;
    const viewSize = 20;
    const camera = new THREE.OrthographicCamera(
      -viewSize * aspect, viewSize * aspect, viewSize, -viewSize, 0.1, 2000
    );
    camera.position.set(0, 100, 0);
    camera.lookAt(0, 0, 0);
    camera.up.set(0, 0, -1);  // -z = screen up = forward

    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(window.devicePixelRatio);

    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(0, 0, 0);
    controls.maxPolarAngle = Math.PI * 0.85;
    controls.minDistance = 5;
    controls.maxDistance = 800;
    controls.update();

    // 网格 — x轴(横向), z轴(纵向)
    const grid = new THREE.GridHelper(200, 200, 0x333355, 0x1a1a2e);
    scene.add(grid);

    // ── 自车标记 ──
    // 地面圆环 (醒目定位, 半径2m)
    const ringGeo = new THREE.RingGeometry(1.8, 2.2, 32);
    const ringMat = new THREE.MeshBasicMaterial({ color: 0x4fc3f7, side: THREE.DoubleSide, transparent: true, opacity: 0.6 });
    const ring = new THREE.Mesh(ringGeo, ringMat);
    ring.rotation.x = -Math.PI / 2;  // 放平到 xz 平面
    ring.position.set(0, 0.02, 0);
    scene.add(ring);

    // 自车车身 (蓝色 box) — 真实尺寸: T68 约 w=1.9m, l=4.9m
    const carW = 1.9, carH = 1.5, carL = 4.9;
    const carGeo = new THREE.BoxGeometry(carW, carH, carL);
    const carMat = new THREE.MeshBasicMaterial({ color: 0x4fc3f7, transparent: true, opacity: 0.85 });
    const carMesh = new THREE.Mesh(carGeo, carMat);
    carMesh.position.set(0, carH / 2, 0);
    carMesh.rotation.y = Math.PI;  // heading=0 → 前方 → -z → rotation.y = π
    scene.add(carMesh);

    // 自车边框
    const carEdges = new THREE.EdgesGeometry(carGeo);
    const carLineMat = new THREE.LineBasicMaterial({ color: 0xffffff });
    const carWireframe = new THREE.LineSegments(carEdges, carLineMat);
    carWireframe.position.copy(carMesh.position);
    carWireframe.rotation.copy(carMesh.rotation);
    scene.add(carWireframe);

    // 前方箭头 (指向 -z = screen up = 前方)
    const arrowGeo = new THREE.ConeGeometry(0.35, 1.2, 8);
    const arrowMat = new THREE.MeshBasicMaterial({ color: 0x00e5ff });
    const arrow = new THREE.Mesh(arrowGeo, arrowMat);
    arrow.position.set(0, carH / 2, -(carL / 2 + 0.8));  // 车头前方
    arrow.rotation.x = -Math.PI / 2;  // 锥体尖端朝 -z (screen up)
    scene.add(arrow);

    // 第二个前方指示箭头 (更远处)
    const fwdGeo = new THREE.ConeGeometry(0.2, 0.8, 6);
    const fwdMat = new THREE.MeshBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.5 });
    const fwdArrow = new THREE.Mesh(fwdGeo, fwdMat);
    fwdArrow.position.set(0, carH / 2, -(carL / 2 + 4));
    fwdArrow.rotation.x = -Math.PI / 2;
    scene.add(fwdArrow);

    const groups = {
      obstacles: new THREE.Group(),
      paths: new THREE.Group(),
      boundaries: new THREE.Group(),
      lanes: new THREE.Group(),
    };
    Object.values(groups).forEach(g => scene.add(g));

    threeRef.current = { scene, camera, renderer, controls, groups, animId: null };

    const animate = () => {
      threeRef.current.animId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    if (pendingDataRef.current) {
      updateSceneInternal(pendingDataRef.current);
      pendingDataRef.current = null;
    }
  }, []);

  // ── canvas出现时初始化Three.js ──
  useEffect(() => {
    if (canvasRef.current) {
      initThreeJS();
    }
  }, [info, initThreeJS]);

  // ── 调整 canvas 大小 ──
  useEffect(() => {
    const handleResize = () => {
      const { renderer, camera } = threeRef.current;
      const canvas = canvasRef.current;
      if (!renderer || !canvas) return;
      const w = canvas.clientWidth || 800;
      const h = canvas.clientHeight || 450;
      renderer.setSize(w, h);
      if (camera.isOrthographicCamera) {
        const aspect = w / h;
        const viewSize = 20;
        camera.left = -viewSize * aspect;
        camera.right = viewSize * aspect;
        camera.top = viewSize;
        camera.bottom = -viewSize;
      } else {
        camera.aspect = w / h;
      }
      camera.updateProjectionMatrix();
    };
    window.addEventListener('resize', handleResize);
    handleResize();
    return () => window.removeEventListener('resize', handleResize);
  }, [info]);

  // ── 加载 fusion_map 基本信息 + 帧范围 ──
  const loadInfo = useCallback(async () => {
    if (!bagPath) return;
    setLoading(true);
    setError('');
    setIndexingMsg('');
    setPlaying(false);
    playingRef.current = false;
    setFrameStats(null);
    try {
      const res = await authFetch(`${API_BASE}/api/bag/fusion-map-info?bag_path=${encodeURIComponent(bagPath)}`);
      if (!res.ok) throw new Error(`fusion-map-info API 返回 ${res.status}`);
      const data = await res.json();
      setInfo(data);

      if (data.exists && data.total_frames > 0) {
        const hasStart = startTsNs != null && startTsNs > 0;
        const hasEnd = endTsNs != null && endTsNs > 0;

        if (hasStart || hasEnd) {
          setIndexingMsg('正在索引帧...');
          try {
            const params = new URLSearchParams({ bag_path: bagPath });
            if (hasStart) params.set('start_ts_ns', String(startTsNs));
            if (hasEnd) params.set('end_ts_ns', String(endTsNs));

            const rangeRes = await authFetch(`${API_BASE}/api/bag/fusion-map-frames-by-ts-range?${params.toString()}`);
            if (rangeRes.ok) {
              const rangeData = await rangeRes.json();
              if (!rangeData.error) {
                const sIdx = rangeData.start_frame_idx ?? 0;
                const eIdx = rangeData.end_frame_idx ?? (data.total_frames - 1);
                setStartFrameIdx(sIdx);
                setEndFrameIdx(eIdx);
                setCurrentFrame(sIdx);
                playIdxRef.current = sIdx;
                await loadFrameDirect(sIdx);
                setIndexingMsg('');
                return;
              }
            }
          } catch (e) {
            // 降级
          }
          setIndexingMsg('');
        }

        setStartFrameIdx(0);
        setEndFrameIdx(data.total_frames - 1);
        setCurrentFrame(0);
        playIdxRef.current = 0;
        try {
          await loadFrameDirect(0);
        } catch (e) {
          setError(`首帧加载失败: ${e.message}`);
        }
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [bagPath, authFetch, startTsNs, endTsNs]);

  // ── 加载并渲染单帧 ──
  const loadFrameDirect = async (idx) => {
    if (!bagPath) return;
    try {
      const res = await authFetch(`${API_BASE}/api/bag/fusion-map-frame?bag_path=${encodeURIComponent(bagPath)}&frame_idx=${idx}`);
      if (!res.ok) throw new Error('Failed to load frame');
      const result = await res.json();
      if (result.error) {
        setError(result.error);
        return;
      }
      updateSceneInternal(result.data);
      setCurrentFrame(idx);
      const d = result.data;
      setFrameStats({
        obs: (d.obstacles || []).length,
        bnd: (d.boundaries || []).length,
        lane: (d.lanes || []).length,
        path: (d.paths || []).length,
        ts_ns: d.timestamp_ns,
        veh: d.veh_loc ? `(${d.veh_loc.x.toFixed(1)},${d.veh_loc.y.toFixed(1)})` : 'None',
      });
      return d;
    } catch (e) {
      setError(e.message);
      return null;
    }
  };

  const loadFrame = useCallback(async (idx) => {
    return await loadFrameDirect(idx);
  }, [bagPath, authFetch]);

  // ── 更新 Three.js 场景 (动态数据: obstacles, boundaries, lanes, paths) ──
  const updateSceneInternal = (data) => {
    if (!threeRef.current.scene) {
      pendingDataRef.current = data;
      return;
    }
    const { groups } = threeRef.current;

    // 清除旧动态对象 (自车标记是静态的,不在这里清除)
    Object.values(groups).forEach(g => {
      while (g.children.length > 0) {
        const child = g.children[0];
        if (child.geometry) child.geometry.dispose();
        if (child.material) {
          if (Array.isArray(child.material)) {
            child.material.forEach(m => m.dispose());
          } else {
            child.material.dispose();
          }
        }
        g.remove(child);
      }
    });

    if (!data) return;

    // 障碍物 — vehToThree: x=forward→-z, y=lateral→-y→+x
    (data.obstacles || []).forEach(obs => {
      const [tx, tz] = vehToThree(obs.x, obs.y);
      const color = obs.movable ? 0xff6b6b : 0xffa726;
      const w = Math.max(0.1, obs.w || 1);
      const h = Math.max(0.1, obs.h || 1);
      const l = Math.max(0.1, obs.l || 1);
      const geo = new THREE.BoxGeometry(w, h, l);
      const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.6 });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(tx, h / 2, tz);
      mesh.rotation.y = Math.PI + (obs.heading || 0);  // heading=0 → forward → -z
      groups.obstacles.add(mesh);

      // 边框
      const edges = new THREE.EdgesGeometry(geo);
      const lineMat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9 });
      const wireframe = new THREE.LineSegments(edges, lineMat);
      wireframe.position.copy(mesh.position);
      wireframe.rotation.copy(mesh.rotation);
      groups.obstacles.add(wireframe);
    });

    // 路径
    (data.paths || []).forEach(path => {
      const pts = (path.points || []).map(p => {
        const [tx, tz] = vehToThree(p[0], p[1]);
        return new THREE.Vector3(tx, 0.1, tz);
      });
      if (pts.length < 2) return;
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const color = path.type === 'model' ? 0x4fc3f7 : 0x66bb6a;
      const mat = new THREE.LineBasicMaterial({ color, linewidth: 2 });
      groups.paths.add(new THREE.Line(geo, mat));
    });

    // 车道
    (data.lanes || []).forEach(lane => {
      const pts = (lane.points || []).map(p => {
        const [tx, tz] = vehToThree(p[0], p[1]);
        return new THREE.Vector3(tx, 0.05, tz);
      });
      if (pts.length < 2) return;
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const color = lane.source === 'rule' ? 0x81d4fa : 0xffff4c;
      const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.7 });
      groups.lanes.add(new THREE.Line(geo, mat));
    });

    // 边界
    (data.boundaries || []).forEach(bnd => {
      const pts = (bnd.points || []).map(p => {
        const [tx, tz] = vehToThree(p[0], p[1]);
        return new THREE.Vector3(tx, 0.05, tz);
      });
      if (pts.length < 2) return;
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const color = bnd.source === 'stopline' ? 0xff5252 : (bnd.source === 'rule' ? 0x90caf9 : 0xffffff);
      const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.5 });
      groups.boundaries.add(new THREE.Line(geo, mat));
    });
  };

  // ── 播放逻辑（用 ref 避免 stale closure，固定10fps）──
  const playNextFrame = useCallback(async () => {
    if (!playingRef.current) return;

    const endIdx = endFrameIdx != null ? endFrameIdx : (info?.total_frames - 1 || 0);
    const nextIdx = playIdxRef.current + 1;

    if (nextIdx > endIdx) {
      playingRef.current = false;
      setPlaying(false);
      return;
    }

    const t0 = performance.now();

    const frameData = await loadFrame(nextIdx);
    if (!frameData || !playingRef.current) return;

    playIdxRef.current = nextIdx;

    // 固定10fps: 无论网络耗时多少，下一帧延迟 = 100ms - 网络耗时
    const elapsed = performance.now() - t0;
    const delay = Math.max(0, PLAYBACK_INTERVAL - elapsed);
    playTimerRef.current = setTimeout(playNextFrame, delay);
  }, [endFrameIdx, info, loadFrame]);

  // ── 播放/暂停控制 ──
  useEffect(() => {
    playingRef.current = playing;

    if (playing) {
      playIdxRef.current = currentFrame;
      lastPlayTimeRef.current = 0;
      playNextFrame();
    } else {
      if (playTimerRef.current) {
        clearTimeout(playTimerRef.current);
        playTimerRef.current = null;
      }
    }

    return () => {
      if (playTimerRef.current) {
        clearTimeout(playTimerRef.current);
        playTimerRef.current = null;
      }
    };
  }, [playing]);

  // ── bag 路径变化时自动加载 ──
  useEffect(() => {
    if (bagPath) {
      loadInfo();
    }
  }, [bagPath, loadInfo]);

  const isSegment = (startTsNs != null && startTsNs > 0) || (endTsNs != null && endTsNs > 0);
  const segmentStartSec = startTsNs != null ? (startTsNs / 1e9).toFixed(2) : null;
  const segmentEndSec = endTsNs != null ? (endTsNs / 1e9).toFixed(2) : null;
  const totalFrames = info?.total_frames || 0;
  const playEnd = endFrameIdx != null ? endFrameIdx : totalFrames - 1;
  const segmentFrameCount = playEnd - startFrameIdx + 1;
  const showCanvas = bagPath;

  // 相对时间显示
  const baseTs = info?.first_ts_ns || 0;
  const tsSec = frameStats?.ts_ns ? ((frameStats.ts_ns - baseTs) / 1e9).toFixed(3) : '--';

  return (
    <div style={{ background: '#fff', borderRadius: 8, padding: 20, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
      <h2>🗺️ 3D BEV View (Fusion Map)</h2>
      {isSegment && (
        <div style={{ fontSize: 12, color: '#1890ff', marginBottom: 4, fontWeight: 500 }}>
          📍 片段模式: {segmentStartSec != null ? `${segmentStartSec}s` : '起始'} → {segmentEndSec != null ? `${segmentEndSec}s` : '末尾'}
          {segmentFrameCount > 0 && ` · 约 ${segmentFrameCount} 帧`}
        </div>
      )}
      <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
        🔵 自车(w1.9×l4.9m, 蓝色box+箭头=前方) · 红box=动态障碍 · 橙box=静态障碍 · 黄线=车道 · 白线=边界 · 绿线=路径 · 10fps
      </div>
      {error && <div style={{ color: 'red', marginBottom: 10 }}>{error}</div>}
      {loading && <div style={{ color: '#1890ff', marginBottom: 10 }}>⏳ Loading fusion map info for: {bagPath}...</div>}
      {indexingMsg && <div style={{ color: '#d48806', marginBottom: 10 }}>⏳ {indexingMsg}</div>}
      {info && !info.exists && (
        <div style={{ color: '#999', padding: '20px 0' }}>此 bag 无 fusion_map_plus 数据</div>
      )}
      {showCanvas && (
        <>
          <canvas
            ref={canvasRef}
            style={{ width: '100%', height: 450, background: '#0a0a1a', borderRadius: 4, marginTop: 12, display: 'block' }}
          />
          {frameStats && (
            <div style={{ fontSize: 11, fontFamily: 'monospace', color: '#666', marginTop: 4, padding: '4px 8px', background: '#f5f5f5', borderRadius: 3 }}>
              t={tsSec}s · Obs:{frameStats.obs} Bnd:{frameStats.bnd} Lane:{frameStats.lane} Path:{frameStats.path} · veh={frameStats.veh}
            </div>
          )}
          {info && info.exists && (
            <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <button
                onClick={() => setPlaying(!playing)}
                disabled={!!indexingMsg}
                style={{
                  padding: '8px 16px', fontSize: 14, borderRadius: 4, border: 'none',
                  background: playing ? '#ff4d4f' : (indexingMsg ? '#ccc' : '#52c41a'), color: '#fff', cursor: indexingMsg ? 'not-allowed' : 'pointer',
                }}
              >
                {playing ? '⏸ Pause' : '▶ Play'}
              </button>
              <span style={{ fontFamily: 'monospace', fontSize: 13 }}>
                {isSegment
                  ? `片段帧: ${currentFrame - startFrameIdx + 1} / ${segmentFrameCount}`
                  : `Frame: ${currentFrame + 1} / ${totalFrames}`}
              </span>
              <input
                type="range"
                min={startFrameIdx}
                max={endFrameIdx != null ? endFrameIdx : (totalFrames - 1 || 1)}
                value={currentFrame}
                onChange={(e) => {
                  const idx = parseInt(e.target.value);
                  setCurrentFrame(idx);
                  playIdxRef.current = idx;
                  loadFrame(idx);
                }}
                style={{ flex: 1, cursor: 'pointer' }}
              />
              <span style={{ fontSize: 12, color: '#999' }}>
                {info.file_size_mb?.toFixed(0)}MB · {info.format} · 10fps
              </span>
            </div>
          )}
        </>
      )}
      {!showCanvas && !loading && !error && (
        <div style={{ color: '#999', padding: '20px 0', textAlign: 'center' }}>加载 bag 后显示 3D BEV 视图</div>
      )}
    </div>
  );
}
