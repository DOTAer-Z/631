import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  PortalContextClearedError,
  PortalContextOriginError,
  PortalContextTimeoutError,
  clearPortalContext,
  getAnnotationAppOrigin,
  getPortalContext,
  initializePortalContext,
  initializePortalContextStrict
} from './portalContext'

type MessageListener = (event: MessageEvent) => void
const REQUEST_IDS = ['request-id-000001', 'request-id-000002', 'request-id-000003']

function createRuntime(
  referrer = 'https://child.example/main/',
  requestIds = REQUEST_IDS
) {
  const listeners = new Set<MessageListener>()
  const parent = { postMessage: vi.fn() }
  let requestIndex = 0
  const windowRef = {
    parent,
    location: { href: 'https://child.example/annotate/' },
    addEventListener: vi.fn((type: string, listener: MessageListener) => {
      if (type === 'message') listeners.add(listener)
    }),
    removeEventListener: vi.fn((type: string, listener: MessageListener) => {
      if (type === 'message') listeners.delete(listener)
    })
  }

  return {
    documentRef: { referrer },
    listeners,
    parent,
    requestIdFactory: vi.fn(() => requestIds[requestIndex++] ?? REQUEST_IDS[2]),
    windowRef,
    dispatch(event: Partial<MessageEvent>) {
      for (const listener of [...listeners]) listener(event as MessageEvent)
    }
  }
}

function validResponse(
  requestId = REQUEST_IDS[0],
  overrides: Record<string, unknown> = {}
) {
  return {
    type: 'faultdiag:context:response',
    version: 2,
    requestId,
    token: 'portal-token',
    userInfo: { id: 7, roles: ['operator'] },
    namespaceId: 'factory-a',
    ...overrides
  }
}

