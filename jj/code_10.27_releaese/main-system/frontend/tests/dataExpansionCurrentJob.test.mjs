import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { FAULT_LABELS, EXECUTED_FAULT_LABELS } from '../src/utils/localFaultFile.mjs'
import { automaticGenerationSelection } from '../src/utils/eulerGeneration.mjs'
import { isActiveJob, requestedFaultType, executedFaultType } from '../src/utils/eulerJobState.mjs'

const source = readFileSync(new URL('../src/views/KeyTechnologyDemo/DataExpansion.vue', import.meta.url), 'utf8')
  .split('<script setup>')[1].split('</script>')[0].replace(/import\s+[\s\S]*?\s+from\s+['"][^'"]+['"]/g, '')
const record = (id, code, status = 'running', created = '2026-10-21T02:00:00Z') => ({
  job_id: id, status, created_at: created,
  job: { fault_type: code, execution_mode: 'baseline', sample_count: 15 },
  summary: { sample_total: 15, sample_succeeded: status === 'succeeded' ? 15 : 0, sample_failed: 0 },
  ...(status === 'succeeded' ? { artifact_path: `${id}.tar.gz` } : {}),
})
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }

function harness({ stored = {}, storageFails = false } = {}) {
  const calls = { create: [], detail: [], save: [], download: [], errors: [] }
  const records = new Map(), imports = new Map()
  let listed = [], failList = false, submitted, requestDetail, mounted, tick
  const context = vm.createContext({
    ref: value => ({ value }), computed: callback => ({ get value() { return callback() } }),
    onMounted: fn => { mounted = fn }, onUnmounted() {}, useRouter: () => ({ push() {} }),
    FAULT_LABELS, EXECUTED_FAULT_LABELS, automaticGenerationSelection, isActiveJob, requestedFaultType, executedFaultType,
    localStorage: {
      getItem: () => JSON.stringify(stored),
      setItem() { if (storageFails) throw new Error('storage disabled') },
    },
    setInterval: fn => { tick = fn; return 1 }, clearInterval() {},
    ElMessage: { success() {}, warning() {}, error: message => calls.errors.push(message) },
    ElMessageBox: { confirm: async () => true },
    createVirtualPrototypeJob: async payload => { calls.create.push(payload); return submitted },
    listVirtualPrototypeJobs: async () => { if (failList) throw new Error('list unavailable'); return { jobs: listed } },
    getVirtualPrototypeJob: async id => {
      calls.detail.push(id)
      if (requestDetail) return requestDetail(id)
      if (!records.has(id)) throw new Error('not yet visible')
      return records.get(id)
    },
    getVirtualPrototypeEvents: async id => ({ events: [{ message: id }] }),
    listVirtualPrototypeImports: async () => ({ imports: [...imports.values()] }),
    saveVirtualPrototypeArtifact: async id => {
      calls.save.push(id)
      const result = { job_id: id, state: 'success' }; imports.set(id, result); return result
    },
    downloadVirtualPrototypeArtifact: async id => { calls.download.push(id); return new Blob([id]) },
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
    document: { createElement: () => ({ click() {} }) }, Blob,
    getVirtualPrototypeHealth: async () => ({}), getVirtualPrototypeCapabilities: async () => ({}),
    getExpansionAnalysis: async () => ({}),
  })
  vm.runInContext(`${source}\nglobalThis.page = { online, capabilities, alternatives, jobs, submittedJobs, latestSubmitted, rows, selected, drawer, events, guestBusy, canSave, saveLabel, submitGeneration, refreshJobs, refreshDrawer, view, save, download };`, context)
  const page = context.page
  page.online.value = true
  page.capabilities.value = Object.fromEntries(Object.keys(FAULT_LABELS).map(code => [code, [{ status: 'supported' }]]))
  return {
    page, calls, records, imports, row: code => page.rows.value.find(row => row.code === code),
    setList: value => { listed = value }, failList: () => { failList = true },
    submit: value => { submitted = value }, detail: fn => { requestDetail = fn },
    mount: () => mounted(), tick: () => tick(),
  }
}

