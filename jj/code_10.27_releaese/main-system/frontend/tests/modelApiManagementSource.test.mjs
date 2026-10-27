import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const apiPath = new URL('../src/api/modelApiConfig.js', import.meta.url)

test('model API client uses the agreed backend endpoints', async () => {
  const source = await readFile(apiPath, 'utf8')

  assert.match(source, /request\.get\(['"]\/model-api\/configs['"]\)/)
  assert.match(source, /request\.post\(['"]\/model-api\/configs['"],\s*data\)/)
  assert.match(source, /request\.patch\(`\/model-api\/configs\/\$\{id\}`,\s*data\)/)
  assert.match(source, /request\.delete\(`\/model-api\/configs\/\$\{id\}`\)/)
  assert.match(source, /request\.post\(`\/model-api\/configs\/\$\{id\}\/activate`\)/)
  assert.match(source, /request\.post\(`\/model-api\/configs\/\$\{id\}\/test`\)/)
})

const routerPath = new URL('../src/router/index.js', import.meta.url)
const trainingPath = new URL('../src/views/ModelManagement/Training.vue', import.meta.url)

test('model management exposes API and training child routes', async () => {
  const source = await readFile(routerPath, 'utf8')

  assert.match(source, /path:\s*['"]model-management['"]/)
  assert.match(source, /redirect:\s*['"]\/model-management\/api['"]/)

  const childrenSource = source.match(
    /path:\s*['"]model-management['"][\s\S]*?children:\s*\[([\s\S]*?)\n\s*\],/
  )?.[1]
  assert.ok(childrenSource, 'model-management route must declare a children array')

  const childEntries = childrenSource.match(/\{[\s\S]*?\n\s*\},?/g) || []
  const visibleChildPaths = childEntries
    .filter((entry) => !/meta:\s*\{[^}]*hidden:\s*true/.test(entry))
    .map((entry) => entry.match(/path:\s*['"]([^'"]+)['"]/)?.[1])
    .filter(Boolean)

  assert.deepEqual(visibleChildPaths, ['api', 'training'])
  assert.match(source, /path:\s*['"]fine-tuning['"],\s*redirect:\s*['"]\/model-management\/training['"]/)
})

test('model training exposes the operational training surface without placeholder copy', async () => {
  const trainingSource = await readFile(trainingPath, 'utf8')

  assert.match(trainingSource, /<h2>大模型微调<\/h2>/)
  assert.match(trainingSource, /label="训练任务"[^>]+name="tasks"/)
  assert.match(trainingSource, /label="Adapter"[^>]+name="adapters"/)
  assert.match(trainingSource, /label="Worker"[^>]+name="worker"/)
  assert.match(trainingSource, /<TrainingTaskDialog/)
  assert.match(trainingSource, /<TrainingTaskDrawer/)
  assert.doesNotMatch(trainingSource, /该功能正在开发中|提交到 GPU 集群|部署为推理服务/)
})

const dialogPath = new URL('../src/views/ModelManagement/components/ApiConfigDialog.vue', import.meta.url)
const pagePath = new URL('../src/views/ModelManagement/ApiManagement.vue', import.meta.url)
const layoutPath = new URL('../src/components/Layout/AppLayout.vue', import.meta.url)

async function importPageStateHelpers() {
  const source = await readFile(pagePath, 'utf8')
  const moduleScript = source.match(/<script>\s*([\s\S]*?)<\/script>/)?.[1]

  assert.ok(moduleScript, 'API management page must expose dependency-free state helpers')
  return import(`data:text/javascript;charset=utf-8,${encodeURIComponent(moduleScript)}`)
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

test('API configuration dialog keeps secret and validation rules local', async () => {
  const source = await readFile(dialogPath, 'utf8')

  assert.match(source, /type="password"/)
  assert.match(source, /autocomplete="new-password"/)
  assert.match(source, /validateApiConfigForm/)
  assert.match(source, /buildApiConfigPayload/)
  assert.match(source, /emit\(['"]submit['"]/)
  assert.doesNotMatch(source, /localStorage|sessionStorage/)
})

test('API configuration dialog blocks framework close requests while submitting', async () => {
  const source = await readFile(dialogPath, 'utf8')

  assert.match(source, /:before-close="beforeClose"/)
  assert.match(source, /function\s+beforeClose\(done\)[\s\S]*?if\s*\(props\.submitting\)\s*return[\s\S]*?emit\(['"]update:modelValue['"],\s*false\)[\s\S]*?done\(\)/)
  assert.match(source, /<el-button\s+@click="close">取消<\/el-button>/)
  assert.doesNotMatch(source, /@close="close"/)
})

test('API configuration dialog clears every form field when hidden or closed', async () => {
  const source = await readFile(dialogPath, 'utf8')

  assert.match(source, /@closed="resetForm"/)
  assert.match(source, /function\s+resetForm\(\)\s*{\s*replaceForm\(createEmptyApiConfigForm\(\)\)\s*}/)
  assert.match(source, /if\s*\(visible\)[\s\S]*?else\s+resetForm\(\)/)
})

test('API management page wires every required workflow', async () => {
  const source = await readFile(pagePath, 'utf8')

  assert.match(source, /listModelApiConfigs/)
  assert.match(source, /createModelApiConfig/)
  assert.match(source, /updateModelApiConfig/)
  assert.match(source, /deleteModelApiConfig/)
  assert.match(source, /activateModelApiConfig/)
  assert.match(source, /testModelApiConfig/)
  assert.match(source, /const\s+activeConfig\s*=\s*computed/)
  assert.match(source, /ElMessageBox\.confirm/)
  assert.match(source, /<ApiConfigDialog/)
  assert.match(source, /<el-empty/)
  assert.doesNotMatch(source, /localStorage|sessionStorage/)
})

test('API management page exposes safe operation and failure states', async () => {
  const source = await readFile(pagePath, 'utf8')

  assert.match(source, /v-loading="loading"/)
  assert.match(source, /v-if="loadError"/)
  assert.match(source, /v-else-if="!loading\s*&&\s*!loadError"/)
  assert.match(source, /v-if="!row\.is_active"[\s\S]*?@click="removeConfig\(row\)"/)
  assert.match(source, /if\s*\(config\.is_active\)\s*return/)
  assert.match(source, /operationError/)
  assert.doesNotMatch(source, /localStorage|sessionStorage|console\.(?:log|info|debug|warn|error)/)
  assert.doesNotMatch(source, /\.api_key(?!_configured)/)
})

test('exclusive operation token releases after resolved work', async () => {
  const { createExclusiveOperationState } = await importPageStateHelpers()
  const state = createExclusiveOperationState()
  const token = state.start()

  assert.equal(typeof token, 'number', 'operation token must use primitive identity')
  try {
    await Promise.resolve('complete')
  } finally {
    state.finish(token)
  }

  assert.equal(state.isActive(), false)
  assert.equal(typeof state.start(), 'number')
})

test('exclusive operation token releases after rejected work', async () => {
  const { createExclusiveOperationState } = await importPageStateHelpers()
  const state = createExclusiveOperationState()
  const token = state.start()

  try {
    await Promise.reject(new Error('request failed'))
  } catch {
    // The operation owner handles the request error before releasing its token.
  } finally {
    state.finish(token)
  }

  assert.equal(state.isActive(), false)
  assert.equal(typeof state.start(), 'number')
})

test('latest request state rejects a stale response that resolves last', async () => {
  const { createLatestRequestState } = await importPageStateHelpers()
  const state = createLatestRequestState()
  const older = deferred()
  const newer = deferred()
  const writes = []

  async function load(request) {
    const requestId = state.start()
    const value = await request.promise
    if (state.isLatest(requestId)) writes.push(value)
  }

  const olderLoad = load(older)
  const newerLoad = load(newer)
  newer.resolve('newer')
  await newerLoad
  older.resolve('older')
  await olderLoad

  assert.deepEqual(writes, ['newer'])
})

test('API management page uses stable row tokens and latest-request guards', async () => {
  const source = await readFile(pagePath, 'utf8')
  const disabledActions = source.match(/:disabled="rowOperationActive"/g) || []
  const protectedFinalizers = source.match(/^\s{4}finishRowOperation\(operation\)$/gm) || []

  assert.match(source, /const\s+rowOperation\s*=\s*shallowRef\(null\)/)
  assert.match(source, /createExclusiveOperationState\(\)/)
  assert.match(source, /const\s+rowOperationActive\s*=\s*computed\(\(\)\s*=>\s*Boolean\(rowOperation\.value\)\)/)
  assert.match(source, /createLatestRequestState\(\)/)
  assert.match(source, /if\s*\(!configRequests\.isLatest\(requestId\)\)\s*return/g)
  assert.match(source, /if\s*\(configRequests\.isLatest\(requestId\)\)\s*loading\.value\s*=\s*false/)
  assert.match(source, /startRowOperation\(['"]test['"],\s*config\.id\)/)
  assert.match(source, /startRowOperation\(['"]activate['"],\s*config\.id\)/)
  assert.match(source, /startRowOperation\(['"]delete['"],\s*config\.id\)/)
  assert.match(source, /:loading="isRowOperation\('test',\s*row\.id\)"/)
  assert.match(source, /:loading="isRowOperation\('activate',\s*row\.id\)"/)
  assert.match(source, /:loading="isRowOperation\('delete',\s*row\.id\)"/)
  assert.ok(disabledActions.length >= 5, 'every row action must be disabled during an operation')
  assert.equal(protectedFinalizers.length, 3, 'every asynchronous row operation must release its own token')
})

test('API management prevents refresh and create during conflicting work', async () => {
  const source = await readFile(pagePath, 'utf8')

  assert.match(source, /:disabled="conflictingWork"[^>]*@click="loadConfigs"/)
  assert.match(source, /:disabled="loading\s*\|\|\s*conflictingWork"[^>]*@click="openCreate"/)
  assert.match(source, /if\s*\(loading\.value\s*\|\|\s*conflictingWork\.value\)\s*return/)
})

test('API management and app shell can shrink at 390px', async () => {
  const [pageSource, layoutSource] = await Promise.all([
    readFile(pagePath, 'utf8'),
    readFile(layoutPath, 'utf8'),
  ])

  assert.match(pageSource, /@media\s*\(max-width:\s*480px\)[\s\S]*?\.toolbar-actions\s*{[\s\S]*?flex-direction:\s*column/)
  assert.match(pageSource, /@media\s*\(max-width:\s*480px\)[\s\S]*?\.toolbar-actions\s+\.el-button\s*{[\s\S]*?width:\s*100%[\s\S]*?margin-left:\s*0/)
  assert.match(layoutSource, /\.app-layout\s*>\s*\.el-container\s*{\s*min-width:\s*0;/)
  assert.match(layoutSource, /\.main\s*{[\s\S]*?min-width:\s*0;/)
})

test('API management page preserves zero-millisecond latency', async () => {
  const source = await readFile(pagePath, 'utf8')

  assert.match(source, /latency\s*===\s*null\s*\|\|\s*latency\s*===\s*undefined/)
  assert.match(source, /`\$\{latency\}\s*ms`/)
  assert.doesNotMatch(source, /latency_ms\s*\?\s*`/)
})
