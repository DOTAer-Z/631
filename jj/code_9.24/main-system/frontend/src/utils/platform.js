import {
  getAppOrigin,
  getAppUrl,
  getPlatformContext,
  isWujieEnv as detectWujieEnv,
} from './platformRuntime.mjs'

export { getAppOrigin, getAppUrl, getPlatformContext }

export function getWujieProps(windowRef = globalThis.window) {
  return getPlatformContext(windowRef)
}

export function getToken(windowRef = globalThis.window) {
  return getPlatformContext(windowRef).token
}

export function getUserInfo(windowRef = globalThis.window) {
  return getPlatformContext(windowRef).userInfo
}

export function getNamespaceId(windowRef = globalThis.window) {
  return getPlatformContext(windowRef).namespaceId
}

export function isWujieEnv(windowRef = globalThis.window) {
  return detectWujieEnv(windowRef)
}
