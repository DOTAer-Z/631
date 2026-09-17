
import { createRouter, createWebHashHistory } from 'vue-router'
// 真·深融合：标注子系统的叶子路由（已去掉独立入口/布局，作为 data-governance/annotation 的 children 挂载）
import { annotationRoutes } from '@annotation/router'

const routes = [
  {
    path: '/',
    component: () => import('@/components/Layout/AppLayout.vue'),
    redirect: '/overview',
    children: [
      // 1. 系统概览
      {
        path: 'overview',
        name: 'Overview',
        component: () => import('@/views/Overview/index.vue'),
        meta: { title: '系统概览', icon: 'DataBoard' },
      },
      // 2. 数据治理（入库唯一入口）：数据导入 → 数据标注 → 知识库 / 知识图谱
      {
        path: 'data-governance',
        name: 'DataGovernance',
        component: () => import('@/views/LogAnalysis/index.vue'), // 纯 <router-view> 壳
        redirect: '/data-governance/data-import',
        meta: { title: '数据治理', icon: 'FolderOpened' },
        children: [
          {
            path: 'data-import',
            name: 'DataImport',
            component: () => import('@/views/DataImport/index.vue'),
            meta: { title: '数据导入', icon: 'FolderAdd' },
          },
          {
            path: 'annotation',
            name: 'DataProcessingPreprocess',
            // 集成方案 A：深融合版。标注子系统的 13 个 view 作为本节的子路由挂载，
            // 通过 annotationRoutes（相对叶子路径）展开，外层用 AnnotationSectionShell
            // 提供标注内页的水平导航 + <router-view/>。
            component: () => import('@/views/DataProcessing/AnnotationSectionShell.vue'),
            // 首屏默认落到标注 dashboard。深融合后标注路由是嵌套 child，用绝对路径重定向，
            // 不依赖命名路由在整体路由树里的全局解析（更稳，避免首屏白屏）。
            redirect: '/data-governance/annotation/dashboard',
            meta: { title: '数据标注', icon: 'MagicStick' },
            children: annotationRoutes,
          },
          {
            path: 'knowledge-base',
            name: 'KnowledgeBase',
            component: () => import('@/views/KnowledgeBase/index.vue'),
            meta: { title: '知识库', icon: 'Folder' },
          },
          {
            path: 'knowledge-graph',
            name: 'KnowledgeGraph',
            component: () => import('@/views/KnowledgeGraph/index.vue'),
            meta: { title: '知识图谱', icon: 'Share' },
          },
        ],
      },
      // 3. 模型管理：运行时 API 配置 + 模型训练
      {
        path: 'model-management',
        name: 'ModelManagement',
        component: () => import('@/views/ModelManagement/index.vue'),
        redirect: '/model-management/api',
        meta: { title: '模型管理', icon: 'Setting' },
        children: [
          {
            path: 'api',
            name: 'ModelApiManagement',
            component: () => import('@/views/ModelManagement/ApiManagement.vue'),
            meta: { title: 'API管理', icon: 'Connection' },
          },
          {
            path: 'training',
            name: 'ModelTraining',
            component: () => import('@/views/ModelManagement/Training.vue'),
            meta: { title: '模型训练', icon: 'Cpu' },
          },
        ],
      },
      // 4. 在线处理（分析输入唯一入口）：上传文件 / 从 DB1 选择 → 送日志分析
      {
        path: 'online-processing',
        name: 'OnlineProcessing',
        component: () => import('@/views/LogAnalysis/index.vue'), // 纯 <router-view> 壳
        redirect: '/online-processing/upload',
        meta: { title: '在线处理', icon: 'Upload' },
        children: [
          {
            path: 'upload',
            name: 'LogUpload',
            component: () => import('@/views/LogAnalysis/LogUpload.vue'),
            meta: { title: '上传文件', icon: 'Upload' },
          },
          {
            path: 'select',
            name: 'LogAnalysisMain',
            component: () => import('@/views/LogAnalysis/LogAnalysis.vue'),
            meta: { title: '文件选择', icon: 'Document' },
          },
          {
            path: 'select/:runId',
            name: 'LogAnalysisDetail',
            component: () => import('@/views/LogAnalysis/LogAnalysisDetail.vue'),
            meta: { title: '数据详情', icon: 'Document', hidden: true, activeMenu: '/online-processing/select' },
          },
        ],
      },
      // 5. 日志分析（分析后分叉：正常→预测预警 / 异常→故障诊断）
      {
        path: 'log-analysis',
        name: 'LogParse',
        component: () => import('@/views/LogAnalysis/LogParse.vue'),
        meta: { title: '日志分析', icon: 'Search' },
      },
      // 6. 故障诊断（读 DB5 异常文件）
      {
        path: 'diagnosis',
        name: 'Diagnosis',
        component: () => import('@/views/Diagnosis/index.vue'),
        meta: { title: '故障诊断', icon: 'Cpu' },
      },
      // 7. 预测预警（读 DB5 正常文件）
      {
        path: 'prediction',
        name: 'Prediction',
        component: () => import('@/views/Prediction/index.vue'),
        meta: { title: '预测预警', icon: 'DataLine' },
      },

      // ── 旧路径重定向（隐藏，避免旧书签白屏） ──────────────────────────────
      { path: 'fine-tuning', redirect: '/model-management/training', meta: { hidden: true } },
      { path: 'knowledge-base', redirect: '/data-governance/knowledge-base', meta: { hidden: true } },
      { path: 'knowledge-graph', redirect: '/data-governance/knowledge-graph', meta: { hidden: true } },
      { path: 'data-processing', redirect: '/data-governance/annotation', meta: { hidden: true } },
      { path: 'data-processing/preprocess', redirect: '/data-governance/annotation', meta: { hidden: true } },
      { path: 'data-processing/data-import', redirect: '/data-governance/data-import', meta: { hidden: true } },
      { path: 'data-processing/logs', redirect: '/online-processing/select', meta: { hidden: true } },
      {
        path: 'data-processing/logs/:runId',
        redirect: (to) => ({ name: 'LogAnalysisDetail', params: { runId: to.params.runId } }),
        meta: { hidden: true },
      },
      { path: 'prediction/data-access', redirect: '/prediction', meta: { hidden: true } },
      { path: 'prediction/software-prediction', redirect: '/prediction', meta: { hidden: true } },
      { path: 'prediction/level-alarm', redirect: '/prediction', meta: { hidden: true } },
      { path: 'log-analysis/upload', redirect: '/online-processing/upload', meta: { hidden: true } },
      { path: 'log-analysis/parse', redirect: '/log-analysis', meta: { hidden: true } },
      {
        path: 'log-analysis/analysis/:runId',
        redirect: (to) => ({ name: 'LogAnalysisDetail', params: { runId: to.params.runId } }),
        meta: { hidden: true },
      },
      { path: 'log-analysis/analysis', redirect: '/online-processing/select', meta: { hidden: true } },
    ],
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
