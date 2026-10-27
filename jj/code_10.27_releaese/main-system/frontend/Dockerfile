# 真·深融合：主前端构建时直接编译标注子系统源码。
# 因此 build context 是仓库根（见 docker-compose.yml），本 Dockerfile 用相对路径定位
# main-system/frontend 与 annotation-app/frontend/src，保持与宿主一致的目录布局，
# 使 vite 的 @annotation 别名（../../annotation-app/frontend/src）在容器内解析一致。

# Stage 1: 构建
FROM node:20-alpine AS builder

WORKDIR /app

RUN npm config set registry https://registry.npmmirror.com

# 先拷主系统前端的依赖清单，单独 npm ci（利用层缓存，源码改动不重装依赖）
COPY main-system/frontend/package.json main-system/frontend/package-lock.json /app/main-system/frontend/
RUN cd /app/main-system/frontend && npm ci

# 完整拷贝主系统前端源码（node_modules 已被 .dockerignore 排除，不覆盖 npm ci 产物）
COPY main-system/frontend/ /app/main-system/frontend/
# 拷贝标注子系统源码（供 vite @annotation 别名解析）
COPY annotation-app/frontend/src /app/annotation-app/frontend/src
# 标注源码在 /app/annotation-app 下，import vue-router 等依赖时沿目录上溯找不到 node_modules。
# 用符号链接把 `annotation-app/frontend/node_modules` 指到主前端的依赖——既解决 Node 解析，
# 又保证融合 bundle 里只有一套 vue/vue-router/pinia/element-plus/axios（单一依赖树，避免双份库）。
RUN ln -s /app/main-system/frontend/node_modules /app/annotation-app/frontend/node_modules

RUN cd /app/main-system/frontend && npm run build

# Stage 2: Nginx 服务
FROM nginx:alpine

COPY --from=builder /app/main-system/frontend/dist /usr/share/nginx/html
COPY main-system/frontend/nginx/default.conf /etc/nginx/templates/default.conf.template
COPY main-system/frontend/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh
EXPOSE 80
ENTRYPOINT ["/docker-entrypoint.sh"]
