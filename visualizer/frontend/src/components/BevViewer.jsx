import React, { useRef, useState, useEffect, useCallback } from 'react';

const API_BASE = 'http://localhost:8000';

/**
 * BEV (Bird's Eye View) 可视化组件
 * 使用 Three.js OrthographicCamera 渲染 EFusionMap 数据
 *
 * Props:
 *   bagPath: string — bag 目录路径
 *   authFetch: function — 带认证的 fetch
 */
export default function BevViewer({ bagPath, authFetch }) {
  const canvasRef = useRef(null);
  const [info, setInfo] = useState(null);
  const [currentFrame, setCurrentFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Three.js 对象引用
  const threeRef = useRef({
    scene: null,
    camera: null,
    renderer: null,
    groups: {},
    animId: null,
  });

  // ── 初始化 Three.js 场景 ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // 动态加载 Three.js
    if (!window.THREE) {
      const script = document.createElement('script');
      script.src = 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js';
      script.onload = () => initScene();
      document.head.appendChild(script);
    } else {
      initScene();
    }

    function initScene() {
      const THREE = window.THREE;
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x0a0a1a);

      const camera = new THREE.OrthographicCamera(-50, 50, 50, -50, 0.1, 200);
      camera.position.set(0, 80, 0);
      camera.lookAt(0, 0, 0);

      const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
      renderer.setSize(canvas.clientWidth, canvas.clientHeight);
      renderer.setPixelRatio(window.devicePixelRatio);

      const grid = new THREE.GridHelper(100, 50, 0x333355, 0x222244);
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

      threeRef.current = { scene, camera, renderer, groups, animId: null };
      renderLoop();
    }

    function renderLoop() {
      const { renderer, scene, camera } = threeRef.current;
      if (!renderer) return;
      const animate = () => {
        threeRef.current.animId = requestAnimationFrame(animate);
        renderer.render(scene, camera);
      };
      animate();
    }

    return () => {
      if (threeRef.current.animId) {
        cancelAnimationFrame(threeRef.current.animId);
      }
    };
  }, []);

  // ── 调整 canvas 大小 ──
  useEffect(() => {
    const handleResize = () => {
      const { renderer, camera } = threeRef.current;
      const canvas = canvasRef.current;
      if (!renderer || !canvas) return;
      const w = canvas.clientWidth;
      const h = canvas.clientHeight;
      renderer.setSize(w, h);
      const aspect = w / h;
      const viewSize = 50;
      camera.left = -viewSize * aspect;
      camera.right = viewSize * aspect;
      camera.top = viewSize;
      camera.bottom = -viewSize;
      camera.updateProjectionMatrix();
    };
    window.addEventListener('resize', handleResize);
    handleResize();
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // ── 鼠标滚轮缩放 ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const handleWheel = (e) => {
      e.preventDefault();
      const { camera } = threeRef.current;
      if (!camera) return;
      const factor = e.deltaY > 0 ? 1.1 : 0.9;
      camera.left *= factor;
      camera.right *= factor;
      camera.top *= factor;
      camera.bottom *= factor;
      camera.updateProjectionMatrix();
    };
    canvas.addEventListener('wheel', handleWheel, { passive: false });
    return () => canvas.removeEventListener('wheel', handleWheel);
  }, []);

  // ── 加载 fusion_map 基本信息 ──
  const loadInfo = useCallback(async () => {
    if (!bagPath) return;
    setLoading(true);
    setError('');
    try {
      const res = await authFetch(`${API_BASE}/api/bag/fusion-map-info?bag_path=${encodeURIComponent(bagPath)}`);
      if (!res.ok) throw new Error('Failed to load fusion map info');
      const data = await res.json();
      setInfo(data);
      if (data.exists && data.total_frames > 0) {
        setCurrentFrame(0);
        loadFrame(0);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [bagPath, authFetch]);

  // ── 加载并渲染单帧 ──
  const loadFrame = useCallback(async (idx) => {
    if (!bagPath) return;
    try {
      const res = await authFetch(`${API_BASE}/api/bag/fusion-map-frame?bag_path=${encodeURIComponent(bagPath)}&frame_idx=${idx}`);
      if (!res.ok) throw new Error('Failed to load frame');
      const result = await res.json();
      if (result.error) {
        setError(result.error);
        return;
      }
      updateScene(result.data);
      setCurrentFrame(idx);
    } catch (e) {
      setError(e.message);
    }
  }, [bagPath, authFetch]);

  // ── 更新 Three.js 场景 ──
  const updateScene = useCallback((data) => {
    const THREE = window.THREE;
    if (!THREE || !threeRef.current.scene) return;
    const { groups, camera } = threeRef.current;

    // 清除旧对象
    Object.values(groups).forEach(g => {
      while (g.children.length > 0) {
        const child = g.children[0];
        if (child.geometry) child.geometry.dispose();
        if (child.material) child.material.dispose();
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
      mesh.position.set(obs.x || 0, 0.5, obs.y || 0);
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
      camera.position.set(vl.x || 0, 80, vl.y || 0);
      camera.lookAt(vl.x || 0, 0, vl.y || 0);
    }
  }, []);

  // ── 播放/暂停动画 ──
  useEffect(() => {
    if (!playing || !info || info.total_frames <= 0) return;
    const interval = setInterval(() => {
      setCurrentFrame(prev => {
        const next = prev + 1;
        if (next >= info.total_frames) {
          setPlaying(false);
          return prev;
        }
        loadFrame(next);
        return next;
      });
    }, 200); // 5fps for BEV animation
    return () => clearInterval(interval);
  }, [playing, info, loadFrame]);

  // ── bag 路径变化时自动加载 ──
  useEffect(() => {
    if (bagPath) {
      loadInfo();
    }
  }, [bagPath, loadInfo]);

  const totalFrames = info?.total_frames || 0;

  return (
    <div style={{ background: '#fff', borderRadius: 8, padding: 20, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
      <h2>🗺️ BEV View (Fusion Map)</h2>
      {error && <div style={{ color: 'red', marginBottom: 10 }}>{error}</div>}
      {loading && <div style={{ color: '#1890ff', marginBottom: 10 }}>Loading fusion map info...</div>}
      {info && !info.exists && (
        <div style={{ color: '#999', padding: '20px 0' }}>此 bag 无 fusion_map_plus 数据</div>
      )}
      {info && info.exists && (
        <>
          <canvas
            ref={canvasRef}
            style={{ width: '100%', height: 400, background: '#0a0a1a', borderRadius: 4, marginTop: 12, display: 'block' }}
          />
          <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <button
              onClick={() => setPlaying(!playing)}
              style={{
                padding: '8px 16px', fontSize: 14, borderRadius: 4, border: 'none',
                background: playing ? '#ff4d4f' : '#52c41a', color: '#fff', cursor: 'pointer',
              }}
            >
              {playing ? '⏸ Pause' : '▶ Play'}
            </button>
            <span style={{ fontFamily: 'monospace', fontSize: 13 }}>
              Frame: {currentFrame + 1} / {totalFrames}
            </span>
            <input
              type="range"
              min={0}
              max={totalFrames - 1 || 1}
              value={currentFrame}
              onChange={(e) => {
                const idx = parseInt(e.target.value);
                setCurrentFrame(idx);
                loadFrame(idx);
              }}
              style={{ flex: 1, cursor: 'pointer' }}
            />
            <span style={{ fontSize: 12, color: '#999' }}>
              滚轮缩放 · {info.file_size_mb}MB · {info.format}
            </span>
          </div>
        </>
      )}
      {!info && !loading && !error && (
        <div style={{ color: '#999', padding: '20px 0', textAlign: 'center' }}>加载 bag 后显示 BEV 视图</div>
      )}
    </div>
  );
}