test('重新生成三类实际故障时，查看、下载、保存都绑定新 job，旧已保存状态不串用', async () => {
  for (const code of ['F2301', 'F5203', 'F5402']) {
    const h = harness()
    const old = record('job-old', code, 'succeeded')
    h.imports.set(old.job_id, { job_id: old.job_id, state: 'success' })
    h.setList([old])
    await h.page.refreshJobs()
    const current = record('job-new', code)
    h.submit(current); h.records.set(current.job_id, current)
    await h.page.submitGeneration(h.row(code))
    assert.equal(h.row(code).latest, current.job_id)
    assert.equal(h.row(code).running, current.job_id)
    assert.equal(h.row(code).completed, undefined)
    assert.equal(h.page.guestBusy.value, true)
    assert.equal(h.calls.save.length, 0, '生成不能自动入库')
    await h.page.view(h.row(code).latest)
    assert.equal(h.page.selected.value.job_id, current.job_id)
    h.records.set(current.job_id, record(current.job_id, code, 'succeeded'))
    await h.page.refreshJobs(); await h.page.refreshDrawer()
    assert.equal(h.page.selected.value.status, 'succeeded')
    assert.equal(h.row(code).completed, current.job_id)
    assert.equal(h.page.canSave(h.row(code).completed), true)
    await h.page.download(h.row(code).completed)
    assert.deepEqual(h.calls.download, [current.job_id])
    assert.equal(h.calls.save.length, 0, '下载不能自动入库')
    await h.page.save(h.row(code).completed)
    assert.deepEqual(h.calls.save, [current.job_id])
    assert.equal(h.page.saveLabel(current.job_id), '已保存')
    assert.equal(h.page.canSave(current.job_id), false)
    await h.page.save(current.job_id)
    assert.deepEqual(h.calls.save, [current.job_id])
  }
})

test('页面重载后列表缺少当前任务或请求失败时，按记忆 ID 更新且不会退回旧归档', async () => {
  const h = harness({ stored: { F4512: 'job-current' } })
  h.setList([record('job-history', 'F4512', 'succeeded')])
  await h.page.refreshJobs()
  assert.equal(h.row('F4512').latest, 'job-current')
  assert.equal(h.row('F4512').completed, undefined)
  h.failList()
  h.records.set('job-current', record('job-current', 'F4512', 'succeeded'))
  await h.page.refreshJobs()
  assert.equal(h.row('F4512').completed, 'job-current')
  assert.equal(h.page.canSave('job-current'), true)
})

test('新任务运行、归档失败或14/15部分失败时不使用旧成功产物', async () => {
  for (const status of ['queued', 'preparing', 'running', 'finalizing', 'failed', 'partial_failed', 'cancelled']) {
    const h = harness({ stored: { F2301: 'job-current' } })
    const current = { ...record('job-current', 'F2301', status), artifact_path: 'partial.tar.gz', summary: { sample_total: 15, sample_succeeded: 14, sample_failed: 1 } }
    h.setList([record('job-history', 'F2301', 'succeeded')]); h.records.set(current.job_id, current)
    await h.page.refreshJobs()
    assert.equal(h.row('F2301').latest, current.job_id, status)
    assert.equal(h.row('F2301').completed, undefined, status)
    assert.equal(h.page.canSave(current.job_id), false, status)
    await h.page.save(current.job_id)
    assert.deepEqual(h.calls.save, [])
  }
})

test('无本地记忆时按创建时间选择最新任务，不回退到最近成功任务', async () => {
  const h = harness()
  h.setList([record('job-z-old', 'F4512', 'succeeded', '2026-10-20T00:00:00Z'), record('job-a-new', 'F4512', 'failed')])
  await h.page.refreshJobs()
  assert.equal(h.row('F4512').latest, 'job-a-new')
  assert.equal(h.row('F4512').completed, undefined)
})

test('禁止localStorage写入时，仍记住已提交任务并保留列表外的提交回执', async () => {
  const h = harness({ storageFails: true })
  h.submit({ job_id: 'job-new' })
  await h.page.submitGeneration(h.row('F4512'))
  assert.equal(h.row('F4512').latest, 'job-new')
  assert.equal(h.page.guestBusy.value, true)
  assert.equal(h.calls.create.length, 1)
  assert.deepEqual(h.calls.errors, [])
})

test('切换查看任务时清空旧详情，迟到响应不能覆盖新任务；慢请求不会被轮询重复发出', async () => {
  const h = harness(), old = deferred(), current = deferred()
  h.detail(id => id === 'job-old' ? old.promise : current.promise)
  const viewingOld = h.page.view('job-old')
  const viewingNew = h.page.view('job-new')
  assert.equal(h.page.selected.value, null)
  assert.equal(await h.page.refreshDrawer(), false)
  assert.deepEqual(h.calls.detail, ['job-old', 'job-new'])
  current.resolve(record('job-new', 'F4512')); await viewingNew
  old.resolve(record('job-old', 'F4512', 'succeeded')); await viewingOld
  assert.equal(h.page.selected.value.job_id, 'job-new')
  assert.equal(h.page.events.value[0].message, 'job-new')
})
