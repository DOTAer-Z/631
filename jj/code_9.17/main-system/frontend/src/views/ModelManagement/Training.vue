<template>
  <div class="training-page">
    <div class="page-toolbar">
      <div>
        <h2>大模型微调</h2>
        <p>创建 CPT / SFT 任务并跟踪训练队列、指标和产物。</p>
      </div>
      <div class="toolbar-actions">
        <el-button :icon="Refresh" :loading="refreshing" @click="refreshCurrent">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openNewTaskDialog">新建任务</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="training-tabs" @tab-change="refreshCurrent">
      <el-tab-pane label="训练任务" name="tasks">
        <el-form :inline="true" class="filters">
          <el-form-item label="类型">
            <el-select v-model="filters.taskType" clearable placeholder="全部" @change="searchTasks">
              <el-option label="CPT" value="cpt" />
              <el-option label="SFT" value="sft" />
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="filters.state" clearable placeholder="全部" @change="searchTasks">
              <el-option v-for="state in taskStates" :key="state" :label="statusLabel(state)" :value="state" />
            </el-select>
          </el-form-item>
          <el-form-item label="任务">
            <el-select v-model="filters.jobKind" clearable placeholder="全部" @change="searchTasks">
              <el-option label="训练" value="training" />
              <el-option label="评估" value="evaluation" />
            </el-select>
          </el-form-item>
        </el-form>

        <el-table :data="tasks" size="small" row-key="id" v-loading="taskLoading">
          <el-table-column prop="name" label="任务名称" min-width="180" show-overflow-tooltip />
          <el-table-column label="类型" width="92">
            <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.task_type.toUpperCase() }}</el-tag></template>
          </el-table-column>
          <el-table-column label="状态" width="118">
            <template #default="{ row }"><el-tag :type="statusType(row.state)" size="small">{{ statusLabel(row.state) }}</el-tag></template>
          </el-table-column>
          <el-table-column label="进度" width="150">
            <template #default="{ row }">
              <el-progress v-if="row.progress != null" :percentage="progressPercent(row.progress)" :stroke-width="8" />
              <span v-else-if="row.queue_position != null">队列第 {{ row.queue_position }} 位</span>
              <span v-else class="muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="最新 Loss" width="112">
            <template #default="{ row }">{{ metricValue(row.latest_metric && row.latest_metric.loss) }}</template>
          </el-table-column>
          <el-table-column label="计算设备" min-width="145" show-overflow-tooltip>
            <template #default="{ row }">{{ taskDeviceSummary(row) }}</template>
          </el-table-column>
          <el-table-column label="排队时间" min-width="168">
            <template #default="{ row }">{{ formatDateTime(row.queued_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="255" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDrawer(row)">详情</el-button>
              <el-button v-if="canCancel(row)" link type="warning" :loading="isTaskAction(row.id, 'cancel')" :disabled="isTaskPending(row.id)" @click="cancelTask(row)">取消</el-button>
              <el-button v-if="canRetry(row)" link type="primary" :loading="isTaskAction(row.id, 'retry')" :disabled="isTaskPending(row.id)" @click="retryTask(row)">重试</el-button>
              <el-button v-if="canEvaluate(row)" link type="success" :loading="isTaskAction(row.id, 'evaluate')" :disabled="isTaskPending(row.id)" @click="evaluateTask(row)">评估</el-button>
              <el-tooltip content="删除任务" placement="top">
                <el-button v-if="isTerminal(row.state)" link type="danger" :icon="Delete" :loading="isTaskAction(row.id, 'delete')" :disabled="isTaskPending(row.id)" aria-label="删除任务" @click="removeTask(row)" />
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination">
          <el-pagination v-model:current-page="taskPage.page" v-model:page-size="taskPage.pageSize" :total="taskPage.total" :page-sizes="[20, 50, 100]" layout="total, sizes, prev, pager, next" @current-change="loadTasks" @size-change="searchTasks" />
        </div>
      </el-tab-pane>

      <el-tab-pane label="Adapter" name="adapters">
        <div class="adapter-toolbar">
          <el-form :inline="true" class="filters">
            <el-form-item label="来源">
              <el-select v-model="adapterFilters.source" clearable placeholder="全部" @change="searchAdapters">
                <el-option label="训练生成" value="training" />
                <el-option label="上传" value="uploaded" />
              </el-select>
            </el-form-item>
            <el-form-item label="阶段">
              <el-select v-model="adapterFilters.adapterStage" clearable placeholder="全部" @change="searchAdapters">
                <el-option label="CPT" value="cpt" />
                <el-option label="SFT" value="sft" />
              </el-select>
            </el-form-item>
            <el-form-item label="名称">
              <el-input v-model="adapterFilters.search" clearable placeholder="Adapter 名称" @keyup.enter="searchAdapters" />
            </el-form-item>
            <el-button :icon="Search" @click="searchAdapters">筛选</el-button>
          </el-form>
          <el-button type="primary" :icon="Upload" @click="uploadDialogVisible = true">上传 Adapter</el-button>
        </div>
        <el-table :data="artifacts" size="small" row-key="id" v-loading="artifactLoading">
          <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
          <el-table-column prop="id" label="Artifact ID" min-width="220" show-overflow-tooltip />
          <el-table-column label="来源" width="100"><template #default="{ row }">{{ row.source === 'uploaded' ? '上传' : '训练生成' }}</template></el-table-column>
          <el-table-column label="阶段" width="86"><template #default="{ row }">{{ row.adapter_stage?.toUpperCase() || '-' }}</template></el-table-column>
          <el-table-column label="大小" width="110"><template #default="{ row }">{{ formatBytes(row.size_bytes) }}</template></el-table-column>
          <el-table-column label="操作" width="250" fixed="right">
            <template #default="{ row }">
              <el-button v-if="row.usable_for_sft" link type="primary" :disabled="isArtifactPending(row.id)" @click="useAdapterForSft(row)">用于 SFT</el-button>
              <el-button v-if="row.evaluable" link type="success" :disabled="isArtifactPending(row.id)" @click="evaluateAdapter(row)">评估</el-button>
              <el-tooltip content="下载产物" placement="top"><el-button link type="primary" :icon="Download" :disabled="isArtifactPending(row.id)" aria-label="下载产物" @click="downloadArtifact(row.id)" /></el-tooltip>
              <el-tooltip content="删除产物" placement="top"><el-button link type="danger" :icon="Delete" :loading="isArtifactAction(row.id, 'delete')" :disabled="!row.deletable || isArtifactPending(row.id)" aria-label="删除产物" @click="removeArtifact(row)" /></el-tooltip>
            </template>
          </el-table-column>
        </el-table>
        <div class="pagination"><el-pagination v-model:current-page="artifactPage.page" :total="artifactPage.total" :page-size="artifactPage.pageSize" layout="total, prev, pager, next" @current-change="loadArtifacts" /></div>
      </el-tab-pane>

      <el-tab-pane label="Worker" name="worker">
        <el-descriptions :column="3" border v-loading="workerLoading">
          <el-descriptions-item label="状态"><el-tag :type="worker.status === 'busy' ? 'warning' : worker.status === 'idle' ? 'success' : 'info'">{{ worker.status || 'offline' }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="模型状态">{{ worker.model_status || '-' }}</el-descriptions-item>
          <el-descriptions-item label="队列长度">{{ worker.queue_length || 0 }}</el-descriptions-item>
          <el-descriptions-item label="当前任务">{{ worker.current_task_id || '-' }}</el-descriptions-item>
          <el-descriptions-item label="计算设备">{{ deviceSummary }}</el-descriptions-item>
          <el-descriptions-item label="心跳">{{ formatDateTime(worker.last_heartbeat_at) }}</el-descriptions-item>
        </el-descriptions>
      </el-tab-pane>
    </el-tabs>

    <TrainingTaskDialog v-model="dialogVisible" :initial-cpt-adapter-id="initialCptAdapterId" @created="handleCreated" />
    <TrainingTaskDrawer v-model="drawerVisible" :task-id="selectedTaskId" @changed="loadTasks" />
    <AdapterUploadDialog v-model="uploadDialogVisible" @imported="handleAdapterImported" />
    <AdapterEvaluationDialog v-model="evaluationDialogVisible" :adapter="evaluationAdapter" @created="handleAdapterEvaluationCreated" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Download, Plus, Refresh, Search, Upload } from '@element-plus/icons-vue'
