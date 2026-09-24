import { beforeEach, describe, expect, it, vi } from 'vitest'

const runtime = vi.hoisted(() => {
  const appComponent = {}
  const app = {
    mount: vi.fn(),
    use: vi.fn()
  }
  return {
    app,
    appComponent,
    createApp: vi.fn(() => app),
    setPortalContext: vi.fn(),
    EMPTY_CONTEXT: Object.freeze({ token: '', userInfo: null, namespaceId: null })
  }
})

vi.mock('vue', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue')>()),
  createApp: runtime.createApp
}))
vi.mock('pinia', () => ({ createPinia: vi.fn(() => ({})) }))
vi.mock('element-plus', () => ({ default: {} }))
vi.mock('./App.vue', () => ({ default: runtime.appComponent }))
vi.mock('./router/standaloneRouter', () => ({ default: {} }))
vi.mock('./platform/portalContext', () => ({
  setPortalContext: runtime.setPortalContext,
  EMPTY_CONTEXT: runtime.EMPTY_CONTEXT
}))

describe('annotation standalone bootstrap', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    runtime.createApp.mockImplementation(() => runtime.app)
  })

  // 握手移除后 bootstrap 是同步的:不再 await 门户上下文,直接以匿名上下文挂载。
  it('mounts synchronously with an anonymous context and no handshake await', async () => {
    await import('./main')

    expect(runtime.setPortalContext).toHaveBeenCalledWith(runtime.EMPTY_CONTEXT)
    expect(runtime.createApp).toHaveBeenCalledWith(runtime.appComponent)
    expect(runtime.app.mount).toHaveBeenCalledWith('#app')
  })

  it('no longer passes an initialPortalContextError prop', async () => {
    await import('./main')

    const [, props] = runtime.createApp.mock.calls[0] as [unknown, unknown]
    expect(props).toBeUndefined()
  })
})
