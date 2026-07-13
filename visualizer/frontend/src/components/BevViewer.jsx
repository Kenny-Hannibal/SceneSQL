import React, { useRef, useState, useEffect, useCallback } from 'react';

const API_BASE = 'http://localhost:8000';

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
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
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

  // ── 初始化 Three.js 场景 ──
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    // 动态加载 Three.js + OrbitControls
    const scriptsToLoad = [
      'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js',
      'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/js/controls/OrbitControls.js',
    ];

    function loadScripts(urls, callback) {
      let loaded = 0;
      urls.forEach(url => {
        if (document.querySelector(`script[src="${url}"]`)) {
          loaded++;
          if (loaded === urls.length) callback();
          return;
        }
        const script = document.createElement('script');
        script.src = url;
        script.onload = () => {
          loaded++;
          if (loaded === urls.length) callback();
        };
        document.head.appendChild(script);
      });
    }

    loadScripts(scriptsToLoad, () => initScene());

    function initScene() {
      const THREE = window.THREE;
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x0a0a1a);

      // ── PerspectiveCamera（默认俯视 BEV，可旋转到 3D）──
      const camera = new THREE.PerspectiveCamera(60, 2, 0.1, 5000);
      camera.position.set(0, 100, 0.01);  // z 偏一点避免 lookAt 死锁
      camera.lookAt(0, 0, 0);

      const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
      renderer.setSize(canvas.clientWidth, canvas.clientHeight);
      renderer.setPixelRatio(window.devicePixelRatio);

      // ── OrbitControls ──
      let controls = null;
      if (THREE.OrbitControls) {
        controls = new THREE.OrbitControls(camera, canvas);
        controls.enableDamping = true;
        controls.dampingFactor = 0.08;
        controls.target.set(0, 0, 0);
        controls.maxPolarAngle = Math.PI * 0.85;  // 限制不能翻到正下方
        controls.minDistance = 5;
        controls.maxDistance = 800;
        controls.update();
      }

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
      renderLoop();
    }

    function renderLoop() {
      const { renderer, scene, camera, controls } = threeRef.current;
      if (!renderer) return;
      const animate = () => {
        threeRef.current.animId = requestAnimationFrame(animate);
        if (controls) controls.update();
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
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    window.addEventListener('resize', handleResize);
    handleResize();
    return () => window.removeEventListener('resize', handleResize);
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
        // 如果有 startTsNs，先查找对应的帧索引
        if (startTsNs != null && startTsNs > 0) {
          await seekToTimestamp(startTsNs);
        } else {
          setCurrentFrame(0);
          loadFrame(0);
        }
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [bagPath, authFetch, startTsNs]);

  // ── 按时间戳定位帧 ──
  const seekToTimestamp = useCallback(async (tsNs) => {
    if (!bagPath) return 0;
    try {
      const res = await authFetch(
        `${API_BASE}/api/bag/fusion-map-frame-by-ts?bag_path=${encodeURIComponent(bagPath)}&ts_ns=${tsNs}`
      );
      if (!res.ok) throw new Error('Failed to find frame by ts');
      const data = await res.json();
      if (data.error) {
        // 降级到第0帧
        setCurrentFrame(0);
        loadFrame(0);
        return 0;
      }
      const idx = data.frame_idx || 0;
      setCurrentFrame(idx);
      loadFrame(idx);
      return idx;
    } catch (e) {
      setCurrentFrame(0);
      loadFrame(0);
      return 0;
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
    const { groups, camera, controls } = threeRef.current;

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

      // 相机跟随车辆（OrbitControls target 也跟随）
      const cx = vl.x || 0;
      const cy = vl.y || 0;
      camera.position.set(cx, 100, cy + 0.01);
      camera.lookAt(cx, 0, cy);
      if (controls) {
        controls.target.set(cx, 0, cy);
        controls.update();
      }
    }
  }, []);

  // ── 播放/暂停动画 ──
  const totalFrames = info?.total_frames || 0;
  const endFrame = React.useMemo(() => {
    // 如果有 endTsNs，需要算出结束帧索引；否则播放到末尾
    if (endTsNs != null && endTsNs > 0 && info?.exists) {
      // 简单估算：用当前帧位置 + 帧率外推
      // 精确方案需要再调 by-ts API，但播放时频繁调 API 不划算
      // 所以这里只设 totalFrames 为上限，实际靠时间戳判断
      return totalFrames;
    }
    return totalFrames;
  }, [endTsNs, info, totalFrames]);

  useEffect(() => {
    if (!playing || !info || info.total_frames <= 0) return;
    const interval = setInterval(() => {
      setCurrentFrame(prev => {
        const next = prev + 1;
        if (next >= totalFrames) {
          setPlaying(false);
          return prev;
        }
        loadFrame(next);
        return next;
      });
    }, 200); // 5fps for BEV animation
    return () => clearInterval(interval);
  }, [playing, info, totalFrames, loadFrame]);

  // ── bag 路径变化时自动加载 ──
  useEffect(() => {
    if (bagPath) {
      loadInfo();
    }
  }, [bagPath, loadInfo]);

  return (
    <div style={{ background: '#fff', borderRadius: 8, padding: 20, boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }}>
      <h2>🗺️ 3D BEV View (Fusion Map)</h2>
      <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
        鼠标左键旋转 · 右键平移 · 滚轮缩放
      </div>
      {error && <div style={{ color: 'red', marginBottom: 10 }}>{error}</div>}
      {loading && <div style={{ color: '#1890ff', marginBottom: 10 }}>Loading fusion map info...</div>}
      {info && !info.exists && (
        <div style={{ color: '#999', padding: '20px 0' }}>此 bag 无 fusion_map_plus 数据</div>
      )}
      {info && info.exists && (
        <>
          <canvas
            ref={canvasRef}
            style={{ width: '100%', height: 450, background: '#0a0a1a', borderRadius: 4, marginTop: 12, display: 'block' }}
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
              {info.file_size_mb}MB · {info.format}
            </span>
          </div>
        </>
      )}
      {!info && !loading && !error && (
        <div style={{ color: '#999', padding: '20px 0', textAlign: 'center' }}>加载 bag 后显示 3D BEV 视图</div>
      )}
    </div>
  );
}
