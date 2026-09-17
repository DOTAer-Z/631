import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  PortalContextOriginError,
  clearPortalContext,
  getAnnotationAppOrigin,
  getPortalContext,
  setPortalContext
} from './portalContext'
import {
  clearAnnotationPlatformRuntime,
  setAnnotationPlatformRuntime
} from './runtime'

const HOST_ORIGIN = 'https://172.30.6.63:30080'
const PORTAL_ORIGIN = 'https://172.30.6.59:30082'

function installHost(origin: string, token = '') {
  setAnnotationPlatformRuntime({
    getOrigin: () => origin,
    getContext: () => ({ token, userInfo: null, namespaceId: null })
  })
}

describe('getAnnotationAppOrigin', () => {
  beforeEach(() => {
    clearAnnotationPlatformRuntime()
    clearPortalContext()
  })

  afterEach(() => {
    clearAnnotationPlatformRuntime()
    clearPortalContext()
  })

  // 这是本次根因修复的核心断言:wujie 下标注必须解析到**集成入口** origin
  // (带 /annotate-api/ 反代的 nginx),而不是宿主门户 origin。宿主 getAppOrigin 已
  // wujie-aware,标注经注入的运行时读取它,因此不可能再与宿主漂移。
  it('resolves the host-provided origin, ignoring the portal window location', () => {
    installHost(HOST_ORIGIN)

    expect(getAnnotationAppOrigin({ location: { href: `${PORTAL_ORIGIN}/portal/` } }))
      .toBe(HOST_ORIGIN)
  })

  it('re-reads the host origin on every call (no module-load snapshot)', () => {
    let origin = HOST_ORIGIN
    setAnnotationPlatformRuntime({
      getOrigin: () => origin,
      getContext: () => ({ token: '', userInfo: null, namespaceId: null })
    })

    expect(getAnnotationAppOrigin()).toBe(HOST_ORIGIN)
    origin = 'https://relocated.example:31000'
    expect(getAnnotationAppOrigin()).toBe('https://relocated.example:31000')
  })

  it('falls back to standalone detection when the host runtime fails', () => {
    setAnnotationPlatformRuntime({
      getOrigin: () => { throw new Error('host failure') },
      getContext: () => ({ token: '', userInfo: null, namespaceId: null })
    })

    expect(getAnnotationAppOrigin({ location: { href: 'https://solo.example/annotate/' } }))
      .toBe('https://solo.example')
  })

  it('returns the annotation origin standalone and rejects credential-bearing locations', () => {
    expect(getAnnotationAppOrigin({ location: { href: 'https://child.example/annotate/' } })).toBe(
      'https://child.example'
    )
    expect(() =>
      getAnnotationAppOrigin({ location: { href: 'https://user:password@child.example/annotate/' } })
    ).toThrow(PortalContextOriginError)
  })

  it('throws a typed error when no origin can be determined at all', () => {
    expect(() => getAnnotationAppOrigin({ location: { href: '' } }))
      .toThrow(PortalContextOriginError)
  })
})

describe('portal context', () => {
  beforeEach(() => {
    clearAnnotationPlatformRuntime()
    clearPortalContext()
  })

  afterEach(() => {
    clearAnnotationPlatformRuntime()
    clearPortalContext()
  })

  it('starts anonymous', () => {
    expect(getPortalContext()).toEqual({ token: '', userInfo: null, namespaceId: null })
  })

  it('prefers the live host context over a previously injected snapshot', () => {
    setPortalContext({ token: 'snapshot-token', userInfo: null, namespaceId: null })
    installHost(HOST_ORIGIN, 'live-token')

    expect(getPortalContext().token).toBe('live-token')
  })

  it('falls back to the snapshot when the host context getter throws', () => {
    setPortalContext({ token: 'snapshot-token', userInfo: null, namespaceId: null })
    setAnnotationPlatformRuntime({
      getOrigin: () => HOST_ORIGIN,
      getContext: () => { throw new Error('host failure') }
    })

    expect(getPortalContext().token).toBe('snapshot-token')
  })

  it('normalizes and freezes an injected snapshot', () => {
    const context = setPortalContext({ token: 7, userInfo: undefined, namespaceId: '' } as never)

    expect(context).toEqual({ token: '', userInfo: null, namespaceId: null })
    expect(Object.isFrozen(context)).toBe(true)
  })

  it('preserves a valid user info object and namespace id', () => {
    const userInfo = Object.freeze({ UserId: 'u1' })
    setPortalContext({ token: 'Bearer opaque', userInfo, namespaceId: 9 })

    expect(getPortalContext()).toEqual({ token: 'Bearer opaque', userInfo, namespaceId: 9 })
  })

  it('clears back to the empty context', () => {
    setPortalContext({ token: 'to-be-cleared', userInfo: null, namespaceId: 3 })
    clearPortalContext()

    expect(getPortalContext()).toEqual({ token: '', userInfo: null, namespaceId: null })
  })

  it('reads the host token per call so rotation takes effect without re-injection', () => {
    let token = 'first'
    setAnnotationPlatformRuntime({
      getOrigin: () => HOST_ORIGIN,
      getContext: () => ({ token, userInfo: null, namespaceId: null })
    })

    expect(getPortalContext().token).toBe('first')
    token = 'rotated'
    expect(getPortalContext().token).toBe('rotated')
  })

  it('no longer exposes the removed postMessage handshake API', async () => {
    const module = await import('./portalContext')

    expect('initializePortalContext' in module).toBe(false)
    expect('initializePortalContextStrict' in module).toBe(false)
    expect('PortalContextTimeoutError' in module).toBe(false)
    expect('PortalContextClearedError' in module).toBe(false)
  })

  it('does not touch window.postMessage during context resolution', () => {
    const postMessage = vi.fn()
    installHost(HOST_ORIGIN, 'tok')

    getAnnotationAppOrigin({ location: { href: `${PORTAL_ORIGIN}/portal/` } })
    getPortalContext()

    expect(postMessage).not.toHaveBeenCalled()
  })
})
