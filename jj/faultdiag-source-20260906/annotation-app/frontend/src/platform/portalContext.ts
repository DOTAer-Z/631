const REQUEST_TYPE = 'faultdiag:context:request'
const RESPONSE_TYPE = 'faultdiag:context:response'
const PROTOCOL_VERSION = 2
const HANDSHAKE_TIMEOUT_MS = 5_000
const MAX_USER_INFO_DEPTH = 8
const MAX_USER_INFO_NODES = 256
const REQUEST_ID_PATTERN = /^[A-Za-z0-9_-]{16,128}$/

export type PortalUserInfo = Readonly<Record<string, unknown>>

export interface PortalContext {
  readonly token: string
  readonly userInfo: PortalUserInfo | null
  readonly namespaceId: string | number | null
}

interface WindowReference {
  readonly parent: unknown
  readonly location?: { readonly href?: unknown }
  addEventListener(type: 'message', listener: (event: MessageEvent) => void): void
  removeEventListener(type: 'message', listener: (event: MessageEvent) => void): void
}

interface DocumentReference {
  readonly referrer: string
}

export interface PortalContextRuntime {
  readonly windowRef?: WindowReference
  readonly documentRef?: DocumentReference
  readonly requestIdFactory?: () => string
}

interface ParentReference {
  postMessage(message: unknown, targetOrigin: string): void
}

interface PendingAttempt {
  readonly generation: number
  readonly requestId: string
  readonly windowRef: WindowReference
  readonly listener: (event: MessageEvent) => void
  readonly promise: Promise<PortalContext>
  readonly reject: (reason: PortalContextError) => void
  timeoutId: ReturnType<typeof setTimeout> | null
}

interface CloneState {
  readonly active: WeakSet<object>
  nodes: number
}

const EMPTY_CONTEXT: PortalContext = Object.freeze({
  token: '',
  userInfo: null,
  namespaceId: null
})

// 是否严格要求门户上下文（同源 + postMessage 握手）。默认关闭：嵌入失败时降级为空上下文，
// 应用仍可匿名正常启动（后端不强制鉴权）。构建时设 VITE_REQUIRE_PORTAL_CONTEXT=true 可恢复
// 旧的严格行为（失败即显示「门户连接失败」）。
const REQUIRE_PORTAL_CONTEXT = (() => {
  try {
    return import.meta.env?.VITE_REQUIRE_PORTAL_CONTEXT === 'true'
  } catch {
    return false
  }
})()

let context: PortalContext = EMPTY_CONTEXT
let initialized = false
let generation = 0
let pendingAttempt: PendingAttempt | null = null

export class PortalContextError extends Error {
  readonly code: string

  constructor(message: string, code: string) {
    super(message)
    this.name = 'PortalContextError'
    this.code = code
  }
}

export class PortalContextOriginError extends PortalContextError {
  constructor() {
    super('Portal parent origin is unavailable or invalid', 'PORTAL_CONTEXT_ORIGIN')
    this.name = 'PortalContextOriginError'
  }
}

export class PortalContextTimeoutError extends PortalContextError {
  constructor() {
    super('Portal context handshake timed out', 'PORTAL_CONTEXT_TIMEOUT')
    this.name = 'PortalContextTimeoutError'
  }
}

export class PortalContextClearedError extends PortalContextError {
  constructor() {
    super('Portal context handshake was cleared', 'PORTAL_CONTEXT_CLEARED')
    this.name = 'PortalContextClearedError'
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false
  const prototype = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}

function safeHttpOrigin(value: unknown): string | null {
  if (typeof value !== 'string' || !value) return null
  try {
    const url = new URL(value)
    if (
      (url.protocol !== 'http:' && url.protocol !== 'https:')
      || url.username
      || url.password
    ) return null
    return url.origin
  } catch {
    return null
  }
}

function ownDataValue(object: object, key: PropertyKey): unknown {
  const descriptor = Object.getOwnPropertyDescriptor(object, key)
  if (!descriptor?.enumerable || !('value' in descriptor)) return undefined
  return descriptor.value
}

function isArrayIndex(key: PropertyKey, length: number): key is string {
  if (typeof key !== 'string' || !/^\d+$/.test(key)) return false
  const index = Number(key)
  return Number.isSafeInteger(index) && index >= 0 && index < length && String(index) === key
}

function cloneJsonValue(value: unknown, state: CloneState, depth: number): unknown {
  state.nodes += 1
  if (state.nodes > MAX_USER_INFO_NODES || depth > MAX_USER_INFO_DEPTH) {
    throw new TypeError('unsafe portal user info')
  }
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return value
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new TypeError('unsafe portal user info')
    return value
  }
  if (typeof value !== 'object') throw new TypeError('unsafe portal user info')
  if (!Array.isArray(value) && !isPlainObject(value)) throw new TypeError('unsafe portal user info')
  if (state.active.has(value)) throw new TypeError('unsafe portal user info')

