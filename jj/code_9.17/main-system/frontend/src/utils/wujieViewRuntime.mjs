import { isWujieEnv } from './platformRuntime.mjs'

export function getWujieViewMode(windowRef = globalThis.window) {
  const isWujie = isWujieEnv(windowRef)
  return Object.freeze({
    isWujie,
    showProjectHeader: !isWujie,
    mainHeight: isWujie ? '100vh' : 'calc(100vh - 60px)',
  })
}
