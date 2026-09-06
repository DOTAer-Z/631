import axios from 'axios'
import { ElMessage } from 'element-plus'
import { getAppUrl, getToken } from './platform'

const request = axios.create({
  baseURL: getAppUrl('/api/v1'),
  timeout: 120000,
})

const signalCleanups = new WeakMap()
let activeRequestScope = null

function removeTokenHeader(headers) {
  if (!headers) return
  if (typeof headers.delete === 'function') {
    headers.delete('X-Token')
    return
  }
  for (const name of Object.keys(headers)) {
    if (name.toLowerCase() === 'x-token') delete headers[name]
  }
}

function releaseSignalCleanup(config) {
  const cleanup = config && signalCleanups.get(config)
  if (!cleanup) return
  signalCleanups.delete(config)
  cleanup()
}

function combineSignals(lifecycleSignal, callerSignal) {
  if (!callerSignal || callerSignal === lifecycleSignal) {
    return { signal: lifecycleSignal, cleanup() {} }
  }

  if (typeof AbortSignal.any === 'function') {
    return {
      signal: AbortSignal.any([lifecycleSignal, callerSignal]),
      cleanup() {},
    }
  }

  const controller = new AbortController()
  const signals = [lifecycleSignal, callerSignal]
  let listening = true
  const cleanup = () => {
    if (!listening) return
    listening = false
    for (const signal of signals) signal.removeEventListener('abort', abort)
  }
  const abort = (event) => {
    cleanup()
    controller.abort(event?.target?.reason)
  }

  for (const signal of signals) {
    if (signal.aborted) {
      abort({ target: signal })
      break
    }
    signal.addEventListener('abort', abort, { once: true })
  }
  return { signal: controller.signal, cleanup }
}

export function installRequestScope(cleanup) {
  if (!cleanup || typeof cleanup.register !== 'function') {
    throw new TypeError('cleanup registry must provide register')
  }
  const controller = new AbortController()
  const scope = { active: true, signal: controller.signal }
  activeRequestScope = scope
  cleanup.register(() => {
    if (!scope.active) return
    scope.active = false
    if (activeRequestScope === scope) activeRequestScope = null
    controller.abort()
  })
  return Object.freeze({ signal: scope.signal })
}

request.interceptors.request.use(
  (config) => {
    releaseSignalCleanup(config)
    const scope = activeRequestScope
    if (!scope?.active) {
      throw new axios.CanceledError('request scope is inactive', config)
    }
    const token = getToken()
    if (typeof token === 'string' && token.length > 0) {
      config.headers ||= {}
      if (typeof config.headers.set === 'function') config.headers.set('X-Token', token)
      else config.headers['X-Token'] = token
    } else {
      removeTokenHeader(config.headers)
    }
    const combined = combineSignals(scope.signal, config.signal)
    config.signal = combined.signal
    signalCleanups.set(config, combined.cleanup)
    return config
  },
  (error) => Promise.reject(error)
)

request.interceptors.response.use(
  (res) => {
    releaseSignalCleanup(res?.config)
    return res.data
  },
  (err) => {
    releaseSignalCleanup(err?.config)
    if (axios.isCancel(err) || err?.code === 'ERR_CANCELED' || err?.name === 'AbortError') {
      return Promise.reject(err)
    }
    const msg = err.response?.data?.detail || err.message || '请求失败'
    ElMessage.error(msg)
    return Promise.reject(err)
  }
)

export default request
