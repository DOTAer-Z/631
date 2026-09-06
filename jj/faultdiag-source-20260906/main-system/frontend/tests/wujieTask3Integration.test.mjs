import test from 'node:test'
import assert from 'node:assert/strict'

import { installAnnotationContextBridge } from '../src/utils/annotationContextBridge.mjs'
import {
  APP_CLEANUP_KEY,
  createAppRuntime,
} from '../src/utils/wujieLifecycle.mjs'

const APP_ORIGIN = 'https://child.example:30443'
const CONTEXT = Object.freeze({ token: '', userInfo: null, namespaceId: null })

test('runtime cleanup removes each cycle bridge before Vue unmount and never reuses it', () => {
  assert.equal(typeof APP_CLEANUP_KEY, 'symbol')
  const events = []
  const cycles = []
  const registries = []
  let cycleNumber = 0

  const runtime = createAppRuntime(cleanup => {
    cycleNumber += 1
    const number = cycleNumber
    const listeners = new Set()
    const responses = []
    const frameWindow = {
      postMessage(message, targetOrigin) { responses.push({ message, targetOrigin }) },
    }
    const iframe = { contentWindow: frameWindow, isConnected: true }
    const windowRef = {
      addEventListener(type, listener) {
        assert.equal(type, 'message')
        events.push(`add-${number}`)
        listeners.add(listener)
      },
      removeEventListener(type, listener) {
        assert.equal(type, 'message')
        events.push(`remove-${number}`)
        listeners.delete(listener)
      },
    }
    let disposeBridge = null
    registries.push(cleanup)
    cycles.push({
      dispatch() {
        for (const listener of [...listeners]) listener({
          source: frameWindow,
          origin: APP_ORIGIN,
          data: {
            type: 'faultdiag:context:request',
            version: 2,
            requestId: `request-id-${String(number).padStart(6, '0')}`,
          },
        })
      },
      listeners,
      responses,
    })

    return {
      mount() {
        disposeBridge = installAnnotationContextBridge({
          windowRef,
          iframe,
          context: () => CONTEXT,
          appOrigin: APP_ORIGIN,
        })
        cleanup.register(disposeBridge)
      },
      unmount() {
        events.push(`vue-unmount-${number}`)
        disposeBridge?.()
      },
    }
  })

  runtime.mount()
  cycles[0].dispatch()
  assert.equal(cycles[0].responses.length, 1)
  runtime.unmount()
  runtime.unmount()
  cycles[0].dispatch()
  assert.equal(cycles[0].responses.length, 1)
  assert.equal(cycles[0].listeners.size, 0)
  assert.deepEqual(events, ['add-1', 'remove-1', 'vue-unmount-1'])

  runtime.mount()
  cycles[0].dispatch()
  cycles[1].dispatch()
  assert.equal(cycles[0].responses.length, 1)
  assert.equal(cycles[1].responses.length, 1)
  assert.notEqual(registries[0], registries[1])
  runtime.unmount()

  assert.deepEqual(events, [
    'add-1',
    'remove-1',
    'vue-unmount-1',
    'add-2',
    'remove-2',
    'vue-unmount-2',
  ])
})
