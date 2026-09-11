/**
 * 标注子系统的「平台运行时」注入缝(真·深融合根因修复)。
 *
 * 背景:标注前端已在编译层融入主前端(vite `@annotation` 别名 + `annotationRoutes`
 * 作为 `data-governance/annotation` 的 children),但**运行时事实**(app origin、
 * 登录态)此前仍由标注自己独立推导一份,与宿主 `platformRuntime.mjs` 各算一套。
 * wujie 微前端下两套算出不同答案 → 标注 API 打到门户 origin → `/annotate-api/` 无反代
 * → 404/非 JSON → 仪表盘 `undefined.length` 崩溃。
 *
 * 解法:环境事实的推导权**收归宿主一份**。宿主在启动时注入本运行时,标注一律经此读取;
 * 未注入时(标注独立部署)退回本文件的独立实现。标注侧不再保留任何 wujie 判定逻辑
 * ——那是宿主的职责。
 *
 * 注入的 getter 必须是**惰性闭包**(每次调用现读 `window`),不能在注入时求值快照,
 * 否则 wujie 注入时机会再次引入顺序依赖。
 */

export type PortalUserInfo = Readonly<Record<string, unknown>>

export interface PortalContext {
  readonly token: string
  readonly userInfo: PortalUserInfo | null
  readonly namespaceId: string | number | null
}

export const EMPTY_CONTEXT: PortalContext = Object.freeze({
  token: '',
  userInfo: null,
  namespaceId: null
})

/**
 * 宿主(主系统)向标注注入的平台运行时契约。
 *
 * - `getOrigin()` 返回集成入口 origin(带 `/annotate-api/` 反代的那个 nginx)。
 *   宿主实现为 `platformRuntime.mjs` 的 `getAppOrigin`——全系统唯一的 origin 算法。
 * - `getContext()` 返回当前登录态。宿主实现为 `getPlatformContext`(读 `$wujie.props`),
 *   每次请求现读,因此支持 token 轮换。
 */
export interface AnnotationPlatformRuntime {
  readonly getOrigin: () => string
  readonly getContext: () => PortalContext
}

/** 校验并归一化为 http(s) origin;拒绝非 http(s)、拒绝带用户名/密码的 URL。 */
export function safeHttpOrigin(value: unknown): string | null {
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

interface StandaloneWindowLike {
  readonly location?: { readonly href?: unknown }
  readonly __APP_CONFIG__?: { readonly appOrigin?: unknown }
}

let injectedRuntime: AnnotationPlatformRuntime | null = null

/**
 * 宿主注入平台运行时。必须在任何标注 API 请求发生前调用;
 * 因为 baseURL 已惰性化(见 `api/http.ts`),注入只需早于**首次请求**,
 * 不再要求早于模块求值。
 */
export function setAnnotationPlatformRuntime(runtime: AnnotationPlatformRuntime): void {
  if (
    !runtime
    || typeof runtime.getOrigin !== 'function'
    || typeof runtime.getContext !== 'function'
  ) {
    throw new TypeError('annotation platform runtime requires getOrigin() and getContext()')
  }
  injectedRuntime = runtime
}

export function clearAnnotationPlatformRuntime(): void {
  injectedRuntime = null
}

export function getAnnotationPlatformRuntime(): AnnotationPlatformRuntime | null {
  return injectedRuntime
}

/**
 * 独立部署(未注入宿主运行时)时的 origin 推导。
 *
 * 只处理非 wujie 情形——标注独立部署不可能运行在 wujie 里。wujie 的读取顺序
 * 由宿主 `getAppOrigin` 独占,标注不再重复一份。
 */
export function readStandaloneOrigin(
  windowRef?: Pick<StandaloneWindowLike, 'location' | '__APP_CONFIG__'>
): string | null {
  const win = (windowRef ?? globalThis.window) as StandaloneWindowLike | undefined
  const readers: Array<() => unknown> = [
    () => win?.location?.href,
    () => win?.__APP_CONFIG__?.appOrigin
  ]
  for (const read of readers) {
    let value: unknown
    try {
      value = read()
    } catch {
      continue
    }
    const origin = safeHttpOrigin(value)
    if (origin) return origin
  }
  return null
}

/**
 * 经注入的宿主运行时读取 origin。宿主实现异常或返回非法值时返回 null,
 * 由调用方退回独立推导——注入方出错不应让标注整站不可用。
 */
export function readInjectedOrigin(): string | null {
  const runtime = injectedRuntime
  if (!runtime) return null
  try {
    return safeHttpOrigin(runtime.getOrigin())
  } catch {
    return null
  }
}

/**
 * 经注入的宿主运行时读取登录态。宿主异常或返回非法结构时返回 null,
 * 由调用方退回本地上下文。
 */
export function readInjectedContext(): PortalContext | null {
  const runtime = injectedRuntime
  if (!runtime) return null
  try {
    const value = runtime.getContext()
    if (!value || typeof value !== 'object') return null
    const token = typeof value.token === 'string' ? value.token : ''
    const userInfo = value.userInfo ?? null
    const namespaceValue = value.namespaceId
    const namespaceId = (
      (typeof namespaceValue === 'string' && namespaceValue.length > 0)
      || (typeof namespaceValue === 'number' && Number.isFinite(namespaceValue))
    ) ? namespaceValue : null
    return Object.freeze({ token, userInfo, namespaceId })
  } catch {
    return null
  }
}
