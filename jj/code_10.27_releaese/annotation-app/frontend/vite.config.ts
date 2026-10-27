import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  // 集成方案 A：作为主系统的 iframe 子应用部署在 /annotate/ 下。
  // base 决定了构建产物里 assets 的引用前缀；与 nginx 的 location /annotate/ 对应。
  // 本地独立开发时通过 VITE_BASE 覆盖（默认 /annotate/）。
  base: process.env.VITE_BASE ?? '/annotate/',
  plugins: [vue()],
  resolve: {
    alias: {
      // 标注内部 import 统一用 @annotation/（深融合后与主系统的 @/ 区分，避免两套 src 冲突）。
      // 保留 @ 别名兼容旧引用（若有遗漏的 @/ 引用也能解析到本包 src）。
      '@annotation': fileURLToPath(new URL('./src', import.meta.url)),
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    }
  },
  test: {
    environment: 'jsdom',
    globals: false,
    include: ['src/**/*.spec.ts']
  }
})
