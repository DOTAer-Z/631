import { createRouter, createWebHistory } from 'vue-router'

import AppLayout from '@annotation/layouts/AppLayout.vue'

import { annotationRoutes } from './index'

/**
 * 标注独立部署态 router（base='/annotate/'）。
 *
 * 仅当标注作为独立站点在 /annotate/ 下打开时由 main.ts 实例化。
 * 主系统真·深融合不会 import 本模块——它只消费 router/index.ts 的 `annotationRoutes`，
 * 因此融合页面里绝不会创建 base='/annotate/' 的历史模式 router，
 * 避免「当前 URL 被该 router 接管 → 跳落到 /annotate/...、无法退回主界面」。
 */
export const standaloneRouter = createRouter({
  // 集成方案 A：嵌入主系统时，前端通过 /annotate/ 路径提供（nginx 反代）。
  // base 必须与 Vite base 一致；createWebHistory 接收的是 router base。
  history: createWebHistory('/annotate/'),
  routes: [
    {
      path: '/',
      component: AppLayout,
      children: annotationRoutes,
    },
  ],
})

export default standaloneRouter
