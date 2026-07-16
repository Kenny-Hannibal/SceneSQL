import React, { useRef, useState, useEffect, useCallback } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

const API_BASE = process.env.REACT_APP_API_BASE || '';

/**
 * 3D BEV 可视化组件
 * 使用 Three.js PerspectiveCamera + OrbitControls 渲染 EFusionMap 数据
 * 默认俯视（BEV），可鼠标拖拽旋转到任意3D角度
 *
 * Props:
 *   bagPath: string — bag 目录路径
 *   authFetch: function — 带认证的 fetch
 *   startTsNs: number | null — 起始时间戳（纳秒），SQL结果锚定用
 *   endTsNs: number | null — 结束时间戳（纳秒），播放终止用
 */
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

  // Three.js 对象引用
  const threeRef = useRef({
    scene: null,
    camera: null,
    renderer: null,
    controls: null,
    groups: {},
    animId: null,
  });

  // 缓存最新帧数据，Three.js 就绪后立即渲染
  const pendingDataRef = useRef(null);
  // 标记Three.js是否已初始化
  const threeInitializedRef = useRef(false);

  // ── 初始化 Three.js 场景（canvas DOM就绪后调用）──
  const initThreeJS = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || threeInitializedRef.current) return;
    threeInitializedRef.current = true;

    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 450;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a1a);

    const camera = new THREE.PerspectiveCamera(60, w / h, 0.1, 5000);
    camera.position.set(0, 100, 0.01);
    camera.lookAt(0, 0, 0);

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

    const axesHelper = new THREE.AxesHelper(3);
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

    // 渲染循环
    const animate = () => {
      threeRef.current.animId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    // Three.js 就绪后，渲染之前缓存的数据
    if (pendingDataRef.current) {
      updateSceneInternal(pendingDataRef.current);
      pendingDataRef.current = null;
    }
  }, []);

  // ── 当canvas出现时初始化Three.js ──
  useEffect(() => {
    // canvas可能还没渲染（条件渲染），用MutationObserver或setTimeout检测
    if (canvasRef.current) {
      initThreeJS();
    }
  }, [info, initThreeJS]); // info变化时检查canvas是否出现

  // ── 调整 canvas 大小 ──
  useEffect(() => {
    const handleResize = () => {
      const { renderer, camera } = threeRef.current;
      const canvas = canvasRef.current;
      if (!renderer || !canvas) return;
      const w = canvas.clientWidth || 800;
      const h = canvas.clientHeight || 450;
      renderer.setSize(w, h);
      camera.aspect = w / h;
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

    // 障碍物（3D 立方体）
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

      const carEdges = new THREE.EdgesGeometry(carGeo);
      const carLineMat = new THREE.LineBasicMaterial({ color: 0x4fc3f7 });
      const carWireframe = new THREE.LineSegments(carEdges, carLineMat);
      carWireframe.position.copy(carMesh.position);
      carWireframe.quaternion.copy(carMesh.quaternion);
      groups.vehicle.add(carWireframe);

      // 相机跟随车辆
      const cx = vl.x || 0;
      const cy = vl.y || 0;
      camera.position.set(cx, 100, cy + 0.01);
      camera.lookAt(cx, 0, cy);
      if (controls) {
        controls.target.set(cx, 0, cy);
        controls.update();
      }
    }
  };

  // ── 播放/暂停动画 ──
  const totalFrames = info?.total_frames || 0;
  const playEnd = endFrameIdx != null ? endFrameIdx : totalFrames - 1;
  const segmentFrameCount = playEnd - startFrameIdx + 1;

  useEffect(() => {
    if (!playing || !info || info.total_frames <= 0) return;
    const interval = setInterval(() => {
      setCurrentFrame(prev => {
        const next = prev + 1;
        if (next > playEnd) {
          setPlaying(false);
          return prev;
        }
        loadFrame(next);
        return next;
      });
    }, 200);
    return () => clearInterval(interval);
  }, [playing, info, playEnd, loadFrame]);

  // ── bag 路径变化时自动加载 ──
  useEffect(() => {
    if (bagPath) {
      loadInfo();
    }
  }, [bagPath, loadInfo]);

  // ── 片段信息显示 ──
  const isSegment = (startTsNs != null && startTsNs > 0) || (endTsNs != null && endTsNs > 0);
  const segmentStartSec = startTsNs != null ? (startTsNs / 1e9).toFixed(2) : null;
  const segmentEndSec = endTsNs != null ? (endTsNs / 1e9).toFixed(2) : null;

  // canvas始终渲染（不再是条件渲染），info加载前显示placeholder
  const showCanvas = bagPath;

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
          {info && info.exists && (
            <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
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
