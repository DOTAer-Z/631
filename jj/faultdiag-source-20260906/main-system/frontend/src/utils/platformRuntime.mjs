const EMPTY_CONTEXT = Object.freeze({ token: '', userInfo: null, namespaceId: null })
const MAX_USER_INFO_DEPTH = 8
const MAX_USER_INFO_NODES = 256
const APP_URL_ERROR = 'application URL must stay on the child origin'

function safeHttpOrigin(value) {
  if (typeof value !== 'string' || !value) return null
  try {
    const url = new URL(value)
    return (
      (url.protocol === 'http:' || url.protocol === 'https:')
      && !url.username
      && !url.password
    ) ? url.origin : null
  } catch {
    return null
  }
}

function isPlainObject(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function dataProperty(object, key) {
  const descriptor = Object.getOwnPropertyDescriptor(object, key)
  return descriptor && 'value' in descriptor ? descriptor.value : undefined
}

function isArrayIndex(key, length) {
  if (!/^\d+$/.test(key)) return false
  const index = Number(key)
  return Number.isSafeInteger(index) && index >= 0 && index < length && String(index) === key
}

function cloneJsonValue(value, state, depth, requireFrozen = false) {
  state.nodes += 1
  if (state.nodes > MAX_USER_INFO_NODES || depth > MAX_USER_INFO_DEPTH) throw new TypeError('unsafe user info')
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new TypeError('unsafe user info')
    return value
  }
  if (typeof value !== 'object') throw new TypeError('unsafe user info')
  if (!Array.isArray(value) && !isPlainObject(value)) throw new TypeError('unsafe user info')
  if (requireFrozen && !Object.isFrozen(value)) throw new TypeError('unsafe user info')
  if (state.active.has(value)) throw new TypeError('unsafe user info')

  state.active.add(value)
  try {
    const descriptors = Object.getOwnPropertyDescriptors(value)
    const keys = Reflect.ownKeys(descriptors)
    if (keys.some(key => typeof key === 'symbol')) throw new TypeError('unsafe user info')

    if (Array.isArray(value)) {
      const clone = []
      for (const key of keys) {
        if (key === 'length') continue
        if (!isArrayIndex(key, value.length)) throw new TypeError('unsafe user info')
      }
      for (let index = 0; index < value.length; index += 1) {
        const descriptor = descriptors[index]
        if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) throw new TypeError('unsafe user info')
        clone.push(cloneJsonValue(descriptor.value, state, depth + 1, requireFrozen))
      }
      return Object.freeze(clone)
    }

    const clone = {}
    for (const key of keys) {
      const descriptor = descriptors[key]
      if (!descriptor.enumerable || !('value' in descriptor)) throw new TypeError('unsafe user info')
      Object.defineProperty(clone, key, {
        configurable: false,
        enumerable: true,
        writable: false,
        value: cloneJsonValue(descriptor.value, state, depth + 1, requireFrozen),
      })
    }
    return Object.freeze(clone)
  } finally {
    state.active.delete(value)
  }
}

function cloneUserInfo(value) {
  try {
    if (!isPlainObject(value)) return null
    return cloneJsonValue(value, { active: new WeakSet(), nodes: 0 }, 0)
  } catch {
    return null
  }
}

export function cloneFrozenPlatformContext(value) {
  try {
    if (!isPlainObject(value) || !Object.isFrozen(value)) return null
    const descriptors = Object.getOwnPropertyDescriptors(value)
    const tokenDescriptor = descriptors.token
    const userInfoDescriptor = descriptors.userInfo
    const namespaceDescriptor = descriptors.namespaceId
    if (
      !tokenDescriptor?.enumerable
      || !('value' in tokenDescriptor)
      || !userInfoDescriptor?.enumerable
      || !('value' in userInfoDescriptor)
      || !namespaceDescriptor?.enumerable
      || !('value' in namespaceDescriptor)
    ) return null

    const token = tokenDescriptor.value
    const userInfoValue = userInfoDescriptor.value
    const namespaceId = namespaceDescriptor.value
    if (typeof token !== 'string') return null
    if (
      namespaceId !== null
      && !(typeof namespaceId === 'string' && namespaceId.length > 0)
      && !(typeof namespaceId === 'number' && Number.isFinite(namespaceId))
    ) return null

    const userInfo = userInfoValue === null
      ? null
      : cloneJsonValue(userInfoValue, { active: new WeakSet(), nodes: 0 }, 0, true)
    if (userInfoValue !== null && !isPlainObject(userInfoValue)) return null
    return Object.freeze({ token, userInfo, namespaceId })
  } catch {
    return null
  }
}

