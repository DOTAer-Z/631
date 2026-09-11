import axios from 'axios'

import { getAnnotationAppOrigin, getPortalContext } from '@annotation/platform/portalContext'

/**
 * baseURL 惰性求值(真·深融合根因修复的第二半)。
 *
 * 此前 baseURL 在**模块加载期**算死:`const baseURL = ...` 紧跟 import。深融合下标注代码
 * 被编进主 bundle,一旦 bundle 求值早于 wujie 注入 `$wujie`/`__APP_CONFIG__`,origin 就会
 * 算成门户地址并永久固化,`/annotate-api/*` 全部 404。
 *
 * 改为首次请求时才求值 + memoize 后,注入顺序不再影响正确性:只要注入早于**首次请求**
 * (Vue 应用挂载后才发请求,注入在 `createMyApp` 里)即可。校验规则一字未改。
 */
let memoizedBaseURL: string | null = null

/**
 * 解析并校验标注 API 的 baseURL:必须是 http(s)、不得携带凭据、且被约束在标注应用
 * 自身的 origin 上(防止 `VITE_API_BASE_URL` 被配成外部地址而外泄 token)。
 */
export function resolveApiBaseURL(): string {
  if (memoizedBaseURL !== null) return memoizedBaseURL

  const annotationAppOrigin = getAnnotationAppOrigin()
  const configuredBaseURL = import.meta.env.VITE_API_BASE_URL || '/api'
  const configuredApiUrl = new URL(configuredBaseURL, `${annotationAppOrigin}/`)
  if (
    (configuredApiUrl.protocol !== 'http:' && configuredApiUrl.protocol !== 'https:')
    || configuredApiUrl.username
    || configuredApiUrl.password
  ) {
    throw new TypeError('annotation API base URL must use HTTP(S) without credentials')
  }
  const confinedApiUrl = new URL(`${annotationAppOrigin}/`)
  confinedApiUrl.pathname = configuredApiUrl.pathname
  confinedApiUrl.search = ''
  confinedApiUrl.hash = ''
  if (confinedApiUrl.origin !== annotationAppOrigin) {
    throw new TypeError('annotation API base URL must stay on the annotation app origin')
  }

  memoizedBaseURL = confinedApiUrl.toString().replace(/\/$/, '')
  return memoizedBaseURL
}

/** 清除 baseURL 缓存(测试用;运行时 origin 不会中途改变)。 */
export function resetApiBaseURLCache(): void {
  memoizedBaseURL = null
}

const http = axios.create({
  timeout: 10000
})

http.interceptors.request.use((config) => {
  // 每次请求现取:baseURL 首次求值后 memoize,token 则始终现读(支持轮换)。
  config.baseURL = resolveApiBaseURL()

  const token = getPortalContext().token
  if (token) {
    config.headers.set('X-Token', token)
  } else {
    config.headers.delete('X-Token')
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(error)
)

function isAbsoluteUrl(value: string) {
  return /^https?:\/\//i.test(value)
}

export function resolveApiUrl(path: string) {
  if (isAbsoluteUrl(path)) {
    return path
  }

  return new URL(path, `${getAnnotationAppOrigin()}/`).toString()
}

export default http