describe('portal context', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    clearPortalContext()
  })

  afterEach(() => {
    clearPortalContext()
    vi.useRealTimers()
  })

  it('returns the frozen empty context immediately in top-level independent mode', async () => {
    const topLevelWindow = {
      location: { href: 'https://child.example/annotate/' },
      addEventListener: vi.fn(),
      removeEventListener: vi.fn()
    }
    Object.assign(topLevelWindow, { parent: topLevelWindow })

    const result = await initializePortalContextStrict({
      windowRef: topLevelWindow,
      documentRef: { referrer: '' }
    })

    expect(result).toEqual({ token: '', userInfo: null, namespaceId: null })
    expect(Object.isFrozen(result)).toBe(true)
    expect(getPortalContext()).toBe(result)
    expect(topLevelWindow.addEventListener).not.toHaveBeenCalled()
  })

  it('derives the parent origin from referrer and posts only to that exact origin', () => {
    const runtime = createRuntime('https://child.example/main/path?ignored=1')

    const pending = initializePortalContextStrict(runtime)
    void pending.catch(() => undefined)

    expect(runtime.parent.postMessage).toHaveBeenCalledOnce()
    expect(runtime.parent.postMessage).toHaveBeenCalledWith(
      { type: 'faultdiag:context:request', version: 2, requestId: REQUEST_IDS[0] },
      'https://child.example'
    )
    expect(runtime.parent.postMessage).not.toHaveBeenCalledWith(expect.anything(), '*')
  })

  it.each([
    'javascript:alert(1)',
    'https://user:password@child.example/main/',
    'https://child.example.evil.test/main/'
  ])('rejects an unsafe or non-parent referrer without posting: %s', async (referrer) => {
    const runtime = createRuntime(referrer)

    await expect(initializePortalContextStrict(runtime)).rejects.toBeInstanceOf(PortalContextOriginError)
    expect(runtime.parent.postMessage).not.toHaveBeenCalled()
  })

  it('accepts a valid parent response and stores only a deeply cloned frozen allowlist', async () => {
    const runtime = createRuntime()
    const response = validResponse(REQUEST_IDS[0], { ignored: 'not-stored' })
    const promise = initializePortalContextStrict(runtime)

    runtime.dispatch({
      source: runtime.parent as unknown as MessageEventSource,
      origin: 'https://child.example',
      data: response
    })
    const context = await promise

    expect(context).toEqual({
      token: 'portal-token',
      userInfo: { id: 7, roles: ['operator'] },
      namespaceId: 'factory-a'
    })
    expect('ignored' in context).toBe(false)
    expect(Object.isFrozen(context)).toBe(true)
    expect(Object.isFrozen(context.userInfo)).toBe(true)
    expect(Object.isFrozen((context.userInfo as { roles: string[] }).roles)).toBe(true)

    ;(response.userInfo as { roles: string[] }).roles[0] = 'changed'
    expect((getPortalContext().userInfo as { roles: string[] }).roles).toEqual(['operator'])
    expect(runtime.listeners.size).toBe(0)
  })

  it('ignores responses with the wrong parent source, origin, exact type, or version', async () => {
    const runtime = createRuntime()
    let settled = false
    const promise = initializePortalContextStrict(runtime).finally(() => {
      settled = true
    })

    runtime.dispatch({ source: {}, origin: 'https://child.example', data: validResponse() })
    runtime.dispatch({ source: runtime.parent, origin: 'https://portal.example', data: validResponse() })
    runtime.dispatch({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse(REQUEST_IDS[0], { type: 'faultdiag:context:response:extra' })
    })
    runtime.dispatch({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse(REQUEST_IDS[0], { version: '2' })
    })
    await Promise.resolve()
    expect(settled).toBe(false)

    runtime.dispatch({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse()
    })
    await expect(promise).resolves.toMatchObject({ token: 'portal-token' })
  })

  it('rejects unsafe nested accessors without executing them', async () => {
    const runtime = createRuntime()
    const roles: string[] = []
    let accessorCalls = 0
    Object.defineProperty(roles, '0', {
      enumerable: true,
      get() {
        accessorCalls += 1
        return 'must-not-run'
      }
    })
    roles.length = 1
    const promise = initializePortalContextStrict(runtime)

    runtime.dispatch({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse(REQUEST_IDS[0], { userInfo: { roles } })
    })
    await Promise.resolve()

    expect(accessorCalls).toBe(0)
    expect(getPortalContext().token).toBe('')

    runtime.dispatch({ source: runtime.parent, origin: 'https://child.example', data: validResponse() })
    await expect(promise).resolves.toMatchObject({ token: 'portal-token' })
  })

  it('rejects an over-wide payload before reading property descriptors or getters', async () => {
    const runtime = createRuntime()
    const wideTarget: Record<string, unknown> = {}
    let getterCalls = 0
    let descriptorCalls = 0
    for (let index = 0; index < 300; index += 1) {
      Object.defineProperty(wideTarget, `field${index}`, {
        enumerable: true,
        get() {
          getterCalls += 1
          return index
        }
      })
    }
    const wideUserInfo = new Proxy(wideTarget, {
      getOwnPropertyDescriptor(target, property) {
        descriptorCalls += 1
        return Reflect.getOwnPropertyDescriptor(target, property)
      }
    })
    const promise = initializePortalContextStrict(runtime)
    void promise.catch(() => undefined)

    runtime.dispatch({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse(REQUEST_IDS[0], { userInfo: wideUserInfo })
    })
    await Promise.resolve()

    expect(descriptorCalls).toBe(0)
    expect(getterCalls).toBe(0)
    expect(getPortalContext().token).toBe('')

    runtime.dispatch({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse()
    })
    await expect(promise).resolves.toMatchObject({ token: 'portal-token' })
  })

  it('rejects with a typed timeout after five seconds and permits a fresh retry', async () => {
    const runtime = createRuntime()
    const first = initializePortalContextStrict(runtime)
    const firstRejection = expect(first).rejects.toMatchObject({
      name: 'PortalContextTimeoutError',
      code: 'PORTAL_CONTEXT_TIMEOUT'
    })

    await vi.advanceTimersByTimeAsync(4_999)
    expect(runtime.listeners.size).toBe(1)
    await vi.advanceTimersByTimeAsync(1)
    await firstRejection
    await expect(first).rejects.toBeInstanceOf(PortalContextTimeoutError)
    expect(runtime.listeners.size).toBe(0)

    const retry = initializePortalContextStrict(runtime)
    expect(retry).not.toBe(first)
    expect(runtime.parent.postMessage).toHaveBeenCalledTimes(2)
    runtime.dispatch({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse(REQUEST_IDS[1])
    })
    await expect(retry).resolves.toMatchObject({ token: 'portal-token' })
  })

  it('shares one pending promise and sends one request for repeated initialization', () => {
    const runtime = createRuntime()

    const first = initializePortalContextStrict(runtime)
    const second = initializePortalContextStrict(runtime)

    expect(second).toBe(first)
    expect(runtime.parent.postMessage).toHaveBeenCalledOnce()
    void first.catch(() => undefined)
  })

  it('clear removes pending resources, clears memory, and prevents stale responses polluting a retry', async () => {
    const runtime = createRuntime()
    const first = initializePortalContextStrict(runtime)
    const staleListener = [...runtime.listeners][0]
    const firstRejection = expect(first).rejects.toBeInstanceOf(PortalContextClearedError)

    clearPortalContext()

    await firstRejection
    expect(runtime.listeners.size).toBe(0)
    expect(vi.getTimerCount()).toBe(0)
    expect(getPortalContext()).toEqual({ token: '', userInfo: null, namespaceId: null })

    const retry = initializePortalContextStrict(runtime)
    staleListener({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse(REQUEST_IDS[0], { token: 'stale-token' })
    } as unknown as MessageEvent)
    expect(getPortalContext().token).toBe('')

    runtime.dispatch({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse(REQUEST_IDS[1], { token: 'fresh-token' })
    })
    await expect(retry).resolves.toMatchObject({ token: 'fresh-token' })

    clearPortalContext()
    expect(getPortalContext()).toEqual({ token: '', userInfo: null, namespaceId: null })
    expect(Object.isFrozen(getPortalContext())).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('ignores an old wire response delivered to the current retry listener', async () => {
    const runtime = createRuntime()
    const first = initializePortalContextStrict(runtime)
    const firstRequest = runtime.parent.postMessage.mock.calls[0][0] as Record<string, unknown>
    const firstRejection = expect(first).rejects.toBeInstanceOf(PortalContextClearedError)
    clearPortalContext()
    await firstRejection

    let settled = false
    const retry = initializePortalContextStrict(runtime).finally(() => {
      settled = true
    })
    const secondRequest = runtime.parent.postMessage.mock.calls[1][0] as Record<string, unknown>

    runtime.dispatch({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse(firstRequest.requestId as string, {
        version: firstRequest.version,
        token: 'stale-token'
      })
    })
    await Promise.resolve()

    expect(settled).toBe(false)
    expect(getPortalContext().token).toBe('')

    runtime.dispatch({
      source: runtime.parent,
      origin: 'https://child.example',
      data: validResponse(secondRequest.requestId as string, {
        version: secondRequest.version,
        token: 'fresh-token'
      })
    })
    await expect(retry).resolves.toMatchObject({ token: 'fresh-token' })
  })

  it('returns the annotation child origin and rejects credential-bearing locations', () => {
    expect(getAnnotationAppOrigin({ location: { href: 'https://child.example/annotate/' } })).toBe(
      'https://child.example'
    )
    expect(() =>
      getAnnotationAppOrigin({ location: { href: 'https://user:password@child.example/annotate/' } })
    ).toThrow(PortalContextOriginError)
  })
})