import {
  cancelTrainingTask, deleteTrainingArtifact, deleteTrainingTask, downloadTrainingArtifactUrl,
  evaluateTrainingTask, getTrainingWorker, listTrainingAdapters, listTrainingArtifacts, listTrainingTasks, retryTrainingTask,
} from '@/api/modelTraining'
import AdapterEvaluationDialog from './components/AdapterEvaluationDialog.vue'
import AdapterUploadDialog from './components/AdapterUploadDialog.vue'
import TrainingTaskDialog from './components/TrainingTaskDialog.vue'
import TrainingTaskDrawer from './components/TrainingTaskDrawer.vue'
import { canCancel, canEvaluate, canRetry, statusLabel, statusType } from './trainingForm'
import { createKeyedOperationState, createLatestRequestState, createSerialPoller } from './trainingUiState'

const taskStates = ['queued', 'preparing_data', 'training', 'evaluating', 'cancelling', 'cancelled', 'succeeded', 'failed', 'interrupted']
const activeTab = ref('tasks')
const tasks = ref([])
const artifacts = ref([])
const worker = ref({ status: 'offline', queue_length: 0, device_type: null, profile_name: null, device: null, gpu: null })
const taskLoading = ref(false)
const artifactLoading = ref(false)
const workerLoading = ref(false)
const dialogVisible = ref(false)
const drawerVisible = ref(false)
const uploadDialogVisible = ref(false)
const evaluationDialogVisible = ref(false)
const selectedTaskId = ref('')
const initialCptAdapterId = ref('')
const evaluationAdapter = ref(null)
const filters = reactive({ taskType: '', state: '', jobKind: '' })
const adapterFilters = reactive({ source: '', adapterStage: '', search: '' })
const taskPage = reactive({ page: 1, pageSize: 20, total: 0 })
const artifactPage = reactive({ page: 1, pageSize: 20, total: 0 })
let taskRequest = 0
let workerRequest = 0
const adapterRequests = createLatestRequestState()
let adapterRefreshQueue = Promise.resolve()
const actionOperations = createKeyedOperationState()
const actionVersion = ref(0)

