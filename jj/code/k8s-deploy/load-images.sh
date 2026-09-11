#!/bin/sh
# 在目标机加载镜像包。无需联网，无需源码。
# 用法：sh load-images.sh [images-integrated.tar.gz]
#       权限不足时：sudo sh load-images.sh
set -e

IN="${1:-images-integrated.tar.gz}"

# 分包交付：镜像被切成 1G 分片 images-integrated.tar.gz.NN.part。
# 若整包不存在但分片在，就先自动合并（cat 按名称顺序拼回）。
if [ ! -f "$IN" ] && ls images-integrated.tar.gz.*.part >/dev/null 2>&1; then
  echo "[load] 检测到分片，正在合并为 ${IN} ..."
  cat images-integrated.tar.gz.*.part > "$IN"
  echo "[load] 合并完成：$(ls -lh "$IN" | awk '{print $5}')"
  # 有校验和文件则校验一次，防止分片缺失/损坏
  if [ -f images-integrated.tar.gz.sha256 ]; then
    if command -v sha256sum >/dev/null 2>&1; then
      echo "[load] 校验 sha256 ..."
      sha256sum -c images-integrated.tar.gz.sha256 || {
        echo "[load] ✗ 校验失败：分片可能缺失或损坏，请重新下载全部 .part 文件。"; exit 1; }
    fi
  fi
fi

if [ ! -f "$IN" ]; then
  CANDIDATE="$(ls -t images-integrated*.tar.gz 2>/dev/null | head -n1)"
  if [ -n "$CANDIDATE" ] && [ -f "$CANDIDATE" ]; then
    IN="$CANDIDATE"
  fi
fi

if [ ! -f "$IN" ]; then
  echo "[load] 找不到镜像包 ${IN}（也没有 images-integrated.tar.gz.*.part 分片）"
  echo "       请把镜像包或全部分片放到本目录，或把整包作为参数传入。"
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "[load] 无法连接 Docker：请确认 Docker 已安装并启动。"
  echo "       权限不足时用：sudo sh load-images.sh"
  exit 1
fi

echo "[load] 加载镜像 (${IN}, 约 927MB, 1-3 分钟)..."
gunzip -c "${IN}" | docker load

echo
echo "[load] 完成。当前镜像："
docker images | grep -E 'faultdiag' || true
echo
echo "[load] 接着启动："
echo "       cp -n .env.example .env"
echo "       docker compose up -d"
