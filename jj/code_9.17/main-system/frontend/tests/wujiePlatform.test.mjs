import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

import {
  getAppOrigin,
  getAppUrl,
  getPlatformContext,
  isWujieEnv,
} from '../src/utils/platformRuntime.mjs'

const platformPath = new URL('../src/utils/platform.js', import.meta.url)
const runtimePath = new URL('../src/utils/platformRuntime.mjs', import.meta.url)
const requestPath = new URL('../src/utils/request.js', import.meta.url)

test('Wujie detection uses the standard flag or actual capability but not the old flag alone', () => {
  assert.equal(isWujieEnv({ __POWERED_BY_WUJIE__: true }), true)
  assert.equal(isWujieEnv({ $wujie: { props: {} } }), true)
  assert.equal(isWujieEnv({ __POWERED_BY_WUJIE__: false, $wujie: { props: {} } }), true)
  assert.equal(isWujieEnv({ __WUJIE: true }), false)
  assert.equal(isWujieEnv({}), false)
  assert.equal(isWujieEnv(null), false)
})

test('independent mode uses its own origin and resolves URLs from the origin root', () => {
  const windowRef = { location: { href: 'http://standalone.example:30080/faultdiag/#/overview' } }

  assert.equal(getAppOrigin(windowRef), 'http://standalone.example:30080')
  assert.equal(getAppUrl('/api/v1', windowRef), 'http://standalone.example:30080/api/v1')
  assert.equal(getAppUrl('annotate/', windowRef), 'http://standalone.example:30080/annotate/')
})

test('Wujie origin comes from child location rather than portal location', () => {
  const windowRef = {
    __POWERED_BY_WUJIE__: true,
    location: { href: 'http://portal.example/app' },
    $wujie: {
      location: { href: 'http://child.example:30080/nested/#/overview' },
      props: { token: 'Bearer test', userInfo: { UserId: 'u1' }, namespaceId: 3 },
    },
  }

  assert.equal(getAppOrigin(windowRef), 'http://child.example:30080')
  assert.equal(getAppUrl('/api/v1', windowRef), 'http://child.example:30080/api/v1')
})

test('Wujie origin safely falls back through app config and module URL', () => {
  const configured = {
    __POWERED_BY_WUJIE__: true,
    location: { href: 'https://portal.example/app' },
    $wujie: { location: { href: 'not an absolute URL' } },
    __APP_CONFIG__: { appOrigin: 'https://configured-child.example:30443/path/' },
  }
  assert.equal(
    getAppOrigin(configured, 'https://module-child.example/assets/platformRuntime.mjs'),
    'https://configured-child.example:30443',
  )

  const moduleFallback = {
    __POWERED_BY_WUJIE__: true,
    location: null,
    $wujie: { location: {} },
    __APP_CONFIG__: { appOrigin: 'javascript:invalid' },
  }
  assert.equal(
    getAppOrigin(moduleFallback, 'https://module-child.example:3443/assets/platformRuntime.mjs'),
    'https://module-child.example:3443',
  )

  const throwingLocation = {
    __POWERED_BY_WUJIE__: true,
    $wujie: {},
    __APP_CONFIG__: { appOrigin: 'https://configured-after-throw.example/path' },
  }
  Object.defineProperty(throwingLocation.$wujie, 'location', {
    get() { throw new Error('untrusted getter') },
  })
  assert.equal(
    getAppOrigin(throwingLocation, 'https://module-child.example/assets/platformRuntime.mjs'),
    'https://configured-after-throw.example',
  )
})

