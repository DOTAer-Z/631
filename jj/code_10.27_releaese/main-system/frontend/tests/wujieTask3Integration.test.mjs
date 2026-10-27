import test from 'node:test'
import assert from 'node:assert/strict'

import {
  APP_CLEANUP_KEY,
  createAppRuntime,
} from '../src/utils/wujieLifecycle.mjs'

// 原先此处用 installAnnotationContextBridge 充当「需要被 cleanup 释放的资源」。
// 该 bridge 已随 iframe 时代移除(标注不再是 iframe 子应用),但本用例真正要验证的是
// wujieLifecycle 的 cleanup 契约——每个挂载周期注册的 disposer 都在 Vue unmount 前被调用、
// 且跨周期不复用。因此改用一个等价的假 disposer(监听器注册/注销 + 事件分发)保留该覆盖。
function installFakeListener({ windowRef, target, onEvent }) {
  const listener = (event) => {
    if (event.source !== target) return
    onEvent(event)
  }
  windowRef.addEventListener('message', listener)
  let disposed = false
  return () => {
    if (disposed) return
    disposed = true
    windowRef.removeEventListener('message', listener)
  }
}

test('runtime cleanup removes each cycle listener before Vue unmount and never reuses it', () => {
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
    const target = {}
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
    let disposeListener = null
    registries.push(cleanup)
    cycles.push({
      dispatch() {
        for (const listener of [...listeners]) listener({ source: target })
      },
      listeners,
      responses,
    })

    return {
      mount() {
        disposeListener = installFakeListener({
          windowRef,
          target,
          onEvent: () => responses.push(`response-${number}`),
        })
        cleanup.register(disposeListener)
      },
      unmount() {
        events.push(`vue-unmount-${number}`)
        disposeListener?.()
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
