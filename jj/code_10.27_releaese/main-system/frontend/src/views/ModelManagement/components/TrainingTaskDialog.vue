<template>
  <el-dialog v-model="visible" title="新建训练任务" width="min(960px, 94vw)" :close-on-click-modal="false" @closed="resetDialog">
    <div class="steps-scroll">
      <el-steps :active="step" finish-status="success" align-center class="steps">
        <el-step title="类型与名称" />
        <el-step title="选择 Test" />
        <el-step title="划分预览" />
        <el-step title="训练参数" />
        <el-step title="SFT 起点" />
        <el-step title="确认提交" />
      </el-steps>
    </div>

    <div class="step-content">
      <el-form v-if="step === 0" label-width="100px">
        <el-form-item label="训练类型" :error="errors.task_type">
          <el-segmented v-model="form.task_type" :options="taskTypeOptions" />
        </el-form-item>
        <el-form-item label="任务名称" :error="errors.name">
          <el-input v-model="form.name" maxlength="255" show-word-limit placeholder="例如：SDK 故障 CPT 训练" />
        </el-form-item>
        <el-form-item label="模型路径" :error="errors.base_model_path">
          <el-input v-model="form.base_model_path" placeholder="基座模型路径，如 /models/Qwen3.5-9B（留空用系统默认）" />
        </el-form-item>
      </el-form>

      <div v-else-if="step === 1">
        <div class="test-toolbar">
          <el-input v-model="testFilters.search" clearable :disabled="selectionLocked" placeholder="Test 名称或平台" @keyup.enter="searchTests" />
          <el-select v-model="testFilters.completeness" :disabled="selectionLocked" @change="searchTests">
            <el-option label="全部完整性" value="" />
            <el-option label="完整" value="complete" />
            <el-option label="不完整" value="incomplete" />
          </el-select>
          <el-button :icon="Search" :disabled="selectionLocked" @click="searchTests">筛选</el-button>
          <el-button :loading="selectionLocked" :disabled="selectionLocked" @click="selectAllFiltered">选择全部筛选结果</el-button>
          <el-tag type="info">已选 {{ selectedCount }} 个 Test</el-tag>
        </div>
        <el-alert v-if="errors.test_version_ids" :title="errors.test_version_ids" type="error" :closable="false" />
        <el-table ref="testTable" :data="testRows" row-key="version_id" size="small" v-loading="testLoading" @select="handleSelect" @select-all="handlePageSelectAll">
          <el-table-column type="selection" width="42" :selectable="row => !selectionLocked && row.completeness === 'complete'" reserve-selection />
          <el-table-column prop="test_name" label="Test" min-width="160" show-overflow-tooltip />
          <el-table-column label="数据源类型" width="130">
            <template #default="{ row }">
              <el-tag size="small" :type="platformCategoryType(row.platform)">
                {{ platformCategoryLabel(row.platform) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="Round 1" width="105"><template #default="{ row }"><el-tag size="small" :type="parseType(row.round_1_parse_status)">{{ row.round_1_parse_status || '-' }}</el-tag></template></el-table-column>
          <el-table-column label="Round 2" width="105"><template #default="{ row }"><el-tag size="small" :type="parseType(row.round_2_parse_status)">{{ row.round_2_parse_status || '-' }}</el-tag></template></el-table-column>
          <el-table-column prop="completeness" label="完整性" width="90" />
          <el-table-column label="缺失项" min-width="150" show-overflow-tooltip><template #default="{ row }">{{ (row.missing_files || []).join('、') || '-' }}</template></el-table-column>
        </el-table>
        <div class="pagination"><el-pagination v-model:current-page="testPage.page" v-model:page-size="testPage.pageSize" :total="testPage.total" :page-sizes="[20, 50, 100]" :disabled="selectionLocked" layout="total, sizes, prev, pager, next" @current-change="changeTestPage" @size-change="searchTests" /></div>
      </div>

      <div v-else-if="step === 2">
        <el-form :inline="true" class="ratio-form">
          <el-form-item label="训练"><el-input-number v-model="form.train_ratio" :min="0" :max="1" :step="0.05" /></el-form-item>
          <el-form-item label="验证"><el-input-number v-model="form.validation_ratio" :min="0" :max="1" :step="0.05" /></el-form-item>
          <el-form-item label="测试"><el-input-number v-model="form.test_ratio" :min="0" :max="1" :step="0.05" /></el-form-item>
          <el-form-item label="种子"><el-input-number v-model="form.seed" :step="1" /></el-form-item>
          <el-button :icon="Refresh" :loading="previewLoading" @click="refreshPreview">重新划分</el-button>
        </el-form>
        <el-alert v-if="errors.ratios" :title="errors.ratios" type="error" :closable="false" />
        <el-alert v-if="errors.seed" :title="errors.seed" type="error" :closable="false" />
        <div v-if="splitPreview" class="split-preview">
          <div class="split-stat"><strong>{{ splitPreview.counts.train }}</strong><span>训练集</span></div>
          <div class="split-stat"><strong>{{ splitPreview.counts.validation }}</strong><span>验证集</span></div>
          <div class="split-stat"><strong>{{ splitPreview.counts.test }}</strong><span>测试集</span></div>
        </div>
        <el-table v-if="splitPreview" :data="splitPreview.strata" size="small">
          <el-table-column prop="sample_class" label="样本类型" />
          <el-table-column prop="domain" label="领域" />
          <el-table-column prop="fault_type" label="故障类型" min-width="160" />
          <el-table-column label="训练 / 验证 / 测试"><template #default="{ row }">{{ row.counts.train }} / {{ row.counts.validation }} / {{ row.counts.test }}</template></el-table-column>
        </el-table>
      </div>

      <el-form v-else-if="step === 3" label-width="112px">
        <el-form-item label="训练预设" :error="errors.preset"><el-segmented v-model="form.preset" :options="presetOptions" /></el-form-item>
        <el-collapse>
          <el-collapse-item title="高级参数（可选）" name="advanced">
            <div class="advanced-grid">
              <el-form-item v-for="item in overrideFields" :key="item.name" :label="item.label">
                <el-input-number v-model="form.overrides[item.name]" :min="item.min" :max="item.max" :step="item.step" controls-position="right" />
              </el-form-item>
            </div>
          </el-collapse-item>
        </el-collapse>
        <el-alert v-if="errors.overrides" :title="errors.overrides" type="error" :closable="false" />
      </el-form>

      <el-form v-else-if="step === 4" label-width="112px">
        <template v-if="form.task_type === 'sft'">
          <el-form-item label="SFT 起点" :error="errors.sft_source"><el-segmented v-model="form.sft_source" :options="sftSourceOptions" /></el-form-item>
          <el-form-item v-if="form.sft_source === 'cpt'" label="CPT Adapter" :error="errors.cpt_adapter_artifact_id">
            <el-select v-model="form.cpt_adapter_artifact_id" filterable placeholder="按 Artifact ID 选择" v-loading="adapterLoading">
              <el-option v-for="item in adapters" :key="item.id" :label="`${item.name} · ${item.id}`" :value="item.id" />
            </el-select>
          </el-form-item>
        </template>
        <el-result v-else icon="info" title="CPT 从基座模型开始" sub-title="CPT 任务无需选择 Adapter" />
      </el-form>

      <el-descriptions v-else :column="2" border size="small">
        <el-descriptions-item label="名称">{{ form.name }}</el-descriptions-item>
        <el-descriptions-item label="类型">{{ form.task_type.toUpperCase() }}</el-descriptions-item>
        <el-descriptions-item label="Test 数量">{{ selectedCount }}</el-descriptions-item>
        <el-descriptions-item label="预设">{{ form.preset }}</el-descriptions-item>
        <el-descriptions-item label="划分">{{ form.train_ratio }} / {{ form.validation_ratio }} / {{ form.test_ratio }}</el-descriptions-item>
        <el-descriptions-item label="SFT 起点">{{ form.task_type === 'sft' ? form.sft_source : 'base' }}</el-descriptions-item>
        <el-descriptions-item v-if="form.cpt_adapter_artifact_id" label="Adapter ID" :span="2">{{ form.cpt_adapter_artifact_id }}</el-descriptions-item>
      </el-descriptions>
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button v-if="step > 0" :disabled="selectionLocked || advancing || submitting" @click="previousStep">上一步</el-button>
      <el-button v-if="step < 5" type="primary" :loading="advancing" :disabled="selectionLocked" @click="nextStep">下一步</el-button>
      <el-button v-else type="primary" :loading="submitting" @click="submitTask">创建任务</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { createTrainingTask, listTrainingAdapters, listTrainingTests, previewTrainingSplit } from '@/api/modelTraining'
import { buildSplitPreviewPayload, buildTaskPayload, createTrainingForm, platformCategoryLabel, platformCategoryType, validateTaskForm } from '../trainingForm'
import { collectPaginatedIds, createLatestRequestState } from '../trainingUiState'

const props = defineProps({
  modelValue: Boolean,
  initialCptAdapterId: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'created'])
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const step = ref(0)
const form = reactive(createTrainingForm())
const errors = reactive({})
const testRows = ref([])
const testTable = ref(null)
const selectedIds = ref(new Set())
const splitPreview = ref(null)
const adapters = ref([])
const testLoading = ref(false)
const previewLoading = ref(false)
const adapterLoading = ref(false)
const submitting = ref(false)
const advancing = ref(false)
const activeSelectAllToken = ref(null)
const testFilters = reactive({ search: '', completeness: '' })
const testPage = reactive({ page: 1, pageSize: 20, total: 0 })
const dialogRequests = createLatestRequestState()
const testRequests = createLatestRequestState()
const previewRequests = createLatestRequestState()
const selectAllRequests = createLatestRequestState()
const advanceRequests = createLatestRequestState()
const adapterRequests = createLatestRequestState()
const submitRequests = createLatestRequestState()
let dialogToken = null
const taskTypeOptions = [{ label: 'CPT', value: 'cpt' }, { label: 'SFT', value: 'sft' }]
const presetOptions = [{ label: '快速 quick', value: 'quick' }, { label: '正式 formal', value: 'formal' }]
const sftSourceOptions = [{ label: '基座模型', value: 'base' }, { label: 'CPT Adapter', value: 'cpt' }]
const overrideFields = [
  { name: 'max_seq_length', label: '序列长度', min: 512, max: 8192, step: 128 },
  { name: 'learning_rate', label: '学习率', min: 0.000001, max: 0.01, step: 0.00001 },
  { name: 'num_train_epochs', label: '训练轮数', min: 0.1, max: 100, step: 0.5 },
  { name: 'lora_r', label: 'LoRA r', min: 4, max: 256, step: 4 },
  { name: 'lora_alpha', label: 'LoRA alpha', min: 1, max: 1024, step: 1 },
  { name: 'lora_dropout', label: 'LoRA dropout', min: 0, max: 0.5, step: 0.01 },
]
const selectedCount = computed(() => selectedIds.value.size)
const selectionLocked = computed(() => activeSelectAllToken.value !== null)

watch(() => props.modelValue, open => {
  if (open) {
    dialogToken = dialogRequests.start()
    if (props.initialCptAdapterId) {
      form.task_type = 'sft'
      form.sft_source = 'cpt'
      form.cpt_adapter_artifact_id = props.initialCptAdapterId
    }
    loadTests(dialogToken).catch(handleBackgroundError)
  } else {
    invalidateDialogWork()
  }
})
watch(() => form.task_type, value => { if (value === 'cpt') { form.sft_source = 'base'; form.cpt_adapter_artifact_id = '' } })
watch(selectedIds, invalidatePreview)
watch([() => form.train_ratio, () => form.validation_ratio, () => form.test_ratio, () => form.seed], invalidatePreview)

function applyErrors(values) { Object.keys(errors).forEach(key => delete errors[key]); Object.assign(errors, values) }
function currentErrors(keys) {
  const all = validateTaskForm(form)
  return Object.fromEntries(keys.filter(key => all[key]).map(key => [key, all[key]]))
}
function validateCurrent() {
  const allKeys = Object.keys(validateTaskForm(form))
  const keys = step.value === 0 ? ['name', 'task_type'] : step.value === 1 ? ['test_version_ids'] : step.value === 2 ? ['ratios', 'seed'] : step.value === 3 ? ['preset', 'overrides'] : step.value === 4 ? ['sft_source', 'cpt_adapter_artifact_id'] : allKeys
  const current = currentErrors(keys)
  applyErrors(current)
  return !Object.keys(current).length
}
function validateSplitStep() {
  form.test_version_ids = [...selectedIds.value]
  const current = currentErrors(['test_version_ids', 'ratios', 'seed'])
  applyErrors(current)
  return !Object.keys(current).length
}
function isDialogCurrent(lifecycleToken) {
  return lifecycleToken === dialogToken && dialogRequests.isLatest(lifecycleToken) && visible.value
}
function handleBackgroundError(error) { void error }
function inlineErrorMessage(error) { return error?.response?.data?.detail || error?.message || '请求失败，请稍后重试' }
function invalidatePreview() {
  previewRequests.invalidate()
  splitPreview.value = null
  previewLoading.value = false
}
function invalidateSelectAll() {
  selectAllRequests.invalidate()
  activeSelectAllToken.value = null
}
function invalidateDialogWork() {
  dialogRequests.invalidate()
  testRequests.invalidate()
  advanceRequests.invalidate()
  adapterRequests.invalidate()
  submitRequests.invalidate()
  invalidatePreview()
  invalidateSelectAll()
  dialogToken = null
  testLoading.value = false
  adapterLoading.value = false
  submitting.value = false
  advancing.value = false
}
function previousStep() {
  if (step.value <= 0 || selectionLocked.value || advancing.value) return
  submitRequests.invalidate()
  advanceRequests.invalidate()
  submitting.value = false
  step.value -= 1
}
async function nextStep() {
  form.test_version_ids = [...selectedIds.value]
  if (!validateCurrent()) return
  const lifecycleToken = dialogToken
  const operationToken = advanceRequests.start()
  advancing.value = true
  try {
    if ((step.value === 1 || step.value === 2) && !await loadPreview(lifecycleToken)) return
    if (step.value === 3 && form.task_type === 'sft' && !await loadAdapters(lifecycleToken)) return
    if (!isDialogCurrent(lifecycleToken) || !advanceRequests.isLatest(operationToken)) return
    step.value += 1
  } catch (error) {
    if (isDialogCurrent(lifecycleToken) && advanceRequests.isLatest(operationToken)) ElMessage.error(inlineErrorMessage(error))
  } finally {
    if (advanceRequests.isLatest(operationToken)) advancing.value = false
  }
}
async function loadTests(lifecycleToken = dialogToken) {
  const requestToken = testRequests.start()
  testLoading.value = true
  try {
    const result = await listTrainingTests({ search: testFilters.search.trim() || undefined, completeness: testFilters.completeness || undefined, page: testPage.page, page_size: testPage.pageSize })
    if (!isDialogCurrent(lifecycleToken) || !testRequests.isLatest(requestToken)) return false
    testRows.value = result.items || []
    testPage.total = result.total || 0
    await nextTick()
    if (!isDialogCurrent(lifecycleToken) || !testRequests.isLatest(requestToken)) return false
    testRows.value.forEach(row => testTable.value?.toggleRowSelection(row, selectedIds.value.has(row.version_id)))
    return true
  } finally {
    if (testRequests.isLatest(requestToken)) testLoading.value = false
  }
}
function searchTests() {
  invalidateSelectAll()
  testPage.page = 1
  loadTests(dialogToken).catch(handleBackgroundError)
}
function changeTestPage() {
  invalidateSelectAll()
  loadTests(dialogToken).catch(handleBackgroundError)
}
function handleSelect(selection, row) {
  if (selectionLocked.value) return
  const next = new Set(selectedIds.value)
  if (selection.some(item => item.version_id === row.version_id)) next.add(row.version_id)
  else next.delete(row.version_id)
  selectedIds.value = next
}
function handlePageSelectAll(selection) {
  if (selectionLocked.value) return
  const selected = new Set(selection.map(row => row.version_id))
  const next = new Set(selectedIds.value)
  for (const row of testRows.value) {
    if (selected.has(row.version_id)) next.add(row.version_id)
    else next.delete(row.version_id)
  }
  selectedIds.value = next
}
async function selectAllFiltered() {
  const lifecycleToken = dialogToken
  const operationToken = selectAllRequests.start()
  activeSelectAllToken.value = operationToken
  try {
    const result = await collectPaginatedIds({
      filters: {
        search: testFilters.search.trim() || undefined,
        completeness: 'complete',
      },
      fetchPage: listTrainingTests,
      isCurrent: () => isDialogCurrent(lifecycleToken) && selectAllRequests.isLatest(operationToken),
    })
    if (result.cancelled || !isDialogCurrent(lifecycleToken) || !selectAllRequests.isLatest(operationToken)) return
    selectedIds.value = new Set(result.ids)
    form.test_version_ids = [...result.ids]
    await loadTests(lifecycleToken)
    if (isDialogCurrent(lifecycleToken) && selectAllRequests.isLatest(operationToken)) {
      if (result.truncated) ElMessage.warning(`已选择前 ${result.ids.length} 个完整 Test，结果过多已达到安全上限`)
      else ElMessage.success(`已选择 ${result.ids.length} 个完整 Test`)
    }
  } catch (error) {
    if (isDialogCurrent(lifecycleToken) && selectAllRequests.isLatest(operationToken)) ElMessage.error(inlineErrorMessage(error))
  } finally {
    if (activeSelectAllToken.value === operationToken) activeSelectAllToken.value = null
  }
}
async function loadPreview(lifecycleToken = dialogToken) {
  form.test_version_ids = [...selectedIds.value]
  const requestToken = previewRequests.start()
  previewLoading.value = true
  try {
    const payload = Object.freeze(buildSplitPreviewPayload({
      ...form,
      test_version_ids: [...form.test_version_ids],
      overrides: { ...form.overrides },
    }))
    const result = await previewTrainingSplit(payload)
    if (!isDialogCurrent(lifecycleToken) || !previewRequests.isLatest(requestToken)) return false
    splitPreview.value = result
    return true
  } finally {
    if (previewRequests.isLatest(requestToken)) previewLoading.value = false
  }
}
async function refreshPreview() {
  if (!validateSplitStep()) return
  try {
    await loadPreview(dialogToken)
  } catch (error) {
    applyErrors({ ratios: inlineErrorMessage(error) })
  }
}
async function loadAdapters(lifecycleToken) {
  const requestToken = adapterRequests.start()
  adapterLoading.value = true
  try {
    const result = await listTrainingAdapters({ adapter_stage: 'cpt', page: 1, page_size: 100 })
    if (!isDialogCurrent(lifecycleToken) || !adapterRequests.isLatest(requestToken)) return false
    adapters.value = (result.items || []).filter(item => item.adapter_stage === 'cpt' && item.usable_for_sft)
    return true
  } finally {
    if (adapterRequests.isLatest(requestToken)) adapterLoading.value = false
  }
}
async function submitTask() {
  form.test_version_ids = [...selectedIds.value]
  if (!validateCurrent()) return
  const lifecycleToken = dialogToken
  const operationToken = submitRequests.start()
  submitting.value = true
  try {
    const payload = Object.freeze(buildTaskPayload(form))
    const task = await createTrainingTask(payload)
    if (!isDialogCurrent(lifecycleToken) || !submitRequests.isLatest(operationToken)) return
    ElMessage.success('训练任务已创建')
    emit('created', task)
  } catch (error) {
    if (isDialogCurrent(lifecycleToken) && submitRequests.isLatest(operationToken)) ElMessage.error(inlineErrorMessage(error))
  } finally {
    if (submitRequests.isLatest(operationToken)) submitting.value = false
  }
}
function parseType(value) { return value === 'parsed' ? 'success' : value?.includes('failed') ? 'danger' : 'info' }
function resetDialog() {
  invalidateDialogWork()
  step.value = 0
  Object.assign(form, createTrainingForm())
  applyErrors({})
  testRows.value = []
  selectedIds.value = new Set()
  splitPreview.value = null
  adapters.value = []
  Object.assign(testFilters, { search: '', completeness: '' })
  Object.assign(testPage, { page: 1, pageSize: 20, total: 0 })
}
</script>

<style scoped>
.steps { margin-bottom: 22px; }
.step-content { min-height: 410px; max-height: 62vh; overflow: auto; padding: 4px 2px; }
.test-toolbar { display: grid; grid-template-columns: minmax(180px, 1fr) 150px auto auto auto; gap: 8px; align-items: center; margin-bottom: 10px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 12px; }
.ratio-form { display: flex; align-items: center; gap: 4px; }
.split-preview { display: grid; grid-template-columns: repeat(3, 1fr); margin: 14px 0; border: 1px solid #e5e7eb; }
.split-stat { display: flex; flex-direction: column; align-items: center; padding: 14px; border-right: 1px solid #e5e7eb; }
.split-stat:last-child { border-right: 0; }
.split-stat strong { font-size: 24px; color: #2563eb; }
.split-stat span { margin-top: 3px; color: #6b7280; font-size: 12px; }
.advanced-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.advanced-grid :deep(.el-input-number), :deep(.el-select) { width: 100%; }
@media (max-width: 760px) {
  .steps-scroll { overflow-x: auto; padding-bottom: 4px; }
  .steps { min-width: 680px; }
  .test-toolbar, .advanced-grid { grid-template-columns: 1fr; }
  .ratio-form { flex-direction: column; align-items: stretch; }
  .ratio-form :deep(.el-form-item), .ratio-form :deep(.el-input-number), .ratio-form > .el-button { width: 100%; margin-right: 0; }
}
</style>
