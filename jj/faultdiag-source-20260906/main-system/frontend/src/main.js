import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import './assets/styles/global.css'
// 真·深融合：标注 view 依赖的通用部件类（page-shell/page-toolbar/console-card 等）。
// 主系统不 import 标注 style.css（含 html/body/#app 全局规则），这里只引入共享部件类。
import '@annotation/styles/annotation-shared.css'
import App from './App.vue'
import router from './router'
import {
  APP_CLEANUP_KEY,
  createAppRuntime,
  installWujieLifecycle,
} from './utils/wujieLifecycle.mjs'
import { installRequestScope } from './utils/request'
// 真·深融合：把主系统的登录态注入标注子系统的 portalContext（标注 http.ts 据此取 token → X-Token）
import { setPortalContext } from '@annotation/platform/portalContext'
import { getPlatformContext } from './utils/platform'

function createMyApp(cleanup) {
  installRequestScope(cleanup)
  const app = createApp(App)
  injectAnnotationContext()
  app.provide(APP_CLEANUP_KEY, cleanup)
  for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
    app.component(key, component)
  }
  app.use(createPinia())
  app.use(router)
  app.use(ElementPlus, { locale: zhCn })
  return app
}

// 深融合：标注不再是 iframe 子应用，登录态由主系统直接注入（wujie props → 标注 portalContext）。
// 标注 http.ts 的请求拦截器从 getPortalContext().token 读 token 注入 X-Token。
function injectAnnotationContext() {
  try {
    const ctx = getPlatformContext()
    setPortalContext({
      token: ctx.token || '',
      userInfo: ctx.userInfo ?? null,
      namespaceId: ctx.namespaceId ?? null,
    })
  } catch {
    // 注入失败不阻断主系统启动；标注请求时 token 为空则匿名（后端不强制鉴权）。
    // 注意：这里刻意不输出任何日志——标注 portalContext 属于敏感登录态，统一不落日志/存储。
  }
}

const runtime = createAppRuntime(createMyApp)
installWujieLifecycle(window, runtime)
