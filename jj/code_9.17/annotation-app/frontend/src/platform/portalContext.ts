/**
 * 标注子系统的门户上下文(真·深融合版)。
 *
 * 深融合后标注不再是 iframe 子应用:
 * - **登录态**由宿主(主系统)注入——优先经 `platform/runtime.ts` 的平台运行时
 *   (`getContext()` 每次现读,支持 token 轮换),或由宿主一次性 `setPortalContext` 写入。
 * - **origin** 一律经宿主注入的 `getOrigin()` 取得(宿主 `platformRuntime.mjs` 的
 *   `getAppOrigin` 是全系统唯一的 origin 算法,含 wujie 读取顺序)。标注侧**不再**
 *   保留任何 wujie 判定 / origin 推导的副本——那正是此前两份实现漂移、导致 wujie 下
 *   API 打到门户 origin 的根因。
 *
 * 原先用于校验 postMessage 响应的深克隆机械(cloneJsonValue / isArrayIndex 等)已随
 * 握手一并移除:它与宿主 `platformRuntime.mjs` 里的同名实现完全重复,而注入路径的数据
 * 已由宿主 `getPlatformContext` 克隆并冻结。
 */

import {
  EMPTY_CONTEXT,
  readInjectedContext,
  readInjectedOrigin,
  readStandaloneOrigin
} from './runtime'

export type { PortalContext, PortalUserInfo } from './runtime'
export { EMPTY_CONTEXT } from './runtime'

import type { PortalContext } from './runtime'

let context: PortalContext = EMPTY_CONTEXT

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
    super('Annotation application origin is unavailable or invalid', 'PORTAL_CONTEXT_ORIGIN')
    this.name = 'PortalContextOriginError'
  }
}

/**
 * 解析标注应用所在的 origin(即带 `/annotate-api/` 反代的集成入口)。
 *
 * 读取顺序:
 * 1. 宿主注入的平台运行时 `getOrigin()`——深融合态的唯一正解(wujie 与非 wujie 皆由
 *    宿主 `getAppOrigin` 统一裁决);
 * 2. 未注入(标注独立部署)或宿主实现异常时,退回本地非 wujie 推导。
 *
 * 注:本函数每次调用现算,不缓存;`api/http.ts` 自行 memoize 首次成功结果。
 */
export function getAnnotationAppOrigin(
  windowRef?: { readonly location?: { readonly href?: unknown } }
): string {
  const injected = readInjectedOrigin()
  if (injected) return injected

  const standalone = readStandaloneOrigin(windowRef)
  if (standalone) return standalone

  throw new PortalContextOriginError()
}

/**
 * 当前登录态。宿主注入了运行时则每次现读(token 轮换即时生效),
 * 否则返回最近一次 `setPortalContext` 写入的值(默认空上下文=匿名)。
 */
export function getPortalContext(): PortalContext {
  const injected = readInjectedContext()
  if (injected) return injected
  return context
}

/**
 * 由宿主注入登录态(深融合路径)。
 *
 * 已注入平台运行时的情况下,`getPortalContext()` 优先走运行时的惰性读取,
 * 本函数写入的值仅作为运行时不可用时的兜底快照。
 */
export function setPortalContext(value: PortalContext): PortalContext {
  const namespaceValue = value?.namespaceId
  context = Object.freeze({
    token: typeof value?.token === 'string' ? value.token : '',
    userInfo: value?.userInfo ?? null,
    namespaceId: (
      (typeof namespaceValue === 'string' && namespaceValue.length > 0)
      || (typeof namespaceValue === 'number' && Number.isFinite(namespaceValue))
    ) ? namespaceValue : null
  })
  return context
}

/** 清空本地上下文快照(卸载/登出)。不影响宿主注入的平台运行时。 */
export function clearPortalContext(): void {
  context = EMPTY_CONTEXT
}