const refreshing = computed(() => taskLoading.value || artifactLoading.value || workerLoading.value)
const cpuProfileLabels = Object.freeze({
  cpu_qlora_4bit_bf16: 'CPU · 4-bit BF16',
  cpu_qlora_4bit_fp32: 'CPU · 4-bit FP32',
  cpu_lora_bf16: 'CPU · LoRA BF16',
  cpu_lora_fp32: 'CPU · LoRA FP32',
})
const deviceSummary = computed(() => {
  const { device_type: deviceType, profile_name: profileName, device } = worker.value
  if (deviceType === 'cpu') {
    if (device !== 'CPU') return '-'
    return cpuProfileLabels[profileName] || '-'
  }
  if (deviceType === 'cuda') {
    if (profileName !== 'cuda_qlora_bf16' || typeof device !== 'string') return '-'
    if (!device || device.trim() !== device || device === 'CPU') return '-'
    return device
  }
  return '-'
})
const mainPoller = createSerialPoller({
  delay: 5000,
  run: () => Promise.all([loadTasks(), loadWorker()]),
  onError: handleBackgroundError,
})

async function loadTasks() {
  const requestId = ++taskRequest
  taskLoading.value = true
  try {
    const result = await listTrainingTasks({ task_type: filters.taskType || undefined, state: filters.state || undefined, job_kind: filters.jobKind || undefined, page: taskPage.page, page_size: taskPage.pageSize })
    if (requestId !== taskRequest) return
    tasks.value = result.items || []
    taskPage.total = result.total || 0
  } finally { if (requestId === taskRequest) taskLoading.value = false }
}
function loadArtifacts() {
  const requestToken = adapterRequests.start()
  const query = Object.freeze({
    source: adapterFilters.source || undefined,
    adapter_stage: adapterFilters.adapterStage || undefined,
    search: adapterFilters.search.trim() || undefined,
    page: artifactPage.page,
    page_size: artifactPage.pageSize,
  })
  artifactLoading.value = true
  const refresh = adapterRefreshQueue.catch(() => {}).then(async () => {
    if (!adapterRequests.isLatest(requestToken)) return
    const result = await listTrainingAdapters({
      source: query.source,
      adapter_stage: query.adapter_stage,
      search: query.search,
      page: query.page,
      page_size: query.page_size,
    })
    if (!adapterRequests.isLatest(requestToken)) return
    artifacts.value = result.items || []
    artifactPage.total = result.total || 0
  })
  adapterRefreshQueue = refresh
  return refresh.finally(() => {
    if (adapterRequests.isLatest(requestToken)) artifactLoading.value = false
  })
}
async function loadWorker() {
  const requestId = ++workerRequest
  workerLoading.value = true
  try { const result = await getTrainingWorker(); if (requestId === workerRequest) worker.value = result }
  finally { if (requestId === workerRequest) workerLoading.value = false }
}
function searchTasks() { taskPage.page = 1; loadTasks().catch(handleBackgroundError) }
function searchAdapters() { artifactPage.page = 1; loadArtifacts().catch(handleBackgroundError) }
function refreshCurrent() {
  if (activeTab.value === 'adapters') return loadArtifacts()
  if (activeTab.value === 'worker') return loadWorker()
  return Promise.all([loadTasks(), loadWorker()])
}
function openDrawer(row) { selectedTaskId.value = row.id; drawerVisible.value = true }
function openNewTaskDialog() { initialCptAdapterId.value = ''; dialogVisible.value = true }
function handleBackgroundError(error) { void error }
function bumpActionVersion() { actionVersion.value += 1 }
function isTaskPending(id) {
  actionVersion.value
  return actionOperations.isPending(`task:${id}`)
}
function isTaskAction(id, action) {
  actionVersion.value
  return actionOperations.isActive(`task:${id}`, action)
}
function isArtifactPending(id) {
  actionVersion.value
  return actionOperations.isPending(`artifact:${id}`)
}
function isArtifactAction(id, action) {
  actionVersion.value
  return actionOperations.isActive(`artifact:${id}`, action)
}
function actionErrorMessage(error) { return error?.response?.data?.detail || error?.message || '操作失败，请稍后重试' }
async function performTaskAction(row, actionName, action, message) {
  const operationToken = actionOperations.start(`task:${row.id}`, actionName)
  if (!operationToken) return
  bumpActionVersion()
  try {
    await action()
    ElMessage.success(message)
    await Promise.all([loadTasks(), loadWorker()])
  } catch (error) {
    ElMessage.error(actionErrorMessage(error))
  } finally {
    actionOperations.finish(operationToken)
    bumpActionVersion()
  }
}
async function confirmAction(message, title) {
  try {
    await ElMessageBox.confirm(message, title, { type: 'warning' })
    return true
  } catch {
    return false
  }
}
async function cancelTask(row) {
  if (!await confirmAction(`确认取消任务“${row.name}”吗？`, '取消任务')) return
  await performTaskAction(row, 'cancel', () => cancelTrainingTask(row.id), '已提交取消请求')
}
async function retryTask(row) { await performTaskAction(row, 'retry', () => retryTrainingTask(row.id), '重试任务已入队') }
async function evaluateTask(row) { await performTaskAction(row, 'evaluate', () => evaluateTrainingTask(row.id), '评估任务已入队') }
async function removeTask(row) {
  if (!await confirmAction(`确认删除任务“${row.name}”吗？`, '删除任务')) return
  await performTaskAction(row, 'delete', () => deleteTrainingTask(row.id), '任务已删除')
}
function downloadArtifact(id) { window.open(downloadTrainingArtifactUrl(id), '_blank') }
async function removeArtifact(row) {
  if (!row.deletable) return
  if (!await confirmAction(`确认删除产物 ${row.id} 吗？`, '删除产物')) return
  const operationToken = actionOperations.start(`artifact:${row.id}`, 'delete')
  if (!operationToken) return
  bumpActionVersion()
  try {
    await deleteTrainingArtifact(row.id)
    ElMessage.success('产物已删除')
    await loadArtifacts()
  } catch (error) {
    ElMessage.error(actionErrorMessage(error))
  } finally {
    actionOperations.finish(operationToken)
    bumpActionVersion()
  }
}
function useAdapterForSft(row) { initialCptAdapterId.value = row.id; dialogVisible.value = true }
function evaluateAdapter(row) { evaluationAdapter.value = row; evaluationDialogVisible.value = true }
function handleAdapterImported() { activeTab.value = 'adapters'; artifactPage.page = 1; loadArtifacts().catch(handleBackgroundError) }
function handleAdapterEvaluationCreated(task) { evaluationDialogVisible.value = false; activeTab.value = 'tasks'; taskPage.page = 1; loadTasks().catch(handleBackgroundError); if (task?.id) { selectedTaskId.value = task.id; drawerVisible.value = true } }
function handleCreated(task) { dialogVisible.value = false; initialCptAdapterId.value = ''; activeTab.value = 'tasks'; taskPage.page = 1; loadTasks().catch(handleBackgroundError); if (task?.id) { selectedTaskId.value = task.id; drawerVisible.value = true } }
function isTerminal(state) { return ['cancelled', 'succeeded', 'failed', 'interrupted'].includes(state) }
function taskDeviceSummary(row) { return worker.value.current_task_id === row.id ? deviceSummary.value : '-' }
function progressPercent(value) { return Math.max(0, Math.min(100, Math.round(Number(value) <= 1 ? Number(value) * 100 : Number(value)))) }
function metricValue(value) { return value == null ? '-' : Number(value).toFixed(4) }
function formatDateTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
function formatBytes(value) { if (value == null) return '-'; if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB` }

onMounted(() => { mainPoller.start() })
onBeforeUnmount(() => { taskRequest += 1; adapterRequests.invalidate(); workerRequest += 1; mainPoller.stop() })
</script>

<style scoped>
.training-page { width: 100%; }
.page-toolbar { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 8px; }
.page-toolbar h2 { margin: 0; font-size: 20px; color: #1f2937; }
.page-toolbar p { margin: 5px 0 0; color: #6b7280; font-size: 13px; }
.toolbar-actions { display: flex; gap: 8px; }
.training-tabs { width: 100%; }
.filters { margin: 0 0 4px; }
.filters :deep(.el-select) { width: 140px; }
.adapter-toolbar { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.adapter-toolbar .filters { flex: 1; }
.pagination { display: flex; justify-content: flex-end; margin-top: 14px; }
.muted { color: #9ca3af; }
@media (max-width: 760px) {
  .adapter-toolbar { flex-direction: column; }
  .adapter-toolbar .filters { width: 100%; }
}
</style>
