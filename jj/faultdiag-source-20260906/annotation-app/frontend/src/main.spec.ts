import { beforeEach, describe, expect, it, vi } from 'vitest'

const runtime = vi.hoisted(() => {
  class PortalContextError extends Error {}
  const appComponent = {}
  const app = {
    mount: vi.fn(),
    use: vi.fn()
  }
  return {
    PortalContextError,
    app,
    appComponent,
    createApp: vi.fn(() => app),
    initializePortalContext: vi.fn<() => Promise<unknown>>()
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
  initializePortalContext: runtime.initializePortalContext,
  PortalContextError: runtime.PortalContextError
}))

function deferred() {
  let resolve!: (value: unknown) => void
  const promise = new Promise((promiseResolve) => {
    resolve = promiseResolve
  })
  return { promise, resolve }
}

describe('annotation bootstrap', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    runtime.createApp.mockImplementation(() => runtime.app)
  })

  it('waits for portal context initialization before mounting API-fetching views', async () => {
    const context = deferred()
    runtime.initializePortalContext.mockReturnValueOnce(context.promise)

    await import('./main')
    await Promise.resolve()

    expect(runtime.initializePortalContext).toHaveBeenCalledOnce()
    expect(runtime.app.mount).not.toHaveBeenCalled()

    context.resolve({ token: '', userInfo: null, namespaceId: null })
    await context.promise
    await Promise.resolve()

    expect(runtime.createApp).toHaveBeenCalledWith(runtime.appComponent, {
      initialPortalContextError: false
    })
    expect(runtime.app.mount).toHaveBeenCalledWith('#app')
  })

  it('mounts an explicit connection-error root after a typed portal context failure', async () => {
    runtime.initializePortalContext.mockRejectedValueOnce(new runtime.PortalContextError('timeout'))

    await import('./main')
    await vi.waitFor(() => expect(runtime.app.mount).toHaveBeenCalledWith('#app'))

    expect(runtime.createApp).toHaveBeenCalledWith(runtime.appComponent, {
      initialPortalContextError: true
    })
  })
})
