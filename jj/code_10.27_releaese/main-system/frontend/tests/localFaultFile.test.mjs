import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import test from 'node:test'
import { FAULT_LABELS, EXECUTED_FAULT_LABELS, FAULT_FALLBACKS, FILE_PREVIEW_BYTES, readLocalFaultFile } from '../src/utils/localFaultFile.mjs'
import { automaticGenerationSelection } from '../src/utils/eulerGeneration.mjs'
import { isActiveJob, requestedFaultType, executedFaultType } from '../src/utils/eulerJobState.mjs'

const file = (content, name = 'fault.data') => Object.assign(new Blob([content]), { name })

test('十类新故障编码和完整中文名称均能匹配，不依赖文件扩展名', async () => {
  for (const [code, label] of Object.entries(FAULT_LABELS)) {
    for (const content of [JSON.stringify({ fault_type: code }), label, label.replace('(', '（').replace(')', '）')]) {
      const result = await readLocalFaultFile(file(content, 'sample.any-extension'), code)
      assert.equal(result.status, 'matched')
      assert.deepEqual(result.codes, [code])
      assert.ok(result.message.includes(label))
    }
  }
})

test('类型不符、编码与名称冲突、多类型、仅文件名包含类型时拒绝匹配', async () => {
  assert.equal((await readLocalFaultFile(file(FAULT_LABELS.F4620), 'F4512')).status, 'mismatch')
  assert.equal((await readLocalFaultFile(file(`F4512 ${FAULT_LABELS.F4620}`), 'F4512')).status, 'ambiguous')
  assert.equal((await readLocalFaultFile(file('F4512 F4620'), 'F4512')).status, 'ambiguous')
  assert.equal((await readLocalFaultFile(file('F9999'), 'F4512')).status, 'mismatch')
  assert.equal((await readLocalFaultFile(file('cpu_usage=98', `${FAULT_LABELS.F4512}-F4512.log`), 'F4512')).status, 'unknown')
})

test('二进制和不完整的大文件无法触发生成，脚本内容仅作为文本读取', async () => {
  assert.equal((await readLocalFaultFile(file(new Uint8Array([0, 1, 2, 70, 52, 53, 49, 50])), 'F4512')).status, 'binary')
  assert.equal((await readLocalFaultFile(file(`F4512${' '.repeat(FILE_PREVIEW_BYTES)}F4620`), 'F4512')).status, 'partial')
  const result = await readLocalFaultFile(file('F4512\nglobalThis.__faultFileExecuted = true', 'fault.js'), 'F4512')
  assert.equal(result.status, 'matched')
  assert.equal(globalThis.__faultFileExecuted, undefined)
})

function pageHarness() {
  const source = readFileSync(new URL('../src/views/KeyTechnologyDemo/DataExpansion.vue', import.meta.url), 'utf8')
    .split('<script setup>')[1].split('</script>')[0].replace(/import\s+[\s\S]*?\s+from\s+['"][^'"]+['"]/g, '')
  const requests = [], errors = []
  const context = vm.createContext({
    ref: value => ({ value }), computed: callback => ({ get value() { return callback() } }),
    onMounted() {}, onUnmounted() {}, useRouter: () => ({}), FAULT_LABELS, EXECUTED_FAULT_LABELS, readLocalFaultFile, automaticGenerationSelection,
    isActiveJob, requestedFaultType, executedFaultType,
    ElMessageBox: { confirm: async () => true },
    ElMessage: { success() {}, warning() {}, error: message => errors.push(message) },
    createVirtualPrototypeJob: async request => { requests.push(request); return { job_id: 'job-test' } },
    listVirtualPrototypeJobs: async () => ({ jobs: [] }),
  })
  vm.runInContext(`${source}\nglobalThis.page = { online, capabilities, alternatives, jobs, localFileInput, localFileFault, localFileJob, localFileError, onLocalFileChange, onGenerationModeChange, generate };`, context)
  const page = context.page
  page.online.value = true
  page.capabilities.value = { F4512: [{ status: 'supported' }] }
  page.localFileFault.value = { code: 'F4512', label: FAULT_LABELS.F4512, row: { code: 'F4512', mode: 'local_file' } }
  return { page, requests, errors }
}

test('选择读取文件模式立即打开选择框，离线或忙碌时仍可重新选择文件', async () => {
  const { page, requests } = pageHarness()
  let opened = 0
  page.localFileInput.value = { value: 'previous-file', click() { opened++ } }
  page.online.value = false
  page.jobs.value = [{ status: 'running' }]
  const row = { code: 'F4512', label: FAULT_LABELS.F4512, mode: 'native' }
  // Select emits the new value before Vue updates the computed table row.
  page.onGenerationModeChange(row, 'local_file')
  assert.equal(opened, 1)
  assert.equal(page.localFileFault.value.row.mode, 'local_file')
  assert.equal(page.localFileInput.value.value, '')
  await page.generate({ ...row, mode: 'local_file' })
  assert.equal(opened, 2)
  page.onGenerationModeChange(row, 'native')
  assert.equal(opened, 2)
  assert.equal(requests.length, 0)
})

