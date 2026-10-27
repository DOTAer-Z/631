#!/bin/sh
# 把三个镜像打包成一个可移植的 tar.gz，便于拷到另一台电脑/服务器离线加载。
# 用法：sh deploy/save-images.sh   → 生成 faultdiag-images.tar.gz
set -e
OUT="${1:-faultdiag-images.tar.gz}"
echo "[save] 导出 faultdiag-backend / faultdiag-frontend / postgres:16 → ${OUT}"
docker save faultdiag-backend:latest faultdiag-frontend:latest postgres:16 | gzip > "${OUT}"
echo "[save] 完成：$(du -h "${OUT}" | cut -f1)  ${OUT}"
echo "[save] 拷贝到目标机后执行：sh load-images.sh ${OUT}"
