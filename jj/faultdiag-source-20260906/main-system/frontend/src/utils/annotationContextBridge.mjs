import { cloneFrozenPlatformContext } from './platformRuntime.mjs'

const REQUEST_TYPE = 'faultdiag:context:request'
const RESPONSE_TYPE = 'faultdiag:context:response'
const PROTOCOL_VERSION = 2
const REQUEST_ID_PATTERN = /^[A-Za-z0-9_-]{16,128}$/

function ownDataValue(object, key) {
  const descriptor = Object.getOwnPropertyDescriptor(object, key)
  return descriptor?.enumerable && 'value' in descriptor ? descriptor.value : undefined
}

function readRequestId(value) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null
  if (ownDataValue(value, 'type') !== REQUEST_TYPE) return null
  if (ownDataValue(value, 'version') !== PROTOCOL_VERSION) return null
  const requestId = ownDataValue(value, 'requestId')
  return typeof requestId === 'string' && REQUEST_ID_PATTERN.test(requestId) ? requestId : null
}

function isExactHttpOrigin(value) {
  if (typeof value !== 'string' || !value || value === '*') return false
  try {
    const url = new URL(value)
    return (
      (url.protocol === 'http:' || url.protocol === 'https:')
      && !url.username
      && !url.password
      && url.origin === value
    )
  } catch {
    return false
  }
}

export function installAnnotationContextBridge({ windowRef, iframe, context, appOrigin }) {
  if (
    !windowRef
    || typeof windowRef.addEventListener !== 'function'
    || typeof windowRef.removeEventListener !== 'function'
    || !iframe
    || typeof context !== 'function'
    || !isExactHttpOrigin(appOrigin)
  ) {
    throw new TypeError('invalid annotation context bridge configuration')
  }

  const listener = event => {
    try {
      const frameWindow = iframe.isConnected === true ? iframe.contentWindow : null
      if (!frameWindow || event.source !== frameWindow || event.origin !== appOrigin) return
      const requestId = readRequestId(event.data)
      if (!requestId) return

      const snapshot = cloneFrozenPlatformContext(context())
      if (!snapshot) return
      frameWindow.postMessage(Object.freeze({
        type: RESPONSE_TYPE,
        version: PROTOCOL_VERSION,
        requestId,
        token: snapshot.token,
        userInfo: snapshot.userInfo,
        namespaceId: snapshot.namespaceId,
      }), appOrigin)
    } catch {
      // Invalid messages and frames navigating during a response are ignored.
    }
  }

  let active = true
  windowRef.addEventListener('message', listener)
  return () => {
    if (!active) return
    active = false
    windowRef.removeEventListener('message', listener)
  }
}
