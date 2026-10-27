// 运行时配置 — 容器启动时由 docker-entrypoint.sh 覆盖此文件
// 本地开发时 Vite 直接 serve 此文件，basePath 保持为空即可
window.__APP_CONFIG__ = {
  basePath: ''
}