function readCandidate(reader) {
  try {
    return reader()
  } catch {
    return undefined
  }
}

export function isWujieEnv(windowRef = globalThis.window) {
  try {
    return Boolean(windowRef?.__POWERED_BY_WUJIE__ || windowRef?.$wujie)
  } catch {
    return false
  }
}

export function getPlatformContext(windowRef = globalThis.window) {
  try {
    const props = windowRef?.$wujie?.props
    if (!isPlainObject(props)) return EMPTY_CONTEXT

    const tokenValue = dataProperty(props, 'token')
    const userInfoValue = dataProperty(props, 'userInfo')
    const namespaceValue = dataProperty(props, 'namespaceId')
    const token = typeof tokenValue === 'string' ? tokenValue : ''
    const userInfo = cloneUserInfo(userInfoValue)
    const namespaceId = (
      (typeof namespaceValue === 'string' && namespaceValue.length > 0)
      || (typeof namespaceValue === 'number' && Number.isFinite(namespaceValue))
    ) ? namespaceValue : null
    return Object.freeze({ token, userInfo, namespaceId })
  } catch {
    return EMPTY_CONTEXT
  }
}

export function getAppOrigin(windowRef = globalThis.window, moduleUrl = import.meta.url) {
  const readers = isWujieEnv(windowRef)
    ? [
        () => windowRef?.$wujie?.location?.href,
        () => windowRef?.__APP_CONFIG__?.appOrigin,
        () => moduleUrl,
      ]
    : [
        () => windowRef?.location?.href,
        () => windowRef?.__APP_CONFIG__?.appOrigin,
        () => moduleUrl,
      ]
  for (const reader of readers) {
    const candidate = readCandidate(reader)
    const origin = safeHttpOrigin(candidate)
    if (origin) return origin
  }
  throw new TypeError('application origin is unavailable')
}

export function getAppUrl(path, windowRef = globalThis.window, moduleUrl = import.meta.url) {
  const origin = getAppOrigin(windowRef, moduleUrl)
  if (typeof path !== 'string' || path.includes('\\')) throw new TypeError(APP_URL_ERROR)
  try {
    const url = new URL(path, `${origin}/`)
    if (
      (url.protocol !== 'http:' && url.protocol !== 'https:')
      || url.origin !== origin
      || url.username
      || url.password
    ) {
      throw new TypeError(APP_URL_ERROR)
    }
    return url.toString()
  } catch {
    throw new TypeError(APP_URL_ERROR)
  }
}

/**
 * 获取子应用真实部署地址(跳过 wujie 虚拟路径)
 * 用于标注 iframe——标注子系统在同一 nginx 下的 /annotate/,必须用真实 origin
 */
export function getRealAppOrigin(windowRef = globalThis.window, moduleUrl = import.meta.url) {
  const readers = [
    () => windowRef?.location?.href,            // 真实浏览器地址(wujie 环境下是宿主地址,但同源 iframe 需要它)
    () => windowRef?.__APP_CONFIG__?.appOrigin,
    () => moduleUrl,
  ]
  for (const reader of readers) {
    const candidate = readCandidate(reader)
    const origin = safeHttpOrigin(candidate)
    if (origin) return origin
  }
  throw new TypeError('real application origin is unavailable')
}

/**
 * 拼接标注子系统 URL——在 wujie 环境下用真实部署 origin,避免指向宿主虚拟路径
 */
export function getAnnotateUrl(path = '/annotate/', windowRef = globalThis.window, moduleUrl = import.meta.url) {
  const origin = getRealAppOrigin(windowRef, moduleUrl)
  if (typeof path !== 'string' || path.includes('\\')) throw new TypeError(APP_URL_ERROR)
  try {
    const url = new URL(path, `${origin}/`)
    if (
      (url.protocol !== 'http:' && url.protocol !== 'https:')
      || url.origin !== origin
      || url.username
      || url.password
    ) {
      throw new TypeError(APP_URL_ERROR)
    }
    return url.toString()
  } catch {
    throw new TypeError(APP_URL_ERROR)
  }
}
