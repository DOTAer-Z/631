import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import {
  createAppRuntime,
  createCleanupRegistry,
  installWujieLifecycle,
} from '../src/utils/wujieLifecycle.mjs'

const lifecyclePath = new URL('../src/utils/wujieLifecycle.mjs', import.meta.url)
const mainPath = new URL('../src/main.js', import.meta.url)

test('cleanup registry runs active disposers once in registration order with predictable removal', () => {
  const cleanup = createCleanupRegistry()
  const events = []
  const removeFirst = cleanup.register(() => events.push('first'))
  const removeSecond = cleanup.register(() => events.push('second'))
  cleanup.register(() => events.push('third'))

  assert.equal(removeSecond(), true)
  assert.equal(removeSecond(), false)
  assert.equal(cleanup.runAll(), 2)
  assert.deepEqual(events, ['first', 'third'])
  assert.equal(cleanup.runAll(), 0)
  assert.equal(removeFirst(), false)
})

test('cleanup registry isolates failures and defers registrations made during a run', () => {
  const cleanup = createCleanupRegistry()
  const events = []
  cleanup.register(() => {
    events.push('register-late')
    cleanup.register(() => events.push('late'))
  })
  cleanup.register(() => {
    events.push('throw')
    throw new Error('Bearer private-token context-secret')
  })
  cleanup.register(() => events.push('after-throw'))

  assert.doesNotThrow(() => cleanup.runAll())
  assert.deepEqual(events, ['register-late', 'throw', 'after-throw'])
  assert.equal(cleanup.runAll(), 1)
  assert.deepEqual(events, ['register-late', 'throw', 'after-throw', 'late'])
})

test('retired cleanup runs pending work once and immediately disposes every late registration', () => {
  const cleanup = createCleanupRegistry()
  const events = []
  cleanup.register(() => {
    events.push('first')
    cleanup.register(() => events.push('registered-during-retire'))
  })
  cleanup.register(() => events.push('second'))

  assert.equal(cleanup.retire(), 2)
  assert.deepEqual(events, ['first', 'registered-during-retire', 'second'])
  assert.equal(cleanup.retire(), 0)
  assert.equal(cleanup.runAll(), 0)
  const removeLate = cleanup.register(() => events.push('registered-after-retire'))
  assert.equal(removeLate(), false)
  assert.doesNotThrow(() => cleanup.register(() => { throw new Error('private late failure') }))
  assert.deepEqual(events, ['first', 'registered-during-retire', 'second', 'registered-after-retire'])
})

test('app runtime is idempotent and cleans before unmount across repeated fresh cycles', () => {
  const events = []
  let appNumber = 0
  const runtime = createAppRuntime((cleanup) => {
    appNumber += 1
    const number = appNumber
    cleanup.register(() => events.push(`cleanup-${number}-first`))
    cleanup.register(() => {
      events.push(`cleanup-${number}-throw`)
      throw new Error('private cleanup failure')
    })
    cleanup.register(() => events.push(`cleanup-${number}-last`))
    return {
      mount(target) { events.push(`mount-${number}:${target}`) },
      unmount() { events.push(`unmount-${number}`) },
    }
  })

  runtime.mount()
  runtime.mount()
  assert.equal(appNumber, 1)
  runtime.unmount()
  runtime.unmount()
  runtime.mount()
  runtime.unmount()

  assert.equal(appNumber, 2)
  assert.deepEqual(events, [
    'mount-1:#app',
    'cleanup-1-first',
    'cleanup-1-throw',
    'cleanup-1-last',
    'unmount-1',
    'mount-2:#app',
    'cleanup-2-first',
    'cleanup-2-throw',
    'cleanup-2-last',
    'unmount-2',
  ])
})

test('app runtime resets after an app unmount error and can mount a fresh root', () => {
  let creates = 0
  const runtime = createAppRuntime(() => {
    creates += 1
    return {
      mount() {},
      unmount() { throw new Error('private app error') },
    }
  })

  runtime.mount()
  assert.doesNotThrow(() => runtime.unmount())
  runtime.mount()
  assert.equal(creates, 2)
})

test('runtime retires and discards each cleanup cycle before a fresh mount', () => {
  const events = []
  const cleanups = []
  let creates = 0
  const runtime = createAppRuntime((cleanup) => {
    creates += 1
    const number = creates
    cleanups.push(cleanup)
    cleanup.register(() => events.push(`cleanup-${number}`))
    return {
      mount() { events.push(`mount-${number}`) },
      unmount() { events.push(`unmount-${number}`) },
    }
  })

  runtime.mount()
  runtime.unmount()
  cleanups[0].register(() => events.push('late-first-cycle'))
  runtime.mount()
  runtime.unmount()

  assert.notEqual(cleanups[0], cleanups[1])
  assert.deepEqual(events, [
    'mount-1',
    'cleanup-1',
    'unmount-1',
    'late-first-cycle',
    'mount-2',
    'cleanup-2',
    'unmount-2',
  ])
})

test('runtime rolls back cleanup after createApp throws and retries with a fresh cycle', () => {
  const events = []
  const cleanups = []
  const createFailure = new Error('private create failure')
  let attempts = 0
  const runtime = createAppRuntime((cleanup) => {
    attempts += 1
    cleanups.push(cleanup)
    cleanup.register(() => events.push(`cleanup-${attempts}`))
    if (attempts === 1) throw createFailure
    return { mount() { events.push('mount-2') }, unmount() { events.push('unmount-2') } }
  })

  assert.throws(() => runtime.mount(), error => error === createFailure)
  cleanups[0].register(() => events.push('late-failed-create'))
  runtime.mount()

  assert.equal(attempts, 2)
  assert.notEqual(cleanups[0], cleanups[1])
  assert.deepEqual(events, ['cleanup-1', 'late-failed-create', 'mount-2'])
})

