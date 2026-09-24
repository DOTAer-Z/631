import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const apiPath = new URL('../src/api/modelTraining.js', import.meta.url)
const uploadPath = new URL('../src/views/ModelManagement/components/AdapterUploadDialog.vue', import.meta.url)
const evaluationPath = new URL('../src/views/ModelManagement/components/AdapterEvaluationDialog.vue', import.meta.url)
const trainingPath = new URL('../src/views/ModelManagement/Training.vue', import.meta.url)
const taskDialogPath = new URL('../src/views/ModelManagement/components/TrainingTaskDialog.vue', import.meta.url)

async function source(path) {
  return readFile(path, 'utf8')
}

async function importApiWithRequestStub() {
  const apiSource = await source(apiPath)
  const calls = []
  const request = {}
  for (const method of ['get', 'post', 'delete', 'getUri']) {
    request[method] = (...args) => {
      const result = { method, ordinal: calls.length }
      calls.push({ method, args, result })
      return result
    }
  }
  globalThis.__adapterRequestStub = request
  const moduleSource = apiSource.replace(
    "import request from './index'",
    'const request = globalThis.__adapterRequestStub',
  )
  const api = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(moduleSource)}`)
  return { api, calls }
}

test('Adapter API helpers preserve multipart progress, cancellation, and the dedicated timeout', async () => {
  const { api, calls } = await importApiWithRequestStub()
  const formData = new FormData()
  const controller = new AbortController()
  const onUploadProgress = () => {}

  const imported = api.importTrainingAdapter(formData, {
    signal: controller.signal,
    onUploadProgress,
  })
  const listed = api.listTrainingAdapters({ source: 'uploaded', adapter_stage: 'cpt' })
  const evaluated = api.evaluateTrainingAdapter('adapter-631', {
    name: 'external evaluation',
    test_version_ids: [7],
  })

  assert.equal(imported, calls[0].result)
  assert.equal(listed, calls[1].result)
  assert.equal(evaluated, calls[2].result)
  assert.deepEqual(calls[0], {
    method: 'post',
    args: [
      '/model-training/adapters/import',
      formData,
      { signal: controller.signal, onUploadProgress, timeout: 30 * 60 * 1000 },
    ],
    result: calls[0].result,
  })
  assert.deepEqual(calls[1].args, [
    '/model-training/adapters',
    { params: { source: 'uploaded', adapter_stage: 'cpt' } },
  ])
  assert.deepEqual(calls[2].args, [
    '/model-training/adapters/adapter-631/evaluate',
    { name: 'external evaluation', test_version_ids: [7] },
  ])
})

test('upload dialog owns one cancellable submission and suppresses stale outcomes', async () => {
  const upload = await source(uploadPath)

  assert.match(upload, /<el-upload/)
  assert.match(upload, /:auto-upload="false"/)
  assert.match(upload, /:limit="1"/)
  assert.match(upload, /<el-segmented[^>]+v-model="form\.adapter_stage"/)
  assert.match(upload, /Qwen\/Qwen3\.5-9B/)
  assert.match(upload, /new AbortController\(\)/)
  assert.match(upload, /if \(submitting\.value\) return/)
  assert.match(upload, /new FormData\(\)/)
  assert.match(upload, /formData\.append\('file'/)
  assert.match(upload, /importTrainingAdapter\(formData, \{ signal: controller\.signal, onUploadProgress/)
  assert.match(upload, /validateAdapterArchive/)
  assert.match(upload, /uploadProgressPercent/)
  assert.match(upload, /uploadGeneration\.isCurrent\(generation\)/)
  assert.match(upload, /isUploadCancellation\(error\)/)
  assert.match(upload, /activeController\?\.abort\(\)/)
  assert.match(upload, /uploadGeneration\.invalidate\(\)/)
  assert.match(upload, /onBeforeUnmount\(cancelUpload\)/)
})

test('evaluation dialog selects unique complete test versions and submits once', async () => {
  const evaluation = await source(evaluationPath)

  assert.match(evaluation, /listTrainingTests/)
  assert.match(evaluation, /completeness:\s*'complete'/)
  assert.match(evaluation, /row-key="version_id"/)
  assert.match(evaluation, /selectedIds = ref\(new Set\(\)\)/)
  assert.match(evaluation, /row\.completeness === 'complete'/)
  assert.match(evaluation, /form\.name\.trim\(\)/)
  assert.match(evaluation, /new Set\(selectedIds\.value\)/)
  assert.match(evaluation, /if \(submitting\.value\) return/)
  assert.match(evaluation, /evaluateTrainingAdapter\(props\.adapter\.id/)
  assert.match(evaluation, /submitRequests\.isLatest\(operationToken\)/)
  assert.match(evaluation, /emit\('created', task\)/)
  assert.match(evaluation, /invalidateDialogWork/)
  assert.match(evaluation, /onBeforeUnmount\(invalidateDialogWork\)/)
})

test('Adapter tab uses server filters and action flags with stale-safe refreshes', async () => {
  const training = await source(trainingPath)

  assert.match(training, /AdapterUploadDialog/)
  assert.match(training, /AdapterEvaluationDialog/)
  assert.match(training, /上传 Adapter/)
  assert.match(training, /adapterFilters\.source/)
  assert.match(training, /adapterFilters\.adapterStage/)
  assert.match(training, /adapterFilters\.search/)
  assert.match(training, /listTrainingAdapters\(\{[\s\S]*?source:[\s\S]*?adapter_stage:[\s\S]*?search:/)
  assert.match(training, /adapterRequests\.start\(\)/)
  assert.match(training, /adapterRequests\.isLatest\(requestToken\)/)
  assert.match(training, /adapterRefreshQueue\.catch\(\(\) => \{\}\)\.then/)
  assert.match(training, /row\.usable_for_sft/)
  assert.match(training, /row\.evaluable/)
  assert.match(training, /row\.deletable/)
  assert.match(training, /useAdapterForSft\(row\)/)
  assert.match(training, /evaluateAdapter\(row\)/)
  assert.match(training, /handleAdapterImported/)
  assert.match(training, /handleAdapterEvaluationCreated/)
  assert.match(training, /label="计算设备"/)
  assert.doesNotMatch(training, /label="GPU"/)
})

test('SFT source selector requests only usable CPT Adapters and keeps stable IDs', async () => {
  const dialog = await source(taskDialogPath)

  assert.match(dialog, /listTrainingAdapters/)
  assert.match(dialog, /adapter_stage:\s*'cpt'/)
  assert.match(dialog, /item\.adapter_stage === 'cpt'/)
  assert.match(dialog, /item\.usable_for_sft/)
  assert.match(dialog, /v-for="item in adapters" :key="item\.id"/)
  assert.match(dialog, /:value="item\.id"/)
  assert.match(dialog, /initialCptAdapterId/)
  assert.doesNotMatch(dialog, /listTrainingArtifacts\(\{ artifact_type: 'final_adapter'/)
})
