import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// CRA → Vite 迁移（2026-08-25）
// - outDir 保持 'build'：deploy.sh 静态服务路径不变，后端无感切换
// - define 兼容旧的 process.env.REACT_APP_API_BASE 用法（src 里 3 处在用）
// - plugin-react include .js：hooks 文件（useStrategies.js 等）以 .js 命名
export default defineConfig({
  plugins: [
    react({ include: '**/*.{js,jsx}' }),
  ],
  define: {
    'process.env.REACT_APP_API_BASE': JSON.stringify(process.env.REACT_APP_API_BASE || ''),
  },
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:30001',
    },
  },
  build: {
    outDir: 'build',
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // 大体积分包：three.js / codemirror 独立 chunk，浏览器可并行缓存
        manualChunks: {
          three: ['three'],
          codemirror: ['@uiw/react-codemirror', '@codemirror/lang-sql', '@codemirror/state', '@codemirror/view'],
        },
      },
    },
  },
});