  state.active.add(value)
  try {
    const keys = Reflect.ownKeys(value)
    if (keys.some((key) => typeof key === 'symbol')) throw new TypeError('unsafe portal user info')
    const propertyKeys = Array.isArray(value) ? keys.filter((key) => key !== 'length') : keys
    if (propertyKeys.length > MAX_USER_INFO_NODES - state.nodes) {
      throw new TypeError('unsafe portal user info')
    }

    if (Array.isArray(value)) {
      const lengthDescriptor = Object.getOwnPropertyDescriptor(value, 'length')
      const length = lengthDescriptor && 'value' in lengthDescriptor ? lengthDescriptor.value : -1
      if (!Number.isSafeInteger(length) || length < 0) throw new TypeError('unsafe portal user info')
      if (propertyKeys.some((key) => !isArrayIndex(key, length))) {
        throw new TypeError('unsafe portal user info')
      }
      const clone: unknown[] = []
      for (let index = 0; index < length; index += 1) {
        const descriptor = Object.getOwnPropertyDescriptor(value, String(index))
        if (!descriptor?.enumerable || !('value' in descriptor)) {
          throw new TypeError('unsafe portal user info')
        }
        clone.push(cloneJsonValue(descriptor.value, state, depth + 1))
      }
      return Object.freeze(clone)
    }

    const clone: Record<string, unknown> = {}
    for (const key of keys) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key)
      if (!descriptor) throw new TypeError('unsafe portal user info')
      if (typeof key !== 'string' || !descriptor.enumerable || !('value' in descriptor)) {
        throw new TypeError('unsafe portal user info')
      }
      Object.defineProperty(clone, key, {
        configurable: false,
        enumerable: true,
        writable: false,
        value: cloneJsonValue(descriptor.value, state, depth + 1)
      })
    }
    return Object.freeze(clone)
  } finally {
    state.active.delete(value)
  }
}

function cloneResponse(value: unknown, expectedRequestId: string): PortalContext | null {
  try {
    if (!isPlainObject(value)) return null
    if (ownDataValue(value, 'type') !== RESPONSE_TYPE) return null
    if (ownDataValue(value, 'version') !== PROTOCOL_VERSION) return null
    if (ownDataValue(value, 'requestId') !== expectedRequestId) return null

    const token = ownDataValue(value, 'token')
    const userInfoValue = ownDataValue(value, 'userInfo')
    const namespaceId = ownDataValue(value, 'namespaceId')
    if (typeof token !== 'string') return null
    if (userInfoValue !== null && !isPlainObject(userInfoValue)) return null
    if (
      namespaceId !== null
      && !(typeof namespaceId === 'string' && namespaceId.length > 0)
      && !(typeof namespaceId === 'number' && Number.isFinite(namespaceId))
    ) return null

    const userInfo = userInfoValue === null
      ? null
      : cloneJsonValue(userInfoValue, { active: new WeakSet(), nodes: 0 }, 0) as PortalUserInfo
    return Object.freeze({ token, userInfo, namespaceId })
  } catch {
    return null
  }
}

function resolveWindow(windowRef?: WindowReference): WindowReference {
  const candidate = windowRef ?? globalThis.window
  if (
    !candidate
    || typeof candidate.addEventListener !== 'function'
    || typeof candidate.removeEventListener !== 'function'
  ) {
    throw new PortalContextOriginError()
  }
  return candidate as unknown as WindowReference
}

function resolveDocument(documentRef?: DocumentReference): DocumentReference {
  return documentRef ?? globalThis.document
}

