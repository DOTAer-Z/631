import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearAnnotationPlatformRuntime,
  getAnnotationPlatformRuntime,
  readInjectedContext,
  readInjectedOrigin,
  readStandaloneOrigin,
  safeHttpOrigin,
  setAnnotationPlatformRuntime
} from './runtime'

describe('annotation platform runtime injection', () => {
  beforeEach(() => {
    clearAnnotationPlatformRuntime()
  })

  afterEach(() => {
    clearAnnotationPlatformRuntime()
  })

  it('rejects a runtime missing either getter', () => {
    expect(() => setAnnotationPlatformRuntime({} as never)).toThrow(TypeError)
    expect(() =>
      setAnnotationPlatformRuntime({ getOrigin: () => 'https://a.example' } as never)
    ).toThrow(TypeError)
    expect(() =>
      setAnnotationPlatformRuntime({ getContext: () => null } as never)
    ).toThrow(TypeError)
    expect(getAnnotationPlatformRuntime()).toBeNull()
  })

  it('reads the host origin lazily on every call rather than snapshotting it', () => {
    let current = 'https://first.example'
    const getOrigin = vi.fn(() => current)
    setAnnotationPlatformRuntime({ getOrigin, getContext: () => ({
      token: '', userInfo: null, namespaceId: null
    }) })

    expect(readInjectedOrigin()).toBe('https://first.example')
    current = 'https://second.example:30080'
    expect(readInjectedOrigin()).toBe('https://second.example:30080')
    expect(getOrigin).toHaveBeenCalledTimes(2)
  })

  it('returns null when the host origin getter throws or yields an unsafe value', () => {
    setAnnotationPlatformRuntime({
      getOrigin: () => { throw new Error('host failure') },
      getContext: () => ({ token: '', userInfo: null, namespaceId: null })
    })
    expect(readInjectedOrigin()).toBeNull()

    for (const unsafe of ['javascript:alert(1)', 'https://user:pw@a.example', 'not a url', '']) {
      setAnnotationPlatformRuntime({
        getOrigin: () => unsafe,
        getContext: () => ({ token: '', userInfo: null, namespaceId: null })
      })
      expect(readInjectedOrigin()).toBeNull()
    }
  })

  it('reads the host context lazily so a rotated token takes effect immediately', () => {
    let token = 'first-token'
    setAnnotationPlatformRuntime({
      getOrigin: () => 'https://a.example',
      getContext: () => ({ token, userInfo: null, namespaceId: null })
    })

    expect(readInjectedContext()?.token).toBe('first-token')
    token = 'rotated-token'
    expect(readInjectedContext()?.token).toBe('rotated-token')
  })

  it('normalizes and freezes an injected context, and rejects invalid namespace ids', () => {
    setAnnotationPlatformRuntime({
      getOrigin: () => 'https://a.example',
      getContext: () => ({ token: 42, userInfo: undefined, namespaceId: '' } as never)
    })

    const context = readInjectedContext()
    expect(context).toEqual({ token: '', userInfo: null, namespaceId: null })
    expect(Object.isFrozen(context)).toBe(true)

    for (const [namespaceId, expected] of [
      ['ns-1', 'ns-1'],
      [7, 7],
      [Number.NaN, null],
      [{}, null]
    ] as Array<[unknown, unknown]>) {
      setAnnotationPlatformRuntime({
        getOrigin: () => 'https://a.example',
        getContext: () => ({ token: '', userInfo: null, namespaceId } as never)
      })
      expect(readInjectedContext()?.namespaceId).toBe(expected)
    }
  })

  it('returns null from the injected readers when no runtime is installed', () => {
    expect(readInjectedOrigin()).toBeNull()
    expect(readInjectedContext()).toBeNull()
  })

  it('falls back to location.href then __APP_CONFIG__ when standalone', () => {
    expect(readStandaloneOrigin({ location: { href: 'https://solo.example/annotate/' } })).toBe(
      'https://solo.example'
    )
    expect(readStandaloneOrigin({
      location: { href: 'javascript:alert(1)' },
      __APP_CONFIG__: { appOrigin: 'https://config.example:8081' }
    })).toBe('https://config.example:8081')
    expect(readStandaloneOrigin({ location: { href: 'not a url' } })).toBeNull()
  })

  it('skips a standalone reader that throws', () => {
    const hostile = {
      get location(): { href: string } { throw new Error('blocked') },
      __APP_CONFIG__: { appOrigin: 'https://fallback.example' }
    }
    expect(readStandaloneOrigin(hostile as never)).toBe('https://fallback.example')
  })

  it('validates http(s) origins and rejects credentials', () => {
    expect(safeHttpOrigin('http://a.example:80/x')).toBe('http://a.example')
    expect(safeHttpOrigin('https://a.example:30080/x?y#z')).toBe('https://a.example:30080')
    expect(safeHttpOrigin('ftp://a.example')).toBeNull()
    expect(safeHttpOrigin('https://user:pw@a.example')).toBeNull()
    expect(safeHttpOrigin(null)).toBeNull()
  })
})