test('platform context is a frozen allowlisted snapshot with the exact Token', () => {
  const shared = { enabled: true }
  const hostUser = {
    UserId: 'u1',
    profile: {
      displayName: 'Ada',
      tags: ['operator', { level: 2 }],
    },
    firstShared: shared,
    secondShared: shared,
  }
  const windowRef = {
    $wujie: {
      props: {
        token: '  Bearer exact portal token  ',
        userInfo: hostUser,
        namespaceId: 7,
        basePath: '/must-not-leak',
        privateValue: 'must-not-leak',
      },
    },
  }

  const context = getPlatformContext(windowRef)
  assert.deepEqual(context, {
    token: '  Bearer exact portal token  ',
    userInfo: {
      UserId: 'u1',
      profile: {
        displayName: 'Ada',
        tags: ['operator', { level: 2 }],
      },
      firstShared: { enabled: true },
      secondShared: { enabled: true },
    },
    namespaceId: 7,
  })
  assert.equal(Object.isFrozen(context), true)
  assert.equal(Object.isFrozen(context.userInfo), true)
  assert.equal(Object.isFrozen(context.userInfo.profile), true)
  assert.equal(Object.isFrozen(context.userInfo.profile.tags), true)
  assert.equal(Object.isFrozen(context.userInfo.profile.tags[1]), true)
  assert.notEqual(context.userInfo.firstShared, context.userInfo.secondShared)
  assert.deepEqual(Object.keys(context).sort(), ['namespaceId', 'token', 'userInfo'])
  hostUser.profile.displayName = 'changed later'
  hostUser.profile.tags[1].level = 99
  assert.equal(context.userInfo.profile.displayName, 'Ada')
  assert.equal(context.userInfo.profile.tags[1].level, 2)
  assert.equal(Reflect.set(context.userInfo.profile, 'displayName', 'child mutation'), false)
  assert.equal(Reflect.set(context.userInfo.profile.tags[1], 'level', 100), false)
  assert.equal(hostUser.profile.displayName, 'changed later')
  assert.equal(hostUser.profile.tags[1].level, 99)
})

test('unsafe or non-JSON userInfo values are rejected without executing accessors', () => {
  let accessorCalls = 0
  const accessor = {}
  Object.defineProperty(accessor, 'secret', {
    enumerable: true,
    get() { accessorCalls += 1; return 'no' },
  })
  const cyclic = { id: 'cycle' }
  cyclic.self = cyclic
  const cases = [
    accessor,
    { nested: () => 'no' },
    { nested: new Date() },
    { nested: cyclic },
    { nested: undefined },
    { nested: Number.POSITIVE_INFINITY },
    new Proxy({}, { getPrototypeOf() { throw new Error('blocked prototype') } }),
    new Proxy({}, { ownKeys() { throw new Error('blocked keys') } }),
  ]

  for (const userInfo of cases) {
    assert.deepEqual(getPlatformContext({
      $wujie: { props: { token: 'keep', userInfo, namespaceId: 4 } },
    }), { token: 'keep', userInfo: null, namespaceId: 4 })
  }
  assert.equal(accessorCalls, 0)
})

test('userInfo snapshot rejects excessive depth and node counts', () => {
  const tooDeep = {}
  let cursor = tooDeep
  for (let depth = 0; depth < 20; depth += 1) {
    cursor.next = {}
    cursor = cursor.next
  }
  const tooManyNodes = { values: Array.from({ length: 300 }, (_, index) => index) }

  assert.equal(getPlatformContext({ $wujie: { props: { userInfo: tooDeep } } }).userInfo, null)
  assert.equal(getPlatformContext({ $wujie: { props: { userInfo: tooManyNodes } } }).userInfo, null)
})

test('missing or malformed props and values produce an empty safe context', () => {
  const empty = { token: '', userInfo: null, namespaceId: null }
  for (const props of [undefined, null, 'bad', [], 7]) {
    assert.deepEqual(getPlatformContext({ $wujie: { props } }), empty)
  }
  assert.deepEqual(getPlatformContext({
    $wujie: { props: { token: 7, userInfo: [], namespaceId: {} } },
  }), empty)
  assert.deepEqual(getPlatformContext({ __POWERED_BY_WUJIE__: true }), empty)
})

