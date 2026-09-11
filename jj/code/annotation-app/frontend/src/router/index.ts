import { type RouteRecordRaw } from 'vue-router'

/**
 * 标注子系统的 13 个叶子路由（真·深融合版）。
 *
 * 供两处使用：
 *  - 主系统深融合：主系统把它作为 `data-governance` 下 `annotation` 子路由的
 *    `children` 挂进主系统 hash 路由树，此时 `path` 是相对路径（相对 `annotation`），
 *    完整路径形如 `/data-governance/annotation/dashboard`。
 *  - 标注独立部署态：用它构建独立 `<RouterView/>` 树（见 `standaloneRouter.ts`）。
 *
 * 路由名全局唯一（已确认与主系统零冲突），深融合后内部 `router.push({ name })`
 * 在主系统路由作用域下照常解析。
 *
 * meta 说明：
 *  - `activeMenu`        —— 标注页内导航（AnnotationSectionShell 的水平菜单）高亮用，值是标注内页相对路径。
 *  - `mainActiveMenu`    —— 主系统侧边栏菜单高亮用，固定为 `/data-governance/annotation`（主系统「数据标注」项）。
 *  - `hidden`            —— 标注页内导航不显示该项（详情/列表等内页不单独出导航项）。
 */
export const annotationRoutes: RouteRecordRaw[] = [
  {
    path: '',
    redirect: { name: 'dashboard' },
    meta: { hidden: true },
  },
  {
    path: 'health',
    name: 'health',
    component: () => import('@annotation/views/HealthView.vue'),
    meta: {
      title: '健康检查',
      hidden: true,
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'dashboard',
    name: 'dashboard',
    component: () => import('@annotation/views/dashboard/DashboardView.vue'),
    meta: {
      title: '首页',
      activeMenu: '/dashboard',
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'packages',
    name: 'packages',
    component: () => import('@annotation/views/packages/PackageListView.vue'),
    meta: {
      title: '数据包管理',
      activeMenu: '/packages',
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'packages/:id',
    name: 'package-detail',
    component: () => import('@annotation/views/packages/PackageDetailView.vue'),
    meta: {
      title: '数据包详情',
      hidden: true,
      activeMenu: '/packages',
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'slicing',
    name: 'slicing',
    component: () => import('@annotation/views/slices/SlicingWorkbenchView.vue'),
    meta: {
      title: '数据切片',
      activeMenu: '/slicing',
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'packages/:id/slice-tasks',
    name: 'slice-task-list',
    component: () => import('@annotation/views/slices/SliceTaskListView.vue'),
    meta: {
      title: '切片任务管理',
      hidden: true,
      activeMenu: '/slicing',
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'packages/:id/slice-tasks/:taskId/windows',
    name: 'slice-window-browser',
    component: () => import('@annotation/views/slices/SliceWindowBrowserView.vue'),
    meta: {
      title: '窗口浏览',
      hidden: true,
      activeMenu: '/slicing',
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'annotation',
    name: 'annotation-workbench',
    component: () => import('@annotation/views/annotations/AnnotationWorkbenchView.vue'),
    meta: {
      title: '数据标注',
      activeMenu: '/annotation',
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'annotation/records',
    name: 'annotation-list',
    component: () => import('@annotation/views/annotations/AnnotationListView.vue'),
    meta: {
      title: '已标注记录',
      hidden: true,
      activeMenu: '/annotation',
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'annotation/fault-types',
    name: 'fault-type-manage',
    component: () => import('@annotation/views/faultTypes/FaultTypeManageView.vue'),
    meta: {
      title: '故障类型管理',
      hidden: true,
      activeMenu: '/annotation',
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'annotation/fault-type-suggestions',
    name: 'fault-type-suggestion-list',
    component: () => import('@annotation/views/faultTypes/FaultTypeSuggestionListView.vue'),
    meta: {
      title: '故障类型建议',
      hidden: true,
      activeMenu: '/annotation',
      mainActiveMenu: '/data-governance/annotation',
    },
  },
  {
    path: 'annotations',
    redirect: { name: 'annotation-workbench' },
    meta: { hidden: true },
  },
]

// 注意：本模块只导出 route 记录，不实例化 router。
// 独立部署态的 standalone router 在 standaloneRouter.ts（base='/annotate/'），
// 只有标注作为独立站点启动时才创建——主系统深融合 import { annotationRoutes } 时
// 绝不触发任何 `createWebHistory('/annotate/')`，杜绝「当前 URL 被历史模式 router 接管」
// 导致的跳落到 /annotate/...、无法退回主界面的副作用。
// （此前是把 standalone router 建在本模块顶部，主系统一 import 就被实例化。）