function createSecureRequestId(): string {
  const cryptoRef = globalThis.crypto
  if (!cryptoRef || typeof cryptoRef.getRandomValues !== 'function') {
    throw new PortalContextError('Secure request ID generation is unavailable', 'PORTAL_CONTEXT_RANDOM')
  }
  const bytes = new Uint8Array(16)
  cryptoRef.getRandomValues(bytes)
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function cleanupAttempt(attempt: PendingAttempt) {
  attempt.windowRef.removeEventListener('message', attempt.listener)
  if (attempt.timeoutId !== null) {
    clearTimeout(attempt.timeoutId)
    attempt.timeoutId = null
  }
}

function failAttempt(attempt: PendingAttempt, error: PortalContextError) {
  if (pendingAttempt !== attempt) return
  pendingAttempt = null
  cleanupAttempt(attempt)
  attempt.reject(error)
}

export function getAnnotationAppOrigin(windowRef?: Pick<WindowReference, 'location'>): string {
  const origin = safeHttpOrigin((windowRef ?? globalThis.window)?.location?.href)
  if (!origin) throw new PortalContextOriginError()
  return origin
}

export function getPortalContext(): PortalContext {
  return context
}

/**
 * 注入门户上下文（真·深融合版）。
 *
 * 深融合后标注不再是主系统的 iframe 子应用，不再发 postMessage 握手，
 * 改由主系统在挂载标注时把登录态 { token, userInfo, namespaceId } 直接注入。
 * 注入即置 initialized=true，后续 getPortalContext() 返回该上下文，
 * http.ts 的请求拦截器随即把 token 注入 X-Token 头。
 *
 * @param value 主系统登录态（符合 PortalContext 形状）
 * @returns 本次写入并冻结的上下文快照
 */
export function setPortalContext(value: PortalContext): PortalContext {
  if (pendingAttempt) {
    // 有未完成的握手则先作废（深融合路径不应有，防御处理）
    failAttempt(pendingAttempt, new PortalContextClearedError())
  }
  context = Object.freeze({
    token: typeof value?.token === 'string' ? value.token : '',
    userInfo: value?.userInfo ?? null,
    namespaceId: value?.namespaceId ?? null,
  })
  initialized = true
  return context
}

export function initializePortalContextStrict(runtime: PortalContextRuntime = {}): Promise<PortalContext> {
  if (pendingAttempt) return pendingAttempt.promise
  if (initialized) return Promise.resolve(context)

  const windowRef = resolveWindow(runtime.windowRef)
  if (windowRef.parent === windowRef) {
    context = EMPTY_CONTEXT
    initialized = true
    return Promise.resolve(context)
  }

  const parentOrigin = safeHttpOrigin(resolveDocument(runtime.documentRef).referrer)
  if (!parentOrigin || parentOrigin !== getAnnotationAppOrigin(windowRef)) {
    return Promise.reject(new PortalContextOriginError())
  }
  const parent = windowRef.parent as ParentReference
  if (!parent || typeof parent.postMessage !== 'function') {
    return Promise.reject(new PortalContextOriginError())
  }
  let requestId: string
  try {
    requestId = (runtime.requestIdFactory ?? createSecureRequestId)()
  } catch (error) {
    return Promise.reject(error instanceof PortalContextError
      ? error
      : new PortalContextError('Secure request ID generation failed', 'PORTAL_CONTEXT_RANDOM'))
  }
  if (!REQUEST_ID_PATTERN.test(requestId)) {
    return Promise.reject(new PortalContextError('Generated request ID is invalid', 'PORTAL_CONTEXT_REQUEST_ID'))
  }

  const attemptGeneration = generation
  let resolvePromise!: (value: PortalContext) => void
  let rejectPromise!: (reason: PortalContextError) => void
  const promise = new Promise<PortalContext>((resolve, reject) => {
    resolvePromise = resolve
    rejectPromise = reject
  })
  const listener = (event: MessageEvent) => {
    const attempt = pendingAttempt
    if (
      !attempt
      || attempt.generation !== attemptGeneration
      || generation !== attemptGeneration
      || event.source !== parent
      || event.origin !== parentOrigin
    ) return

    const snapshot = cloneResponse(event.data, requestId)
    if (!snapshot) return
    pendingAttempt = null
    cleanupAttempt(attempt)
    context = snapshot
    initialized = true
    resolvePromise(snapshot)
  }
  const attempt: PendingAttempt = {
    generation: attemptGeneration,
    requestId,
    windowRef,
    listener,
    promise,
    reject: rejectPromise,
    timeoutId: null
  }

  pendingAttempt = attempt
  windowRef.addEventListener('message', listener)
  attempt.timeoutId = setTimeout(() => failAttempt(attempt, new PortalContextTimeoutError()), HANDSHAKE_TIMEOUT_MS)
  try {
    parent.postMessage(Object.freeze({
      type: REQUEST_TYPE,
      version: PROTOCOL_VERSION,
      requestId
    }), parentOrigin)
  } catch {
    failAttempt(attempt, new PortalContextError('Portal context request failed', 'PORTAL_CONTEXT_REQUEST'))
  }
  return promise
}

export function initializePortalContext(runtime: PortalContextRuntime = {}): Promise<PortalContext> {
  const init = initializePortalContextStrict(runtime)
  if (REQUIRE_PORTAL_CONTEXT) return init
  return init.catch((error) => {
    if (!(error instanceof PortalContextError)) throw error
    console.warn(`[portalContext] ${error.message}; continuing without a portal context`)
    context = EMPTY_CONTEXT
    initialized = true
    return context
  })
}

export function clearPortalContext(): void {
  generation += 1
  initialized = false
  context = EMPTY_CONTEXT
  const attempt = pendingAttempt
  if (!attempt) return
  pendingAttempt = null
  cleanupAttempt(attempt)
  attempt.reject(new PortalContextClearedError())
}
