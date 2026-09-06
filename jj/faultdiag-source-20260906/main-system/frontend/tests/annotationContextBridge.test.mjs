import test from 'node:test'
import assert from 'node:assert/strict'

import { installAnnotationContextBridge } from '../src/utils/annotationContextBridge.mjs'

const APP_ORIGIN = 'https://child.example:30443'
const REQUEST_ID = 'request-id-000001'

function createHarness(contextValue = Object.freeze({
  token: 'Bearer opaque',
  userInfo: Object.freeze({ UserId: 'u1' }),
  namespaceId: 7,
})) {
  const listeners = new Set()
  const responses = []
  let contextCalls = 0
  const frameWindow = {
    postMessage(message, targetOrigin) {
      responses.push({ message, targetOrigin })
    },
  }
  const iframe = { contentWindow: frameWindow, isConnected: true }
  const windowRef = {
    addEventListener(type, listener) {
      assert.equal(type, 'message')
      listeners.add(listener)
    },
    removeEventListener(type, listener) {
      assert.equal(type, 'message')
      listeners.delete(listener)
    },
  }
  const dispose = installAnnotationContextBridge({
    windowRef,
    iframe,
    context() {
      contextCalls += 1
      return contextValue
    },
    appOrigin: APP_ORIGIN,
  })

  return {
    dispose,
    frameWindow,
    iframe,
    listeners,
    responses,
    get contextCalls() { return contextCalls },
    dispatch(overrides = {}) {
      const event = {
        source: frameWindow,
        origin: APP_ORIGIN,
        data: { type: 'faultdiag:context:request', version: 2, requestId: REQUEST_ID },
        ...overrides,
      }
      for (const listener of [...listeners]) listener(event)
    },
  }
}

test('valid live iframe request receives only the versioned allowlisted context at the exact origin', () => {
  const context = Object.freeze({
    token: 'Bearer opaque',
    userInfo: Object.freeze({ UserId: 'u1' }),
    namespaceId: 9,
    privateValue: 'must-not-cross-the-bridge',
  })
  const harness = createHarness(context)

  harness.dispatch()

  assert.equal(harness.contextCalls, 1)
  assert.deepEqual(harness.responses, [{
    message: {
      type: 'faultdiag:context:response',
      version: 2,
      requestId: REQUEST_ID,
      token: 'Bearer opaque',
      userInfo: { UserId: 'u1' },
      namespaceId: 9,
    },
    targetOrigin: APP_ORIGIN,
  }])
  assert.equal(Object.hasOwn(harness.responses[0].message, 'privateValue'), false)
  assert.notEqual(harness.responses[0].targetOrigin, '*')
})

test('wrong source, origin, type, or version receives no response and cannot read context', () => {
  const invalidEvents = [
    { source: { postMessage() {} } },
    { origin: 'https://portal.example' },
    { data: { type: 'faultdiag:context:unknown', version: 2, requestId: REQUEST_ID } },
    { data: { type: 'faultdiag:context:request', version: 1, requestId: REQUEST_ID } },
    { data: { type: 'faultdiag:context:request', version: 2 } },
    { data: { type: 'faultdiag:context:request', version: 2, requestId: 'short' } },
    { data: { type: 'faultdiag:context:request', version: 2, requestId: 'x'.repeat(129) } },
    { data: { type: 'faultdiag:context:request', version: 2, requestId: 'request id invalid' } },
    { data: null },
  ]

  for (const invalid of invalidEvents) {
    const harness = createHarness()
    harness.dispatch(invalid)
    assert.equal(harness.responses.length, 0)
    assert.equal(harness.contextCalls, 0)
  }
})

test('detached, missing, or replaced iframe windows receive no response', () => {
  const detached = createHarness()
  detached.iframe.isConnected = false
  detached.dispatch()

  const missing = createHarness()
  missing.iframe.contentWindow = null
  missing.dispatch()

  const replaced = createHarness()
  const formerWindow = replaced.frameWindow
  replaced.iframe.contentWindow = { postMessage() { throw new Error('must not send') } }
  replaced.dispatch({ source: formerWindow })

  for (const harness of [detached, missing, replaced]) {
    assert.equal(harness.responses.length, 0)
    assert.equal(harness.contextCalls, 0)
  }
})

