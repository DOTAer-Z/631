import { isWujieEnv } from './platformRuntime.mjs'

export const APP_CLEANUP_KEY = Symbol('app-cleanup')

export function createCleanupRegistry() {
  const entries = new Map()
  let nextId = 0
  let retired = false

  function invoke(disposer) {
    try {
      disposer()
    } catch {
      // Cleanup is best-effort and must never expose captured application context.
    }
  }

  function drain() {
    const pending = [...entries.values()]
    entries.clear()
    for (const dispose of pending) invoke(dispose)
    return pending.length
  }

  return {
    register(disposer) {
      if (typeof disposer !== 'function') throw new TypeError('cleanup disposer must be a function')
      if (retired) {
        invoke(disposer)
        return () => false
      }
      nextId += 1
      const id = nextId
      entries.set(id, disposer)
      let active = true
      return () => {
        if (!active) return false
        active = false
        return entries.delete(id)
      }
    },
    runAll() {
      return retired ? 0 : drain()
    },
    retire() {
      if (retired) return 0
      retired = true
      return drain()
    },
  }
}

export function createAppRuntime(createApp) {
  if (typeof createApp !== 'function') throw new TypeError('createApp must be a function')
  let app = null
  let cleanup = null
  let phase = 'idle'

  return {
    mount() {
      if (phase === 'mounted') return app
      if (phase !== 'idle') return null

      phase = 'mounting'
      const nextCleanup = createCleanupRegistry()
      cleanup = nextCleanup
      let nextApp = null
      try {
        nextApp = createApp(nextCleanup)
        if (!nextApp || typeof nextApp.mount !== 'function' || typeof nextApp.unmount !== 'function') {
          throw new TypeError('application must provide mount and unmount')
        }
        nextApp.mount('#app')
        app = nextApp
        phase = 'mounted'
        return app
      } catch (error) {
        nextCleanup.retire()
        try {
          if (nextApp && typeof nextApp.unmount === 'function') nextApp.unmount()
        } catch {
          // Rollback failures must not replace or expose the original mount failure.
        }
        app = null
        cleanup = null
        phase = 'idle'
        throw error
      }
    },
    unmount() {
      if (phase !== 'mounted') return
      const currentApp = app
      const currentCleanup = cleanup
      app = null
      cleanup = null
      phase = 'unmounting'
      currentCleanup.retire()
      try {
        currentApp.unmount()
      } catch {
        // Teardown failures are isolated so a later mount starts from a fresh runtime.
      } finally {
        phase = 'idle'
      }
    },
    get cleanup() {
      return cleanup
    },
  }
}

export function installWujieLifecycle(windowRef, runtime) {
  if (!runtime || typeof runtime.mount !== 'function' || typeof runtime.unmount !== 'function') {
    throw new TypeError('runtime must provide mount and unmount')
  }
  if (isWujieEnv(windowRef)) {
    windowRef.__WUJIE_MOUNT = () => runtime.mount()
    windowRef.__WUJIE_UNMOUNT = () => runtime.unmount()
    return
  }
  runtime.mount()
}