test('application URLs cannot escape the credential-free child HTTP origin', () => {
  const windowRef = {
    __POWERED_BY_WUJIE__: true,
    $wujie: { location: { href: 'https://child.example:30443/app/#/home' } },
  }
  const stableError = /application URL must stay on the child origin/

  assert.equal(getAppUrl('/api/v1', windowRef), 'https://child.example:30443/api/v1')
  assert.equal(getAppUrl('api/v1', windowRef), 'https://child.example:30443/api/v1')
  assert.equal(
    getAppUrl('https://child.example:30443/api/v1', windowRef),
    'https://child.example:30443/api/v1',
  )
  for (const unsafe of [
    'https://other.example/api/v1',
    '//other.example/api/v1',
    '\\\\other.example\\api\\v1',
    'javascript:alert(1)',
    'https://user:password@child.example:30443/api/v1',
  ]) {
    assert.throws(() => getAppUrl(unsafe, windowRef), stableError)
  }
})

test('platform compatibility module keeps stable exports without the legacy detector', async () => {
  const source = await readFile(platformPath, 'utf8')

  for (const name of ['getWujieProps', 'getToken', 'getUserInfo', 'getNamespaceId', 'isWujieEnv']) {
    assert.match(source, new RegExp(`export (?:function |\\{[^}]*\\b)${name}`))
  }
  assert.match(source, /getPlatformContext/)
  assert.doesNotMatch(source, /__WUJIE\b/)
})

async function importRequestWithStubs() {
  const source = await readFile(requestPath, 'utf8')
  const captured = { createConfig: null, requestHandlers: null, responseHandlers: null, appPaths: [] }
  let token = ''
  const instance = {
    interceptors: {
      request: { use: (...handlers) => { captured.requestHandlers = handlers } },
      response: { use: (...handlers) => { captured.responseHandlers = handlers } },
    },
  }
  globalThis.__wujieRequestStubs = {
    axios: {
      CanceledError: class CanceledError extends Error {
        constructor(message, config) {
          super(message)
          this.code = 'ERR_CANCELED'
          this.config = config
          this.__CANCEL__ = true
        }
      },
      create(config) {
        captured.createConfig = config
        return instance
      },
      isCancel(error) { return error?.__CANCEL__ === true },
    },
    ElMessage: { error() {} },
    getAppUrl(path) {
      captured.appPaths.push(path)
      return `https://child.example:30080${path}`
    },
    getToken() { return token },
  }
  const moduleSource = source
    .replace("import axios from 'axios'", 'const axios = globalThis.__wujieRequestStubs.axios')
    .replace("import { ElMessage } from 'element-plus'", 'const { ElMessage } = globalThis.__wujieRequestStubs')
    .replace(
      /import \{\s*getAppUrl,\s*getToken\s*\} from ['"]\.\/platform['"]/,
      'const { getAppUrl, getToken } = globalThis.__wujieRequestStubs',
    )
  const imported = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(moduleSource)}`)
  return {
    captured,
    imported,
    setToken(value) { token = value },
  }
}

test('Axios uses the absolute child API origin and attaches only a nonempty exact Token', async () => {
  const runtime = await importRequestWithStubs()
  runtime.imported.installRequestScope({ register() {} })
  assert.equal(runtime.imported.default.interceptors !== undefined, true)
  assert.deepEqual(runtime.captured.appPaths, ['/api/v1'])
  assert.equal(runtime.captured.createConfig.baseURL, 'https://child.example:30080/api/v1')

  runtime.setToken(' Bearer unchanged ')
  const withToken = runtime.captured.requestHandlers[0]({ headers: {} })
  assert.equal(withToken.headers['X-Token'], ' Bearer unchanged ')

  runtime.setToken('')
  const withoutToken = runtime.captured.requestHandlers[0]({ headers: {} })
  assert.equal(Object.hasOwn(withoutToken.headers, 'X-Token'), false)
})

test('platform and request sources never persist or expose the portal Token', async () => {
  const combined = [
    await readFile(runtimePath, 'utf8'),
    await readFile(platformPath, 'utf8'),
    await readFile(requestPath, 'utf8'),
  ].join('\n')

  assert.doesNotMatch(combined, /localStorage|sessionStorage|document\.cookie|console\.(?:log|info|debug|warn|error)/)
  assert.doesNotMatch(combined, /[?&#](?:token|Token)=|URLSearchParams/)
})
