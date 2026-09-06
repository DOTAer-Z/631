#!/bin/sh
# 在目标机加载镜像包。用法：sh load-images.sh [faultdiag-images.tar.gz]
set -e
IN="${1:-faultdiag-images.tar.gz}"
echo "[load] 从 ${IN} 加载镜像 ..."
gunzip -c "${IN}" | docker load
echo "[load] 完成。当前镜像："
docker images | grep -E 'faultdiag|postgres' || true
echo "[load] 接着：cd deploy && docker compose -f docker-compose.prod.yaml up -d"
