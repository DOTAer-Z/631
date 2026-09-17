import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  base: '/',
  plugins: [
    vue(),
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
  ],
  resolve: {
    alias: {
      // 主系统自身源码
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // 真·深融合：标注子系统源码（标注内部 import 已统一改用 @annotation/ 别名，
      // 以区分主系统的 @/，避免两套 src 互相解析错误）
      // 相对路径：vite.config 在 main-system/frontend/，上溯两级到 631_9.4/，再进 annotation-app/frontend/src
      '@annotation': fileURLToPath(new URL('../../annotation-app/frontend/src', import.meta.url)),
    },
  },
  define: {
    // 标注 http.ts 读取 import.meta.env.VITE_API_BASE_URL 决定 API 前缀。
    // 主系统构建时该 env 未定义 → 标注会 fallback 到 /api（主系统后端，错）。
    // 深融合后标注 API 仍走主系统 nginx 的 /annotate-api/v1 反代，这里强制注入。
    'import.meta.env.VITE_API_BASE_URL': JSON.stringify('/annotate-api/v1'),
  },
  server: {
    host: '0.0.0.0',
    open: true,
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path,
      },
    },
  },
})

