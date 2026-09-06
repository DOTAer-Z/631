#!/bin/sh
# 在源机（开发机）打包全部 6 个镜像为 tar.gz，并按 <1G 分片（未压缩整包约 3GB）。
# 前置：先从 631change 源码构建镜像：
#   cd /home/junjiezuo/631-fault/631change
#   docker compose build                 # 产出 backend/frontend/backend-annotate/frontend-annotate
#   docker pull postgres:16 ; docker pull postgres:16-alpine
# 用法：sh save-images.sh              （权限不足加 sudo）
# 输出：images-integrated.tar.gz.NN.part（压缩后约 927M，通常单分片）+ sha256
#       需要训练镜像时另见 README-k8s.md「附：手动追加训练镜像」
set -e

OUT="${OUT:-images-integrated.tar.gz}"

echo "[save] 1/2 给 postgres 打集成别名（不动原 postgres:16 / postgres:16-alpine）..."
docker tag postgres:16        faultdiag-postgres:16                 2>/dev/null || true
docker tag postgres:16-alpine faultdiag-postgres-annotate:16-alpine 2>/dev/null || true

# 6 镜像精简包（无训练镜像）。需要训练时手动追加 faultdiag-training-worker:latest。
IMAGES="
faultdiag-backend:latest
faultdiag-frontend:integrated
faultdiag-backend-annotate:latest
faultdiag-frontend-annotate:latest
faultdiag-postgres:16
faultdiag-postgres-annotate:16-alpine
"

echo "[save] 2/2 docker save → ${OUT}（6 镜像，约 3GB 未压缩，数分钟）..."
# shellcheck disable=SC2086
docker save $IMAGES | gzip -1 > "${OUT}"

echo "[save] 分片（每片 <1GB）..."
rm -f "${OUT}".*.part
split -b 1024m -d -a 2 "${OUT}" "${OUT}."
for p in "${OUT}".0*; do
  mv "$p" "${p}.part"
done

echo "[save] 校验和..."
sha256sum "${OUT}" > "${OUT}.sha256"

ls -lh "${OUT}".*.part
echo "[save] 完成。把全部分片 + ${OUT}.sha256 与本部署目录一起拷贝到目标机即可（load-images.sh 自动合并）。"
