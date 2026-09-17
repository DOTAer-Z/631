import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import {
  createAppRuntime,
  createCleanupRegistry,
} from '../src/utils/wujieLifecycle.mjs'

const requestPath = new URL('../src/utils/request.js', import.meta.url)
let importNumber = 0

async function importRequestWithStubs() {
  const source = await readFile(requestPath, 'utf8')
  const captured = {
    createConfig: null,
    messages: [],
    requestHandlers: null,
    responseHandlers: null,
  }
  let token = ''
  const instance = {
    interceptors: {
      request: { use: (...handlers) => { captured.requestHandlers = handlers } },
      response: { use: (...handlers) => { captured.responseHandlers = handlers } },
    },
  }
  class CanceledError extends Error {
    constructor(message, config) {
      super(message)
      this.name = 'CanceledError'
      this.code = 'ERR_CANCELED'
      this.config = config
      this.__CANCEL__ = true
    }
  }
  globalThis.__wujieRequestLifecycleStubs = {
    axios: {
      CanceledError,
      create(config) {
        captured.createConfig = config
        return instance
      },
      isCancel(error) { return error?.__CANCEL__ === true },
    },
    ElMessage: { error(message) { captured.messages.push(message) } },
    getAppUrl(path) { return `https://child.example:30080${path}` },
    getToken() { return token },
  }
  importNumber += 1
  const moduleSource = source
    .replace("import axios from 'axios'", 'const axios = globalThis.__wujieRequestLifecycleStubs.axios')
    .replace("import { ElMessage } from 'element-plus'", 'const { ElMessage } = globalThis.__wujieRequestLifecycleStubs')
    .replace(
      /import \{\s*getAppUrl,\s*getToken\s*\} from ['"]\.\/platform['"]/,
      'const { getAppUrl, getToken } = globalThis.__wujieRequestLifecycleStubs',
    )
    .concat(`\n// request lifecycle test import ${importNumber}`)
  const imported = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(moduleSource)}`)
  return {
    CanceledError,
    captured,
    imported,
    setToken(value) { token = value },
  }
}

function trackAbortListeners(signal) {
  const addEventListener = signal.addEventListener.bind(signal)
  const removeEventListener = signal.removeEventListener.bind(signal)
  let listeners = 0
  signal.addEventListener = (...args) => {
    if (args[0] === 'abort') listeners += 1
    return addEventListener(...args)
  }
  signal.removeEventListener = (...args) => {
    if (args[0] === 'abort') listeners -= 1
    return removeEventListener(...args)
  }
  return () => listeners
}

test('runtime aborts pending requests before Vue unmount and remounts with a fresh scope', async () => {
  const requestRuntime = await importRequestWithStubs()
  const events = []
  const scopeSignals = []
  const runtime = createAppRuntime((cleanup) => {
    const scope = requestRuntime.imported.installRequestScope(cleanup)
    scopeSignals.push(scope.signal)
    cleanup.register(() => { throw new Error('isolated cleanup failure') })
    return {
      mount() { events.push('vue-mount') },
      unmount() { events.push('vue-unmount') },
    }
  })

  runtime.mount()
  const oldConfig = requestRuntime.captured.requestHandlers[0]({ headers: {} })
  oldConfig.signal.addEventListener('abort', () => events.push('request-abort'))
  runtime.unmount()
  runtime.mount()
  const freshConfig = requestRuntime.captured.requestHandlers[0]({ headers: {} })

  assert.deepEqual(events, ['vue-mount', 'request-abort', 'vue-unmount', 'vue-mount'])
  assert.equal(oldConfig.signal.aborted, true)
  assert.notEqual(scopeSignals[0], scopeSignals[1])
  assert.equal(scopeSignals[1].aborted, false)
  assert.equal(freshConfig.signal, scopeSignals[1])

  const oldCancellation = new requestRuntime.CanceledError('canceled', oldConfig)
  await assert.rejects(
    requestRuntime.captured.responseHandlers[1](oldCancellation),
    error => error === oldCancellation,
  )
  assert.deepEqual(requestRuntime.captured.messages, [])
  assert.equal(scopeSignals[1].aborted, false)
})

test('caller and lifecycle signals independently cancel a request', async () => {
  for (const abortSource of ['caller', 'lifecycle']) {
    const requestRuntime = await importRequestWithStubs()
    const cleanup = createCleanupRegistry()
    const scope = requestRuntime.imported.installRequestScope(cleanup)
    const caller = new AbortController()
    const config = requestRuntime.captured.requestHandlers[0]({
      headers: {},
      signal: caller.signal,
    })

    assert.notEqual(config.signal, caller.signal)
    assert.notEqual(config.signal, scope.signal)
    if (abortSource === 'caller') caller.abort()
    else cleanup.retire()

    assert.equal(config.signal.aborted, true, `${abortSource} abort must reach Axios`)
    const cancellation = new requestRuntime.CanceledError('canceled', config)
    await assert.rejects(requestRuntime.captured.responseHandlers[1](cancellation))
    assert.deepEqual(requestRuntime.captured.messages, [])
  }
})

test('fallback signal listeners are released on success, failure, and abort', async () => {
  const originalAny = AbortSignal.any
  AbortSignal.any = undefined
  try {
    for (const outcome of ['success', 'failure', 'abort']) {
      const requestRuntime = await importRequestWithStubs()
      const cleanup = createCleanupRegistry()
      const scope = requestRuntime.imported.installRequestScope(cleanup)
      const caller = new AbortController()
      const lifecycleListeners = trackAbortListeners(scope.signal)
      const callerListeners = trackAbortListeners(caller.signal)
      const config = requestRuntime.captured.requestHandlers[0]({ headers: {}, signal: caller.signal })

      assert.equal(lifecycleListeners(), 1)
      assert.equal(callerListeners(), 1)
      if (outcome === 'success') {
        assert.deepEqual(requestRuntime.captured.responseHandlers[0]({ config, data: { ok: true } }), { ok: true })
      } else if (outcome === 'failure') {
        await assert.rejects(requestRuntime.captured.responseHandlers[1]({ config, message: 'network failed' }))
      } else {
        caller.abort()
      }
      assert.equal(lifecycleListeners(), 0)
      assert.equal(callerListeners(), 0)
      cleanup.retire()
    }
  } finally {
    AbortSignal.any = originalAny
  }
})

test('a retired or missing request scope rejects new work as Axios cancellation', async () => {
  const requestRuntime = await importRequestWithStubs()
  const runRequest = () => Promise.resolve().then(
    () => requestRuntime.captured.requestHandlers[0]({ headers: {} }),
  )

  await assert.rejects(runRequest(), error => error.code === 'ERR_CANCELED' && error.__CANCEL__ === true)
  const cleanup = createCleanupRegistry()
  requestRuntime.imported.installRequestScope(cleanup)
  cleanup.retire()
  await assert.rejects(runRequest(), error => error.code === 'ERR_CANCELED' && error.__CANCEL__ === true)
})

test('empty Token removes stale reusable headers while exact nonempty Token is preserved', async () => {
  const requestRuntime = await importRequestWithStubs()
  requestRuntime.imported.installRequestScope(createCleanupRegistry())
  const headers = { 'X-Token': 'stale', keep: 'value' }

  requestRuntime.setToken(' exact portal token ')
  requestRuntime.captured.requestHandlers[0]({ headers })
  assert.equal(headers['X-Token'], ' exact portal token ')

  requestRuntime.setToken('')
  requestRuntime.captured.requestHandlers[0]({ headers })
  assert.deepEqual(headers, { keep: 'value' })
})

test('Axios, ERR_CANCELED, and AbortError cancellations are silent but business failures toast', async () => {
  const requestRuntime = await importRequestWithStubs()
  const quietErrors = [
    new requestRuntime.CanceledError('canceled', {}),
    { code: 'ERR_CANCELED', message: 'canceled' },
    { name: 'AbortError', message: 'aborted' },
  ]
  for (const error of quietErrors) {
    await assert.rejects(requestRuntime.captured.responseHandlers[1](error), rejected => rejected === error)
  }
  assert.deepEqual(requestRuntime.captured.messages, [])

  const businessError = { response: { data: { detail: 'business failed' } }, message: 'fallback' }
  await assert.rejects(
    requestRuntime.captured.responseHandlers[1](businessError),
    rejected => rejected === businessError,
  )
  assert.deepEqual(requestRuntime.captured.messages, ['business failed'])
})

test('main installs one request scope for every application cleanup cycle', async () => {
  const source = await readFile(new URL('../src/main.js', import.meta.url), 'utf8')

  assert.match(source, /import \{\s*installRequestScope\s*\} from ['"]\.\/utils\/request['"]/)
  assert.match(source, /function createMyApp\(cleanup\)[\s\S]*installRequestScope\(cleanup\)/)
})
