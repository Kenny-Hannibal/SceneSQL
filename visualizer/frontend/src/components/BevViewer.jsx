import React, { useRef, useState, useEffect, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

const API_BASE = process.env.REACT_APP_API_BASE || '';

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
  const [frameStats, setFrameStats] = useState(null); // {obs, bnd, lane, path, ts_ns}

  const threeRef = useRef({
    scene: null,
    camera: null,
    renderer: null,
    controls: null,
    groups: {},
    animId: null,
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

    // 正交相机（和UBM一致，更适合BEV俯视）
    const aspect = w / h;
    const viewSize = 40;
    const camera = new THREE.OrthographicCamera(
      -viewSize * aspect, viewSize * aspect, viewSize, -viewSize, 0.1, 2000
    );
    camera.position.set(0, 100, 0);
    camera.lookAt(0, 0, 0);
    camera.up.set(0, 0, -1);

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

    const grid = new THREE.GridHelper(200, 100, 0x333355, 0x222244);
    scene.add(grid);

    const axesHelper = new THREE.AxesHelper(5);
    scene.add(axesHelper);

    const groups = {
      obstacles: new THREE.Group(),
      paths: new THREE.Group(),
      boundaries: new THREE.Group(),
      lanes: new THREE.Group(),
      vehicle: new THREE.Group(),
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
        const viewSize = 40;
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
    try {
      const res = await authFetch(`${API_BASE}/api/bag/fusion-map-info?bag_path=${encodeURIComponent(bagPath)}`);
      if (!res.ok) throw new Error('Failed to load fusion map info');
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
        await loadFrameDirect(0);
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
      // 更新帧统计
      const d = result.data;
      setFrameStats({
        obs: (d.obstacles || []).length,
        bnd: (d.boundaries || []).length,
        lane: (d.lanes || []).length,
        path: (d.paths || []).length,
        ts_ns: d.timestamp_ns,
        veh: d.veh_loc ? `(${d.veh_loc.x.toFixed(1)},${d.veh_loc.y.toFixed(1)})` : 'None',
      });
    } catch (e) {
      setError(e.message);
    }
  };

  const loadFrame = useCallback(async (idx) => {
    await loadFrameDirect(idx);
  }, [bagPath, authFetch]);

  // ── 更新 Three.js 场景 ──
  const updateSceneInternal = (data) => {
    if (!threeRef.current.scene) {
      pendingDataRef.current = data;
      return;
    }
    const { groups, camera, controls } = threeRef.current;

    // 清除旧对象
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

    // 障碍物
    (data.obstacles || []).forEach(obs => {
      const color = obs.movable ? 0xff6b6b : 0xffa726;
      const w = Math.max(0.1, obs.w || 1);
      const h = Math.max(0.1, obs.h || 1);
      const l = Math.max(0.1, obs.l || 1);
      const geo = new THREE.BoxGeometry(w, h, l);
      const mat = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.6 });
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(obs.x || 0, h / 2, obs.y || 0);
      mesh.rotation.y = obs.heading || 0;
      groups.obstacles.add(mesh);

      const edges = new THREE.EdgesGeometry(geo);
      const lineMat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.9 });
      const wireframe = new THREE.LineSegments(edges, lineMat);
      wireframe.position.copy(mesh.position);
      wireframe.rotation.copy(mesh.rotation);
      groups.obstacles.add(wireframe);
    });

    // 路径
    (data.paths || []).forEach(path => {
      const pts = (path.points || []).map(p => new THREE.Vector3(p[0], 0.1, p[1]));
      if (pts.length < 2) return;
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const color = path.type === 'model' ? 0x4fc3f7 : 0x66bb6a;
      const mat = new THREE.LineBasicMaterial({ color, linewidth: 2 });
      groups.paths.add(new THREE.Line(geo, mat));
    });

    // 车道
    (data.lanes || []).forEach(lane => {
      const pts = (lane.points || []).map(p => new THREE.Vector3(p[0], 0.05, p[1]));
      if (pts.length < 2) return;
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const color = lane.source === 'rule' ? 0x81d4fa : 0xffff4c;
      const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.7 });
      groups.lanes.add(new THREE.Line(geo, mat));
    });

    // 边界
    (data.boundaries || []).forEach(bnd => {
      const pts = (bnd.points || []).map(p => new THREE.Vector3(p[0], 0.05, p[1]));
      if (pts.length < 2) return;
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const color = bnd.source === 'stopline' ? 0xff5252 : (bnd.source === 'rule' ? 0x90caf9 : 0xffffff);
      const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.5 });
      groups.boundaries.add(new THREE.Line(geo, mat));
    });

    // 车辆位置
    const vl = data.veh_loc;
    if (vl) {
      const carGeo = new THREE.BoxGeometry(2, 1.5, 4.5);
      const carMat = new THREE.MeshBasicMaterial({ color: 0x4fc3f7, transparent: true, opacity: 0.8 });
      const carMesh = new THREE.Mesh(carGeo, carMat);
      carMesh.position.set(vl.x || 0, 0.75, vl.y || 0);
      if (vl.qw !== undefined) {
        carMesh.quaternion.set(vl.qx || 0, vl.qy || 0, vl.qz || 0, vl.qw || 1);
      }
      groups.vehicle.add(carMesh);

      // 前方箭头（和UBM一致）
      const arrowGeo = new THREE.ConeGeometry(0.3, 1, 8);
      const arrowMat = new THREE.MeshBasicMaterial({ color: 0x4fc3f7 });
      const arrow = new THREE.Mesh(arrowGeo, arrowMat);
      arrow.position.set(vl.x || 0, 0.75, vl.y || 0);
      arrow.rotation.x = -Math.PI / 2;
      if (vl.qw !== undefined) {
        const q = new THREE.Quaternion(vl.qx || 0, vl.qy || 0, vl.qz || 0, vl.qw || 1);
        arrow.quaternion.copy(q);
        arrow.rotateX(-Math.PI / 2);
      }
      groups.vehicle.add(arrow);

      // 正交相机跟随车辆
      if (camera.isOrthographicCamera) {
        camera.position.set(vl.x || 0, 100, vl.y || 0);
        camera.lookAt(vl.x || 0, 0, vl.y || 0);
        if (controls) {
          controls.target.set(vl.x || 0, 0, vl.y || 0);
          controls.update();
        }
      }
    }
  };

  // ── 播放/暂停（基于真实时间差控制帧率，和UBM一致）──
  const playTimerRef = useRef(null);
  const lastTsRef = useRef(null);

  const playNextFrame = useCallback(async () => {
    const nextIdx = currentFrame + 1;
    if (nextIdx > (endFrameIdx ?? (info?.total_frames - 1 || 0))) {
      setPlaying(false);
      return;
    }
    await loadFrame(nextIdx);
    // 根据帧间时间差计算下一帧延迟
    if (frameStats?.ts_ns && lastTsRef.current && frameStats.ts_ns > lastTsRef.current) {
      const dtMs = (frameStats.ts_ns - lastTsRef.current) / 1e6;
      playTimerRef.current = setTimeout(playNextFrame, Math.max(10, dtMs));
    } else {
      playTimerRef.current = setTimeout(playNextFrame, 100); // 默认100ms
    }
    lastTsRef.current = frameStats?.ts_ns || null;
  }, [currentFrame, endFrameIdx, info, frameStats, loadFrame]);

  useEffect(() => {
    if (playing) {
      lastTsRef.current = null;
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

  // 时间戳显示
  const tsSec = frameStats?.ts_ns ? (frameStats.ts_ns / 1e9).toFixed(3) : '--';

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
        鼠标左键旋转 · 右键平移 · 滚轮缩放
      </div>
      {error && <div style={{ color: 'red', marginBottom: 10 }}>{error}</div>}
      {loading && <div style={{ color: '#1890ff', marginBottom: 10 }}>Loading fusion map info...</div>}
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
          {/* 帧数据统计 */}
          {frameStats && (
            <div style={{ fontSize: 11, fontFamily: 'monospace', color: '#666', marginTop: 4, padding: '4px 8px', background: '#f5f5f5', borderRadius: 3 }}>
              ts={tsSec}s · Obs:{frameStats.obs} Bnd:{frameStats.bnd} Lane:{frameStats.lane} Path:{frameStats.path} · veh={frameStats.veh}
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
                  : `Frame: ${currentFrame + 1} / ${totalFrames}`
                }
              </span>
              <input
                type="range"
                min={startFrameIdx}
                max={endFrameIdx != null ? endFrameIdx : (totalFrames - 1 || 1)}
                value={currentFrame}
                onChange={(e) => {
                  const idx = parseInt(e.target.value);
                  setCurrentFrame(idx);
                  loadFrame(idx);
                }}
                style={{ flex: 1, cursor: 'pointer' }}
              />
              <span style={{ fontSize: 12, color: '#999' }}>
                {info.file_size_mb}MB · {info.format}
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