test('bridge ignores mutable or throwing context providers without responding', () => {
  const mutable = createHarness({ token: 'mutable', userInfo: null, namespaceId: null })
  mutable.dispatch()
  assert.equal(mutable.responses.length, 0)

  const listeners = new Set()
  const frameWindow = { postMessage() { throw new Error('must not send') } }
  installAnnotationContextBridge({
    windowRef: {
      addEventListener(_type, listener) { listeners.add(listener) },
      removeEventListener(_type, listener) { listeners.delete(listener) },
    },
    iframe: { contentWindow: frameWindow, isConnected: true },
    context() { throw new Error('private context failure') },
    appOrigin: APP_ORIGIN,
  })
  assert.doesNotThrow(() => {
    for (const listener of listeners) listener({
      source: frameWindow,
      origin: APP_ORIGIN,
      data: { type: 'faultdiag:context:request', version: 2, requestId: REQUEST_ID },
    })
  })
})

test('bridge validates request envelope without executing a requestId accessor', () => {
  const harness = createHarness()
  let getterCalls = 0
  const data = { type: 'faultdiag:context:request', version: 2 }
  Object.defineProperty(data, 'requestId', {
    enumerable: true,
    get() {
      getterCalls += 1
      return REQUEST_ID
    },
  })

  harness.dispatch({ data })

  assert.equal(getterCalls, 0)
  assert.equal(harness.responses.length, 0)
  assert.equal(harness.contextCalls, 0)
})

test('bridge rejects accessors, special objects, proxies, and shallow-frozen nested context', () => {
  let getterCalls = 0
  const accessorUserInfo = {}
  Object.defineProperty(accessorUserInfo, 'secret', {
    enumerable: true,
    get() { getterCalls += 1; return 'must-not-read' },
  })
  Object.freeze(accessorUserInfo)

  const throwingProxyTarget = Object.freeze({ UserId: 'u1' })
  const throwingProxy = new Proxy(throwingProxyTarget, {
    getOwnPropertyDescriptor() { throw new Error('hostile descriptor') },
  })
  const cases = [
    Object.freeze({ token: '', userInfo: accessorUserInfo, namespaceId: null }),
    Object.freeze({ token: '', userInfo: Object.freeze(new Date(0)), namespaceId: null }),
    Object.freeze({
      token: '',
      userInfo: Object.freeze({ profile: { displayName: 'mutable' } }),
      namespaceId: null,
    }),
    Object.freeze({
      token: '',
      userInfo: Object.freeze({ tags: ['mutable-array'] }),
      namespaceId: null,
    }),
    Object.freeze({ token: '', userInfo: throwingProxy, namespaceId: null }),
  ]

  for (const context of cases) {
    const harness = createHarness(context)
    assert.doesNotThrow(() => harness.dispatch())
    assert.equal(harness.responses.length, 0)
  }
  assert.equal(getterCalls, 0)
})

test('bridge defensively clones an accepted deeply frozen context', () => {
  const userInfo = Object.freeze({
    profile: Object.freeze({
      displayName: 'Ada',
      tags: Object.freeze(['operator', Object.freeze({ level: 2 })]),
    }),
  })
  const harness = createHarness(Object.freeze({ token: '', userInfo, namespaceId: 'ns-1' }))

  harness.dispatch()

  assert.equal(harness.responses.length, 1)
  const responseUserInfo = harness.responses[0].message.userInfo
  assert.deepEqual(responseUserInfo, userInfo)
  assert.notEqual(responseUserInfo, userInfo)
  assert.notEqual(responseUserInfo.profile, userInfo.profile)
  assert.equal(Object.isFrozen(responseUserInfo.profile.tags[1]), true)
})

test('disposer removes the message listener exactly once', () => {
  const harness = createHarness()
  assert.equal(harness.listeners.size, 1)

  harness.dispose()
  harness.dispose()
  harness.dispatch()

  assert.equal(harness.listeners.size, 0)
  assert.equal(harness.responses.length, 0)
  assert.equal(harness.contextCalls, 0)
})
