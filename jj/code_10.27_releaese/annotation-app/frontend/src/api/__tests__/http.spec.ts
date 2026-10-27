import type { AxiosRequestConfig, AxiosResponse } from 'axios'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:8000/api/v1')

const platform = vi.hoisted(() => ({
  context: { token: '', userInfo: null, namespaceId: null },
  getAnnotationAppOrigin: vi.fn(() => 'https://child.example'),
  getPortalContext: vi.fn(() => platform.context)
}))

vi.mock('@annotation/platform/portalContext', () => ({
  getAnnotationAppOrigin: platform.getAnnotationAppOrigin,
  getPortalContext: platform.getPortalContext
}))

function adapter(config: AxiosRequestConfig): Promise<AxiosResponse> {
  return Promise.resolve({
    data: {},
    status: 200,
    statusText: 'OK',
    headers: {},
    config: config as AxiosResponse['config']
  })
}

beforeEach(() => {
  vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:8000/api/v1')
  vi.resetModules()
  platform.context = { token: '', userInfo: null, namespaceId: null }
  platform.getPortalContext.mockClear()
  platform.getAnnotationAppOrigin.mockClear()
  platform.getAnnotationAppOrigin.mockReturnValue('https://child.example')
})

describe('resolveApiUrl', () => {
  it('resolves relative backend download urls against the annotation child origin', async () => {
    const { resolveApiUrl } = await import('@annotation/api/http')

    expect(resolveApiUrl('/api/v1/annotations/export/download?file=a.json')).toBe(
      'https://child.example/api/v1/annotations/export/download?file=a.json'
    )
  })

  it('preserves an absolute backend download url', async () => {
    const { resolveApiUrl } = await import('@annotation/api/http')

    expect(resolveApiUrl('https://downloads.example/export/a.json')).toBe(
      'https://downloads.example/export/a.json'
    )
  })
})

// baseURL 惰性化的行为契约:模块加载期**不得**求值 origin,否则 wujie 注入时机
// 一旦晚于 bundle 求值,baseURL 就会固化成门户地址(此前崩溃的根因之一)。
describe('lazy base URL resolution', () => {
  it('does not resolve the origin at module import time', async () => {
    await import('@annotation/api/http')

    expect(platform.getAnnotationAppOrigin).not.toHaveBeenCalled()
  })

  it('resolves the origin on the first request, after the host runtime is injected', async () => {
    const { default: http } = await import('@annotation/api/http')
    expect(platform.getAnnotationAppOrigin).not.toHaveBeenCalled()

    // 模拟宿主在 bundle 求值之后才注入 origin
    platform.getAnnotationAppOrigin.mockReturnValue('https://172.30.6.63:30080')
    const response = await http.get('/packages', { adapter })

    expect(response.config.baseURL).toBe('https://172.30.6.63:30080/api/v1')
  })

  it('memoizes the resolved base URL across requests', async () => {
    const { default: http } = await import('@annotation/api/http')

    await http.get('/packages', { adapter })
    await http.get('/packages', { adapter })

    expect(platform.getAnnotationAppOrigin).toHaveBeenCalledTimes(1)
  })

  it('recomputes after an explicit cache reset', async () => {
    const { default: http, resetApiBaseURLCache } = await import('@annotation/api/http')

    await http.get('/packages', { adapter })
    resetApiBaseURLCache()
    platform.getAnnotationAppOrigin.mockReturnValue('https://moved.example')
    const response = await http.get('/packages', { adapter })

    expect(response.config.baseURL).toBe('https://moved.example/api/v1')
  })
})

describe('annotation API client', () => {
  it('keeps an absolute configured API path on the annotation child origin', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:8000/api/v1')
    vi.resetModules()
    const { resolveApiBaseURL } = await import('@annotation/api/http')

    expect(resolveApiBaseURL()).toBe('https://child.example/api/v1')
  })

  it('resolves a relative API base against the annotation child origin', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '/annotate-api/v1')
    vi.resetModules()
    const { resolveApiBaseURL } = await import('@annotation/api/http')

    expect(resolveApiBaseURL()).toBe('https://child.example/annotate-api/v1')
  })

  it('confines a network-path-like configured pathname to the annotation child origin', async () => {
    vi.stubEnv(
      'VITE_API_BASE_URL',
      'https://config.example//evil.example/api/v1?ignored=1#ignored'
    )
    vi.resetModules()
    const { resolveApiBaseURL } = await import('@annotation/api/http')
    const finalUrl = new URL(resolveApiBaseURL())

    expect(finalUrl.origin).toBe('https://child.example')
    expect(finalUrl.pathname).toBe('//evil.example/api/v1')
    expect(finalUrl.search).toBe('')
    expect(finalUrl.hash).toBe('')
  })

  it.each([
    'javascript:alert(1)',
    'https://user:password@config.example/api/v1'
  ])('rejects an unsafe configured API URL: %s', async (configuredUrl) => {
    vi.stubEnv('VITE_API_BASE_URL', configuredUrl)
    vi.resetModules()
    const { resolveApiBaseURL } = await import('@annotation/api/http')

    // 校验规则未变,只是从模块加载期挪到首次求值时
    expect(() => resolveApiBaseURL()).toThrow(
      'annotation API base URL must use HTTP(S) without credentials'
    )
  })

  it('rejects an unsafe configured API URL on the request path too', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'javascript:alert(1)')
    vi.resetModules()
    const { default: http } = await import('@annotation/api/http')

    await expect(http.get('/packages', { adapter })).rejects.toThrow(
      'annotation API base URL must use HTTP(S) without credentials'
    )
  })

  it('reads the current in-memory token for every request and passes it through unchanged', async () => {
    const { default: http } = await import('@annotation/api/http')

    platform.context = { token: 'Bearer exact-token', userInfo: null, namespaceId: null }
    const first = await http.get('/packages', { adapter })
    platform.context = { token: 'rotated-token', userInfo: null, namespaceId: null }
    const second = await http.get('/packages', { adapter })

    expect(first.config.headers.get('X-Token')).toBe('Bearer exact-token')
    expect(second.config.headers.get('X-Token')).toBe('rotated-token')
    expect(platform.getPortalContext).toHaveBeenCalledTimes(2)
  })

  it('does not add X-Token when the current token is empty', async () => {
    const { default: http } = await import('@annotation/api/http')

    const response = await http.get('/packages', { adapter })

    expect(response.config.headers.has('X-Token')).toBe(false)
  })
})