test('实际页面文件处理只在匹配时提交一个受控 15 样本任务', async () => {
  for (const content of [FAULT_LABELS.F4512, 'F4512']) {
    const { page, requests, errors } = pageHarness()
    await page.onLocalFileChange({ target: { files: [file(content)] } })
    assert.equal(requests.length, 1)
    assert.deepEqual(JSON.parse(JSON.stringify(requests[0])), {
      fault_type: 'F4512', task_kind: 'augmentation', sample_class: 'fault',
      sample_count: 15, seed: 20261008,
    })
    assert.equal(page.localFileJob.value, 'job-test')
    assert.deepEqual(errors, [])
  }
})

test('F1206 按新版调度名称匹配，旧网卡名称和旧语义代码组合不匹配', async () => {
  assert.equal((await readLocalFaultFile(file('硬件功能/数据完整性异常(网卡帧错误速率超限)'), 'F1206')).status, 'unknown')
  assert.equal((await readLocalFaultFile(file('F1206 网卡帧错误速率超限'), 'F1206')).status, 'mismatch')
  assert.equal((await readLocalFaultFile(file(FAULT_LABELS.F1206), 'F1206')).status, 'matched')
})

test('替代 JSON 必须声明固定的请求/执行身份，额外类型及错误映射被拒绝', async () => {
  for (const [requested, executed] of Object.entries(FAULT_FALLBACKS)) {
    const content = JSON.stringify({ requested_fault_type: requested, executed_fault_type: executed, fault_type: executed })
    const result = await readLocalFaultFile(file(content), executed)
    assert.equal(result.status, 'matched')
    assert.equal(result.executedFaultType, executed)
    assert.equal(result.requestedFaultType, requested)
    assert.equal((await readLocalFaultFile(file(content), requested)).status, 'mismatch')
    assert.equal((await readLocalFaultFile(file(requested), executed)).status, 'mismatch')
    assert.equal((await readLocalFaultFile(file(`${requested} ${executed}`), requested)).status, 'ambiguous')
    assert.equal((await readLocalFaultFile(file(JSON.stringify({ requested_fault_type: requested, executed_fault_type: executed, fault_type: 'F4512' })), requested)).status, 'ambiguous')
  }
  assert.equal((await readLocalFaultFile(file(JSON.stringify({ requested_fault_type: 'F2305', executed_fault_type: 'F5402' })), 'F2305')).status, 'ambiguous')
})

test('读取历史替代文件按声明的实际故障生成，不再触发模拟策略', async () => {
  const { page, requests } = pageHarness()
  page.localFileFault.value = { code: 'F2301', label: FAULT_LABELS.F2301, row: { code: 'F2301', mode: 'local_file' } }
  page.capabilities.value = { F2301: [{ name: 'python3', status: 'supported' }, { name: 'writable_cpu_cgroup', status: 'supported' }] }
  const content = JSON.stringify({ requested_fault_type: 'F2305', executed_fault_type: 'F2301', fault_type: 'F2301' })
  await page.onLocalFileChange({ target: { files: [file(content)] } })
  assert.equal(requests.length, 1)
  assert.equal(requests[0].fault_type, 'F2301')
  assert.equal('auto_select_execution' in requests[0], false)
  assert.equal('expected_executed_fault_type' in requests[0], false)
  assert.equal('options' in requests[0], false)
  assert.equal('duration_seconds' in requests[0], false)
})

test('页面只保留两种生成方式，不展示原始/替代和策略下拉框', () => {
  const source = readFileSync(new URL('../src/views/KeyTechnologyDemo/DataExpansion.vue', import.meta.url), 'utf8')
  const template = source.split('<script setup>')[0]
  assert.ok(template.includes('label="注入生成"'))
  assert.ok(template.includes('label="算法生成"'))
  assert.ok(!template.includes('executionTargets'))
  assert.ok(!template.includes('capabilityPolicies'))
  assert.ok(!template.includes('value="original"'))
})

test('实际页面类型错误、离线、能力不足或已有任务时不提交', async () => {
  for (const reason of ['mismatch', 'unknown', 'offline', 'unsupported', 'busy']) {
    const { page, requests, errors } = pageHarness()
    if (reason === 'offline') page.online.value = false
    if (reason === 'unsupported') page.capabilities.value = { F4512: [{ status: 'unsupported' }] }
    if (reason === 'busy') page.jobs.value = [{ status: 'running' }]
    const content = reason === 'mismatch' ? FAULT_LABELS.F4620 : reason === 'unknown' ? 'cpu_usage=95' : 'F4512'
    await page.onLocalFileChange({ target: { files: [file(content)] } })
    assert.equal(requests.length, 0, reason)
    assert.ok(errors.length > 0, reason)
    assert.ok(page.localFileError.value, reason)
  }
})
