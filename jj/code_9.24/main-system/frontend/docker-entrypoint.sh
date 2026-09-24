#!/bin/sh
set -eu

# ============================================================
# 运行时配置注入
# 环境变量（K8s env / docker run -e / docker-compose environment）：
#   BASE_PATH  子路径前缀，如 /faultdiag，留空则根路径部署
# ============================================================

BASE_PATH="${BASE_PATH:-}"

# nginx 模板已改用固定上游（backend:8000 / frontend-annotate:80 / backend-annotate:8000），
# 不再从环境变量拼 proxy_pass——K8s 注入的 BACKEND_PORT 形如 tcp://10.x.x.x:8000，
# 拼出来是非法的 tcp:// 协议，会导致 nginx 启动失败。

# ── 步骤 1：写入前端运行时配置 config.js ──────────────────────────────
# 覆盖 dist/config.js，供 request.js 在浏览器中读取 basePath
cat > /usr/share/nginx/html/config.js << JSEOF
window.__APP_CONFIG__ = {
  basePath: '${BASE_PATH}'
};
JSEOF

# ── 步骤 2：生成 nginx 配置 ────────────────────────────────────────────
if [ -z "$BASE_PATH" ]; then
    # ── 情况 A：根路径部署（BASE_PATH=""）
    # 模板已改用固定上游（backend:8000 / frontend-annotate:80 / backend-annotate:8000），
    # 不再含任何 ${...} 占位符，因此无需再做运行时变量替换。
    cp /etc/nginx/templates/default.conf.template /etc/nginx/conf.d/default.conf

else
    # ── 情况 B：子路径部署（BASE_PATH=/faultdiag 等）
    # 完全动态生成 nginx 配置，不再依赖模板文件
    #
    # 路径对应关系（以 BASE_PATH=/faultdiag 为例）：
    #   浏览器请求   /faultdiag/api/v1/xxx
    #   nginx 匹配   location /faultdiag/api/
    #   proxy_pass   http://规范化后端地址      → 后端收到 /api/v1/xxx
    #
    #   浏览器请求   /faultdiag/assets/app.js
    #   nginx 匹配   location /faultdiag/
    #   alias        /usr/share/nginx/html/ → 实际文件 /usr/share/nginx/html/assets/app.js
    #
    # 注意：\$uri \$host 等以反斜杠转义，避免被 shell 展开，
    #       写入文件后成为 nginx 的合法变量 $uri $host

    cat > /etc/nginx/conf.d/default.conf << NGINXEOF
server {
    listen 80;
    server_name _;
    index index.html;

    # API 反代：剥离 BASE_PATH 前缀，转发给后端
    # ${BASE_PATH}/api/v1/xxx → 后端 /api/v1/xxx
    # 固定上游 backend:8000（不再用 BACKEND_PORT 拼接，规避 K8s 注入 tcp:// 的问题）
    location ${BASE_PATH}/api/ {
        rewrite ^${BASE_PATH}/api/(.*)\$ /api/\$1 break;
        proxy_pass http://backend:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }

    # 静态文件：alias 映射 BASE_PATH/ → dist 根目录
    # ${BASE_PATH}/assets/app.js → /usr/share/nginx/html/assets/app.js
    location ${BASE_PATH}/ {
        alias /usr/share/nginx/html/;
        add_header Access-Control-Allow-Origin "*" always;
        try_files \$uri @spa_fallback;
    }

    # SPA 回退：找不到静态文件时返回 index.html
    location @spa_fallback {
        root /usr/share/nginx/html;
        add_header Access-Control-Allow-Origin "*" always;
        try_files /index.html =404;
    }

    gzip on;
    gzip_types text/plain application/json application/javascript text/css application/xml;
    gzip_min_length 1024;
}
NGINXEOF

fi

# ── 步骤 3：启动 nginx（前台运行）─────────────────────────────────────
exec nginx -g 'daemon off;'