test('runtime rolls back cleanup before partial unmount after invalid app shape', () => {
  const events = []
  let attempts = 0
  const runtime = createAppRuntime((cleanup) => {
    attempts += 1
    cleanup.register(() => events.push(`cleanup-${attempts}`))
    if (attempts === 1) {
      return { unmount() { events.push('rollback-partial-unmount') } }
    }
    return { mount() { events.push('mount-2') }, unmount() {} }
  })

  assert.throws(() => runtime.mount(), /application must provide mount and unmount/)
  runtime.mount()

  assert.deepEqual(events, ['cleanup-1', 'rollback-partial-unmount', 'mount-2'])
})

test('runtime rolls back a failed mount, preserves its error, and retries fresh', () => {
  const events = []
  const cleanups = []
  const mountFailure = new Error('private mount failure')
  let attempts = 0
  const runtime = createAppRuntime((cleanup) => {
    attempts += 1
    cleanups.push(cleanup)
    cleanup.register(() => events.push(`cleanup-${attempts}`))
    if (attempts === 1) {
      return {
        mount() { events.push('mount-1'); throw mountFailure },
        unmount() { events.push('rollback-unmount-1'); throw new Error('secondary rollback failure') },
      }
    }
    return { mount() { events.push('mount-2') }, unmount() {} }
  })

  assert.throws(() => runtime.mount(), error => error === mountFailure)
  runtime.mount()

  assert.notEqual(cleanups[0], cleanups[1])
  assert.deepEqual(events, ['mount-1', 'cleanup-1', 'rollback-unmount-1', 'mount-2'])
})

test('runtime suppresses reentrant mount while creating and mounting one root', () => {
  let runtime
  let creates = 0
  let nestedDuringCreate = 'unset'
  let nestedDuringMount = 'unset'
  runtime = createAppRuntime(() => {
    creates += 1
    nestedDuringCreate = runtime.mount()
    return {
      mount() { nestedDuringMount = runtime.mount() },
      unmount() {},
    }
  })

  const app = runtime.mount()

  assert.equal(creates, 1)
  assert.equal(nestedDuringCreate, null)
  assert.equal(nestedDuringMount, null)
  assert.equal(runtime.mount(), app)
})

test('independent installation mounts immediately once and preserves host globals', () => {
  const hostMount = () => 'host mount'
  const hostUnmount = () => 'host unmount'
  const windowRef = {
    location: { href: 'https://child.example/' },
    __WUJIE_MOUNT: hostMount,
    __WUJIE_UNMOUNT: hostUnmount,
  }
  let mounts = 0
  const runtime = createAppRuntime(() => ({
    mount() { mounts += 1 },
    unmount() {},
  }))

  installWujieLifecycle(windowRef, runtime)
  installWujieLifecycle(windowRef, runtime)

  assert.equal(mounts, 1)
  assert.equal(windowRef.__WUJIE_MOUNT, hostMount)
  assert.equal(windowRef.__WUJIE_UNMOUNT, hostUnmount)
})

test('Wujie installation exposes standard globals backed by one runtime root', () => {
  const events = []
  let creates = 0
  const runtime = createAppRuntime(() => {
    creates += 1
    const number = creates
    return {
      mount(target) { events.push(`mount-${number}:${target}`) },
      unmount() { events.push(`unmount-${number}`) },
    }
  })
  const windowRef = {
    __POWERED_BY_WUJIE__: true,
    hostOwnedValue: 'keep',
  }

  installWujieLifecycle(windowRef, runtime)
  assert.deepEqual(events, [])
  windowRef.__WUJIE_MOUNT()
  windowRef.__WUJIE_MOUNT()
  windowRef.__WUJIE_UNMOUNT()
  windowRef.__WUJIE_UNMOUNT()
  windowRef.__WUJIE_MOUNT()

  assert.equal(creates, 2)
  assert.deepEqual(events, ['mount-1:#app', 'unmount-1', 'mount-2:#app'])
  assert.equal(windowRef.hostOwnedValue, 'keep')
  assert.equal(typeof windowRef.__WUJIE_MOUNT, 'function')
  assert.equal(typeof windowRef.__WUJIE_UNMOUNT, 'function')
})

test('lifecycle implementation never logs raw cleanup errors or context', async () => {
  const source = await readFile(lifecyclePath, 'utf8')

  assert.doesNotMatch(source, /console\.(?:log|info|debug|warn|error)/)
  assert.doesNotMatch(source, /(?:error|exception)\.(?:message|stack)|JSON\.stringify\((?:error|exception)/i)
  assert.doesNotMatch(source, /token|userInfo|namespaceId/i)
})

test('main bootstrap preserves app construction and delegates only lifecycle ownership', async () => {
  const source = await readFile(mainPath, 'utf8')

  assert.match(source, /import \{ createApp \} from 'vue'/)
  assert.match(source, /import \{ createPinia \} from 'pinia'/)
  assert.match(source, /import ElementPlus from 'element-plus'/)
  assert.match(source, /import 'element-plus\/dist\/index\.css'/)
  assert.match(source, /import '\.\/assets\/styles\/global\.css'/)
  assert.match(source, /app\.component\(key, component\)/)
  assert.match(source, /app\.use\(createPinia\(\)\)/)
  assert.match(source, /app\.use\(router\)/)
  assert.match(source, /app\.use\(ElementPlus, \{ locale: zhCn \}\)/)
  assert.match(source, /createAppRuntime\(createMyApp\)/)
  assert.match(source, /installWujieLifecycle\(window, runtime\)/)
  assert.doesNotMatch(source, /__WUJIE\b/)
  assert.doesNotMatch(source, /delete window\.__WUJIE_/)
})
