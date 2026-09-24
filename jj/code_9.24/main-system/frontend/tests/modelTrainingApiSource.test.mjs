import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const apiPath = new URL('../src/api/modelTraining.js', import.meta.url)

async function importWithRequestStub() {
  const source = await readFile(apiPath, 'utf8')
  const calls = []
  const request = {}

  for (const method of ['get', 'post', 'delete', 'getUri']) {
    request[method] = (...args) => {
      const sentinel = { method, call: calls.length }
      calls.push({ method, args, sentinel })
      return sentinel
    }
  }
  globalThis.__modelTrainingRequestStub = request
  const moduleSource = source.replace(
    "import request from './index'",
    'const request = globalThis.__modelTrainingRequestStub',
  )
  const api = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(moduleSource)}`)
  return { api, calls }
}

test('model training API helpers execute every frozen backend request contract', async () => {
  const { api, calls } = await importWithRequestStub()
  const listTestsParams = { page: 2 }
  const splitBody = { test_version_ids: [12] }
  const taskBody = { name: 'CPT smoke' }
  const listTasksParams = { state: 'training' }
  const retryBody = { resume_checkpoint_artifact_id: 'checkpoint-631' }
  const artifactParams = { kind: 'checkpoint' }
  const invocations = [
    [() => api.listTrainingTests(listTestsParams), 'get', ['/model-training/tests', { params: listTestsParams }]],
    [() => api.previewTrainingSplit(splitBody), 'post', ['/model-training/splits/preview', splitBody]],
    [() => api.createTrainingTask(taskBody), 'post', ['/model-training/tasks', taskBody]],
    [() => api.listTrainingTasks(listTasksParams), 'get', ['/model-training/tasks', { params: listTasksParams }]],
    [() => api.getTrainingTask('task-631'), 'get', ['/model-training/tasks/task-631']],
    [() => api.cancelTrainingTask('task-631'), 'post', ['/model-training/tasks/task-631/cancel']],
    [() => api.retryTrainingTask('task-631'), 'post', ['/model-training/tasks/task-631/retry']],
    [() => api.retryTrainingTask('task-631', retryBody), 'post', ['/model-training/tasks/task-631/retry', retryBody]],
    [() => api.evaluateTrainingTask('task-631'), 'post', ['/model-training/tasks/task-631/evaluate']],
    [() => api.getTrainingTaskLogs('task-631', 27), 'get', ['/model-training/tasks/task-631/logs', { params: { after_line: 27 } }]],
    [() => api.deleteTrainingTask('task-631'), 'delete', ['/model-training/tasks/task-631']],
    [() => api.listTrainingArtifacts(artifactParams), 'get', ['/model-training/artifacts', { params: artifactParams }]],
    [() => api.downloadTrainingArtifactUrl('artifact-631'), 'getUri', [{ url: '/model-training/artifacts/artifact-631/download' }]],
    [() => api.deleteTrainingArtifact('artifact-631'), 'delete', ['/model-training/artifacts/artifact-631']],
    [() => api.getTrainingWorker(), 'get', ['/model-training/worker']],
  ]

  for (const [invoke, method, args] of invocations) {
    const result = invoke()
    const call = calls.at(-1)
    assert.equal(result, call.sentinel)
    assert.equal(call.method, method)
    assert.deepEqual(call.args, args)
  }
})

test('model training API accepts identifiers and cursors but no raw filesystem paths', async () => {
  const source = await readFile(apiPath, 'utf8')

  assert.doesNotMatch(source, /\b(?:filePath|rawPath|relativePath|absolutePath|artifactPath|downloadPath)\b/)
})
