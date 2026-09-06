import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router/standaloneRouter'
import {
  PortalContextError,
  initializePortalContext
} from './platform/portalContext'
import './style.css'

export async function bootstrap() {
  let initialPortalContextError = false
  try {
    await initializePortalContext()
  } catch (error) {
    if (!(error instanceof PortalContextError)) throw error
    initialPortalContextError = true
  }

  const app = createApp(App, { initialPortalContextError })

  app.use(createPinia())
  app.use(router)
  app.use(ElementPlus)

  app.mount('#app')
}

void bootstrap()
