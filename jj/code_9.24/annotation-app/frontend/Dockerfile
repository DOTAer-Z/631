# 集成方案 A 用：标注子系统前端（生产构建 + nginx 静态托管）
#
# 与原 Dockerfile（dev server）的区别：
#   - 原版 CMD 为 `npm run dev` 提供 :5173；浏览器需要 CORS 才能跨源访问后端。
#   - 现版多阶段构建：vue-tsc + vite build → 由 nginx 提供静态资源。
#   - base 路径 /annotate/ 在 vite.config.ts 中固化，构建产物自带前缀。
#   - 该镜像最终被组合 compose 中的 frontend-annotate 引用，监听 :80。
#     主系统 nginx 通过 `location /annotate/ { proxy_pass ... }` 反代到这里。

FROM node:20-alpine AS builder

WORKDIR /app/frontend

RUN npm config set registry https://registry.npmmirror.com

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./

# VITE_API_BASE_URL 走同源 /annotate-api/v1，由主系统 nginx 反代到 backend-annotate:8000
ENV VITE_API_BASE_URL=/annotate-api/v1
RUN npm run build

FROM nginx:alpine

# 标注子系统的静态资源放到 /annotate/ 子路径，与 vite base / vue-router base 对齐
COPY --from=builder /app/frontend/dist /usr/share/nginx/html/annotate

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
