import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const apiPath = new URL('../src/api/logAnalysis.js', import.meta.url)

async function importWithRequestStub() {
  const source = await readFile(apiPath, 'utf8')
  const calls = []
  const request = {}
  for (const method of ['get', 'post', 'patch', 'delete']) {
    request[method] = (...args) => {
      const sentinel = { method, index: calls.length }
      calls.push({ method, args, sentinel })
      return sentinel
    }
  }
  globalThis.__logAnalysisRequestStub = request
  const moduleSource = source.replace(
    "import request from './index'",
    'const request = globalThis.__logAnalysisRequestStub',
  )
  const api = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(moduleSource)}`)
  return { api, calls }
}

test('log analysis API exposes parse task create, detail, and cancel contracts', async () => {
  const { api, calls } = await importWithRequestStub()
  const body = { run_ids: ['run-1'] }
  const invocations = [
    [() => api.createLogParseTask(body), 'post', ['/log-analysis/parse-tasks', body]],
    [() => api.getLogParseTask('task-1'), 'get', ['/log-analysis/parse-tasks/task-1']],
    [() => api.cancelLogParseTask('task-1'), 'post', ['/log-analysis/parse-tasks/task-1/cancel']],
  ]

  for (const [invoke, method, args] of invocations) {
    const result = invoke()
    const call = calls.at(-1)
    assert.equal(result, call.sentinel)
    assert.equal(call.method, method)
    assert.deepEqual(call.args, args)
  }
})