describe('portal context default mode', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    clearPortalContext()
  })

  afterEach(() => {
    clearPortalContext()
    vi.useRealTimers()
  })

  it('degrades to the empty context instead of rejecting on a non-parent referrer', async () => {
    const runtime = createRuntime('https://child.example.evil.test/main/')

    await expect(initializePortalContext(runtime)).resolves.toEqual({
      token: '',
      userInfo: null,
      namespaceId: null
    })
    expect(runtime.parent.postMessage).not.toHaveBeenCalled()
    expect(getPortalContext()).toEqual({ token: '', userInfo: null, namespaceId: null })
  })

  it('degrades to the empty context after the handshake times out', async () => {
    const runtime = createRuntime()
    const promise = initializePortalContext(runtime)

    await vi.advanceTimersByTimeAsync(5_000)
    await expect(promise).resolves.toEqual({ token: '', userInfo: null, namespaceId: null })
    expect(getPortalContext()).toEqual({ token: '', userInfo: null, namespaceId: null })
  })

  it('still resolves with a real context when a valid parent responds', async () => {
    const runtime = createRuntime()
    const promise = initializePortalContext(runtime)

    runtime.dispatch({
      source: runtime.parent as unknown as MessageEventSource,
      origin: 'https://child.example',
      data: validResponse(REQUEST_IDS[0])
    })
    await expect(promise).resolves.toMatchObject({ token: 'portal-token' })
  })
})
