<template>
  <el-dialog v-model="visible" title="评估 Adapter" width="min(860px, 94vw)" :close-on-click-modal="false" @closed="resetDialog">
    <el-form label-width="92px">
      <el-form-item label="Adapter">
        <el-input :model-value="props.adapter?.name || '-'" disabled />
      </el-form-item>
      <el-form-item label="任务名称" :error="errors.name">
        <el-input v-model="form.name" maxlength="255" show-word-limit :disabled="submitting" />
      </el-form-item>
      <el-form-item label="选择 Test" :error="errors.test_version_ids">
        <div class="test-selector">
          <div class="test-toolbar">
            <el-input v-model="filters.search" clearable placeholder="Test 名称或平台" :disabled="submitting" @keyup.enter="searchTests" />
            <el-button :icon="Search" :disabled="submitting" @click="searchTests">筛选</el-button>
            <el-tag type="info">已选 {{ selectedIds.size }}</el-tag>
          </div>
          <el-table
            ref="testTable"
            :data="testRows"
            row-key="version_id"
            size="small"
            max-height="360"
            v-loading="testLoading"
            @select="handleSelect"
            @select-all="handlePageSelectAll"
          >
            <el-table-column type="selection" width="46" :selectable="isSelectable" />
            <el-table-column prop="test_name" label="Test" min-width="190" show-overflow-tooltip />
            <el-table-column prop="platform" label="平台" min-width="120" show-overflow-tooltip />
            <el-table-column prop="version_id" label="Version ID" min-width="110" />
          </el-table>
          <div class="pagination">
            <el-pagination
              v-model:current-page="page.page"
              :page-size="page.pageSize"
              :total="page.total"
              layout="total, prev, pager, next"
              @current-change="changeTestPage"
            />
          </div>
        </div>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button :disabled="submitting" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" :disabled="submitting" @click="submitEvaluation">创建评估</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { evaluateTrainingAdapter, listTrainingTests } from '@/api/modelTraining'
import { createLatestRequestState } from '../trainingUiState'

const props = defineProps({
  modelValue: Boolean,
  adapter: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue', 'created'])
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const form = reactive({ name: '' })
const errors = reactive({})
const filters = reactive({ search: '' })
const page = reactive({ page: 1, pageSize: 20, total: 0 })
const testRows = ref([])
const testTable = ref(null)
const selectedIds = ref(new Set())
const testLoading = ref(false)
const submitting = ref(false)
const dialogRequests = createLatestRequestState()
const testRequests = createLatestRequestState()
const submitRequests = createLatestRequestState()
let dialogToken = null

watch(() => props.modelValue, open => {
  if (open) {
    dialogToken = dialogRequests.start()
    if (!form.name && props.adapter?.name) form.name = `${props.adapter.name} 评估`
    loadTests(dialogToken).catch(handleBackgroundError)
  } else {
    invalidateDialogWork()
  }
})

function isDialogCurrent(lifecycleToken) {
  return lifecycleToken === dialogToken && dialogRequests.isLatest(lifecycleToken) && visible.value
}
function handleBackgroundError(error) { void error }
function errorMessage(error) { return error?.response?.data?.detail || error?.message || '请求失败，请稍后重试' }
function applyErrors(values) {
  Object.keys(errors).forEach(key => delete errors[key])
  Object.assign(errors, values)
}
function validateForm() {
  const next = {}
  if (!form.name.trim()) next.name = '请输入评估任务名称'
  if (form.name.trim().length > 255) next.name = '任务名称不能超过 255 个字符'
  if (selectedIds.value.size === 0) next.test_version_ids = '请至少选择一个完整 Test'
  applyErrors(next)
  return Object.keys(next).length === 0
}
function isSelectable(row) {
  return !submitting.value && row.completeness === 'complete'
}
async function loadTests(lifecycleToken = dialogToken) {
  const requestToken = testRequests.start()
  testLoading.value = true
  try {
    const result = await listTrainingTests({
      search: filters.search.trim() || undefined,
      completeness: 'complete',
      page: page.page,
      page_size: page.pageSize,
    })
    if (!isDialogCurrent(lifecycleToken) || !testRequests.isLatest(requestToken)) return
    testRows.value = (result.items || []).filter(row => row.completeness === 'complete')
    page.total = result.total || 0
    await nextTick()
    if (!isDialogCurrent(lifecycleToken) || !testRequests.isLatest(requestToken)) return
    for (const row of testRows.value) {
      testTable.value?.toggleRowSelection(row, selectedIds.value.has(row.version_id))
    }
  } finally {
    if (testRequests.isLatest(requestToken)) testLoading.value = false
  }
}
function searchTests() {
  page.page = 1
  loadTests(dialogToken).catch(handleBackgroundError)
}
function changeTestPage() {
  loadTests(dialogToken).catch(handleBackgroundError)
}
function handleSelect(selection, row) {
  if (!isSelectable(row)) return
  const next = new Set(selectedIds.value)
  if (selection.some(item => item.version_id === row.version_id)) next.add(row.version_id)
  else next.delete(row.version_id)
  selectedIds.value = next
}
function handlePageSelectAll(selection) {
  if (submitting.value) return
  const selected = new Set(selection.filter(isSelectable).map(row => row.version_id))
  const next = new Set(selectedIds.value)
  for (const row of testRows.value) {
    if (!isSelectable(row)) continue
    if (selected.has(row.version_id)) next.add(row.version_id)
    else next.delete(row.version_id)
  }
  selectedIds.value = next
}
async function submitEvaluation() {
  if (submitting.value) return
  if (!validateForm() || !props.adapter?.id) return
  const lifecycleToken = dialogToken
  const operationToken = submitRequests.start()
  submitting.value = true
  try {
    const uniqueIds = [...new Set(selectedIds.value)]
    const task = await evaluateTrainingAdapter(props.adapter.id, {
      name: form.name.trim(),
      test_version_ids: uniqueIds,
    })
    if (!isDialogCurrent(lifecycleToken) || !submitRequests.isLatest(operationToken)) return
    ElMessage.success('评估任务已创建')
    emit('created', task)
    visible.value = false
  } catch (error) {
    if (isDialogCurrent(lifecycleToken) && submitRequests.isLatest(operationToken)) ElMessage.error(errorMessage(error))
  } finally {
    if (submitRequests.isLatest(operationToken)) submitting.value = false
  }
}
function invalidateDialogWork() {
  dialogRequests.invalidate()
  testRequests.invalidate()
  submitRequests.invalidate()
  dialogToken = null
  testLoading.value = false
  submitting.value = false
}
function resetDialog() {
  invalidateDialogWork()
  form.name = ''
  applyErrors({})
  filters.search = ''
  Object.assign(page, { page: 1, pageSize: 20, total: 0 })
  testRows.value = []
  selectedIds.value = new Set()
}

onBeforeUnmount(invalidateDialogWork)
</script>

<style scoped>
.test-selector { width: 100%; min-width: 0; }
.test-toolbar { display: grid; grid-template-columns: minmax(180px, 1fr) auto auto; gap: 8px; margin-bottom: 10px; }
.pagination { display: flex; justify-content: flex-end; margin-top: 12px; }
@media (max-width: 640px) {
  .test-toolbar { grid-template-columns: 1fr auto; }
  .test-toolbar .el-tag { grid-column: 1 / -1; width: max-content; }
}
</style>
