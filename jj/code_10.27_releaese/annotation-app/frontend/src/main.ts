import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router/standaloneRouter'
import { EMPTY_CONTEXT, setPortalContext } from './platform/portalContext'
import './style.css'

/**
 * 标注独立部署入口。
 *
 * 深融合后 postMessage 握手已移除:独立部署直接以匿名上下文启动(后端不强制鉴权),
 * 不再有「等待门户上下文」的异步前置,也不再有「门户连接失败」页。
 * 融合态走主系统 main.js 的注入路径,不经过本文件。
 */
export function bootstrap() {
  setPortalContext(EMPTY_CONTEXT)

  const app = createApp(App)

  app.use(createPinia())
  app.use(router)
  app.use(ElementPlus)

  app.mount('#app')
}

bootstrap()
