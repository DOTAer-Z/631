<template>
  <el-drawer v-model="visible" title="训练任务详情" size="min(880px, 92vw)" @opened="openDrawer" @closed="cleanupDrawer">
    <div v-loading="loading" class="drawer-body">
      <template v-if="task">
        <div class="drawer-toolbar">
          <div><strong>{{ task.name }}</strong><el-tag :type="statusType(task.state)" size="small">{{ statusLabel(task.state) }}</el-tag></div>
          <div>
            <el-tooltip content="刷新详情" placement="top"><el-button circle :icon="Refresh" aria-label="刷新详情" @click="refreshTask" /></el-tooltip>
            <el-tooltip content="刷新日志" placement="top"><el-button circle :icon="Document" aria-label="刷新日志" @click="refreshLogs" /></el-tooltip>
          </div>
        </div>

        <el-tabs v-model="activeTab">
          <el-tab-pane label="概览" name="overview">
            <el-descriptions :column="descriptionColumns" border size="small">
              <el-descriptions-item label="任务 ID" :span="descriptionColumns === 1 ? 1 : 2">{{ task.id }}</el-descriptions-item>
              <el-descriptions-item label="类型">{{ task.task_type.toUpperCase() }}</el-descriptions-item>
              <el-descriptions-item label="模型">{{ task.model_id }}</el-descriptions-item>
              <el-descriptions-item label="排队位置">{{ task.queue_position ?? '-' }}</el-descriptions-item>
              <el-descriptions-item label="最新 Loss">{{ metricValue(task.latest_metric?.loss) }}</el-descriptions-item>
            </el-descriptions>
            <h4>时间线</h4>
            <el-timeline class="timeline">
              <el-timeline-item v-for="event in task.timeline" :key="`${event.name}-${event.at}`" :timestamp="formatDateTime(event.at)">{{ timelineLabel(event.name) }}</el-timeline-item>
            </el-timeline>
          </el-tab-pane>
          <el-tab-pane label="数据与配置" name="data">
            <h4>冻结划分</h4>
            <el-descriptions :column="descriptionColumns" border size="small" class="split-counts">
              <el-descriptions-item label="训练">{{ task.split_counts.train }}</el-descriptions-item>
              <el-descriptions-item label="验证">{{ task.split_counts.validation }}</el-descriptions-item>
              <el-descriptions-item label="测试">{{ task.split_counts.test }}</el-descriptions-item>
            </el-descriptions>
            <h4>公开配置</h4>
            <pre class="config">{{ JSON.stringify(task.config, null, 2) }}</pre>
          </el-tab-pane>
          <el-tab-pane label="指标" name="metrics">
            <template v-if="task.job_kind === 'training'">
              <div ref="chartElement" class="metric-chart" />
              <el-empty v-if="!(task.metrics || []).length" description="暂无训练指标" :image-size="64" />
            </template>
            <template v-else-if="task.job_kind === 'evaluation'">
              <div class="source-metric-header">
                <strong>来源训练 Loss</strong>
                <span v-if="sourceTask">{{ sourceTask.name }}</span>
              </div>
              <div v-if="sourceTaskLoading" v-loading="true" class="source-metric-loading" />
              <el-alert v-else-if="sourceTaskError" type="error" :closable="false" title="来源训练指标加载失败" />
              <template v-else>
                <div ref="chartElement" class="metric-chart" />
                <el-empty v-if="!chartMetrics.length" description="来源训练任务未记录 Loss" :image-size="64" />
              </template>
            </template>
          </el-tab-pane>
          <el-tab-pane label="日志" name="logs">
            <div class="log-header"><span>已载入 {{ logs.length }} 行</span><span>游标 {{ nextAfterLine }}</span></div>
            <pre class="logs">{{ logs.join('\n') || '暂无公开日志' }}</pre>
          </el-tab-pane>
          <el-tab-pane label="产物" name="artifacts">
            <el-table :data="task.artifacts" row-key="id" size="small">
              <el-table-column prop="id" label="Artifact ID" min-width="230" show-overflow-tooltip />
              <el-table-column prop="artifact_type" label="类型" width="130" />
              <el-table-column label="大小" width="110"><template #default="{ row }">{{ formatBytes(row.size_bytes) }}</template></el-table-column>
              <el-table-column label="操作" width="70"><template #default="{ row }"><el-tooltip content="下载产物" placement="top"><el-button link type="primary" :icon="Download" aria-label="下载产物" @click="downloadArtifact(row.id)" /></el-tooltip></template></el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </template>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { Document, Download, Refresh } from '@element-plus/icons-vue'
import { downloadTrainingArtifactUrl, getTrainingTask, getTrainingTaskLogs } from '@/api/modelTraining'
import { statusLabel, statusType, validLossMetrics } from '../trainingForm'
import { createLatestRequestState, createSerialPoller } from '../trainingUiState'

const MAX_LOG_LINES = 2000
const props = defineProps({ modelValue: Boolean, taskId: { type: String, default: '' } })
const emit = defineEmits(['update:modelValue', 'changed'])
const visible = computed({ get: () => props.modelValue, set: value => emit('update:modelValue', value) })
const task = ref(null)
const sourceTask = ref(null)
const sourceTaskLoading = ref(false)
const sourceTaskError = ref('')
const loading = ref(false)
const activeTab = ref('overview')
const logs = ref([])
const nextAfterLine = ref(0)
const chartElement = ref(null)
const descriptionColumns = ref(window.innerWidth <= 640 ? 1 : 3)
const chartMetrics = computed(() => validLossMetrics(
  task.value?.job_kind === 'evaluation'
    ? sourceTask.value?.metrics
    : task.value?.metrics,
))
let chart = null
let drawerToken = null
let activeLogToken = null
const drawerRequests = createLatestRequestState()
const detailRequests = createLatestRequestState()
const sourceDetailRequests = createLatestRequestState()
const logRequests = createLatestRequestState()
const drawerPoller = createSerialPoller({
  delay: 2000,
  async run() {
    const lifecycleToken = drawerToken
    if (!lifecycleToken) return
    await loadDetailUpdate(lifecycleToken)
    await pollLogs(lifecycleToken)
  },
  onError: handleBackgroundError,
})

watch(() => props.taskId, () => { if (visible.value) restartDrawer() })
watch(() => props.modelValue, open => { if (!open) cleanupDrawer() })
watch(activeTab, name => { if (name === 'metrics') nextTick(renderChart) })

function handleBackgroundError(error) { void error }
function isDrawerCurrent(lifecycleToken) { return drawerRequests.isLatest(lifecycleToken) && visible.value }
function openDrawer() { beginDrawer().catch(handleBackgroundError) }
function refreshTask() { if (drawerToken) loadTask(drawerToken).catch(handleBackgroundError) }
function refreshLogs() { if (drawerToken) pollLogs(drawerToken).catch(handleBackgroundError) }
function restartDrawer() {
  cleanupData()
  openDrawer()
}
async function beginDrawer() {
  if (!props.taskId || !visible.value) return
  drawerToken = drawerRequests.start()
  window.addEventListener('resize', handleResize)
  handleResize()
  try {
    await loadTask(drawerToken)
  } catch (error) {
    handleBackgroundError(error)
  }
  if (isDrawerCurrent(drawerToken) && !isTerminal(task.value?.state)) drawerPoller.start()
}
async function loadTask(lifecycleToken) {
  if (!props.taskId || !isDrawerCurrent(lifecycleToken)) return false
  const requestToken = detailRequests.start()
  loading.value = true
  try {
    const result = await getTrainingTask(props.taskId)
    if (!isDrawerCurrent(lifecycleToken) || !detailRequests.isLatest(requestToken)) return false
    task.value = result
    await loadSourceTask(lifecycleToken, result, true)
    await nextTick()
    if (!isDrawerCurrent(lifecycleToken) || !detailRequests.isLatest(requestToken)) return false
    renderChart()
    await pollLogs(lifecycleToken)
    return true
  } finally {
    if (detailRequests.isLatest(requestToken)) loading.value = false
  }
}
async function loadDetailUpdate(lifecycleToken) {
  if (!props.taskId || !isDrawerCurrent(lifecycleToken)) return false
  const requestToken = detailRequests.start()
  const result = await getTrainingTask(props.taskId)
  if (!isDrawerCurrent(lifecycleToken) || !detailRequests.isLatest(requestToken)) return false
  task.value = result
  await loadSourceTask(lifecycleToken, result)
  await nextTick()
  renderChart()
  if (isTerminal(result.state)) drawerPoller.stop()
  return true
}
async function pollLogs(lifecycleToken) {
  if (!props.taskId || !isDrawerCurrent(lifecycleToken) || activeLogToken !== null) return false
  const requestToken = logRequests.start()
  activeLogToken = requestToken
  try {
    let hasMore = true
    while (hasMore && isDrawerCurrent(lifecycleToken) && logRequests.isLatest(requestToken)) {
      try {
        const result = await getTrainingTaskLogs(props.taskId, nextAfterLine.value)
        if (!isDrawerCurrent(lifecycleToken) || !logRequests.isLatest(requestToken)) return false
        logs.value = [...logs.value, ...(result.lines || [])].slice(-MAX_LOG_LINES)
        nextAfterLine.value = result.next_after_line
        hasMore = Boolean(result.has_more)
      } catch (error) {
        if (error?.response?.status !== 404) throw error
        hasMore = false
      }
    }
    return true
  } finally {
    if (activeLogToken === requestToken) activeLogToken = null
  }
}
async function loadSourceTask(lifecycleToken, currentTask, force = false) {
  const parentTaskId = currentTask?.job_kind === 'evaluation' ? currentTask.parent_task_id : ''
  if (!parentTaskId) {
    sourceDetailRequests.invalidate()
    sourceTask.value = null
    sourceTaskLoading.value = false
    sourceTaskError.value = ''
    return false
  }
  if (!force && sourceTask.value?.id === parentTaskId) return true
  const requestToken = sourceDetailRequests.start()
  disposeChart()
  sourceTask.value = null
  sourceTaskLoading.value = true
  sourceTaskError.value = ''
  try {
    const result = await getTrainingTask(parentTaskId)
    if (!isDrawerCurrent(lifecycleToken) || !sourceDetailRequests.isLatest(requestToken)) return false
    sourceTask.value = result
    return true
  } catch (error) {
    if (isDrawerCurrent(lifecycleToken) && sourceDetailRequests.isLatest(requestToken)) {
      sourceTaskError.value = '来源训练指标加载失败'
    }
    return false
  } finally {
    if (sourceDetailRequests.isLatest(requestToken)) sourceTaskLoading.value = false
  }
}
function renderChart() {
  if (!task.value || !['training', 'evaluation'].includes(task.value.job_kind)) {
    disposeChart()
    return
  }
  if (task.value.job_kind === 'evaluation' && (
    sourceTaskLoading.value || sourceTaskError.value || !chartMetrics.value.length
  )) {
    disposeChart()
    return
  }
  if (!chartElement.value || !task.value) return
  if (!chart) chart = echarts.init(chartElement.value)
  const evaluation = task.value.job_kind === 'evaluation'
  const metrics = evaluation ? chartMetrics.value : (task.value.metrics || [])
  const series = [{ name: 'Loss', type: 'line', smooth: true, connectNulls: true, data: metrics.map(item => item.loss) }]
  if (!evaluation) {
    series.push({ name: 'Eval Loss', type: 'line', smooth: true, connectNulls: true, data: metrics.map(item => item.eval_loss) })
  }
  chart.setOption({ tooltip: { trigger: 'axis' }, legend: { data: series.map(item => item.name) }, grid: { left: 52, right: 22, top: 42, bottom: 42 }, xAxis: { type: 'category', name: 'Step', data: metrics.map(item => item.step ?? '-') }, yAxis: { type: 'value', name: 'Loss', scale: true }, series }, true)
  chart.resize()
}
function handleResize() {
  descriptionColumns.value = window.innerWidth <= 640 ? 1 : 3
  chart?.resize()
}
function disposeChart() { if (chart) { chart.dispose(); chart = null } }
function cleanupData() {
  drawerPoller.stop()
  drawerRequests.invalidate()
  detailRequests.invalidate()
  sourceDetailRequests.invalidate()
  logRequests.invalidate()
  drawerToken = null
  activeLogToken = null
  task.value = null
  sourceTask.value = null
  sourceTaskLoading.value = false
  sourceTaskError.value = ''
  logs.value = []
  nextAfterLine.value = 0
  window.removeEventListener('resize', handleResize)
  disposeChart()
}
function cleanupDrawer() { cleanupData(); activeTab.value = 'overview'; loading.value = false }
function downloadArtifact(id) { window.open(downloadTrainingArtifactUrl(id), '_blank') }
function isTerminal(state) { return ['cancelled', 'succeeded', 'failed', 'interrupted'].includes(state) }
function metricValue(value) { return value == null ? '-' : Number(value).toFixed(4) }
function formatDateTime(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
function formatBytes(value) { if (value == null) return '-'; if (value < 1024) return `${value} B`; if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`; return `${(value / 1048576).toFixed(1)} MB` }
function timelineLabel(name) { return ({ queued: '进入队列', started: '开始执行', heartbeat: '最近心跳', cancel_requested: '请求取消', succeeded: '执行成功', failed: '执行失败', cancelled: '已取消', interrupted: '执行中断' })[name] || name }

onBeforeUnmount(() => cleanupDrawer())
</script>

<style scoped>
.drawer-body { min-height: 360px; }
.drawer-toolbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.drawer-toolbar > div { display: flex; align-items: center; gap: 8px; }
h4 { margin: 18px 0 10px; font-size: 14px; }
.timeline { padding: 4px 10px; }
.config, .logs { margin: 0; padding: 12px; background: #111827; color: #d1d5db; border-radius: 4px; overflow: auto; font: 12px/1.6 ui-monospace, SFMono-Regular, Consolas, monospace; }
.logs { height: 460px; white-space: pre-wrap; overflow-wrap: anywhere; }
.log-header { display: flex; justify-content: space-between; margin-bottom: 8px; color: #6b7280; font-size: 12px; }
.metric-chart { width: 100%; height: 390px; }
.source-metric-header { display: flex; align-items: baseline; gap: 10px; margin: 4px 0 12px; }
.source-metric-header span { min-width: 0; overflow: hidden; color: #909399; font-size: 13px; text-overflow: ellipsis; white-space: nowrap; }
.source-metric-loading { min-height: 180px; }
@media (max-width: 640px) {
  .drawer-toolbar { align-items: flex-start; flex-wrap: wrap; }
  .drawer-toolbar > div:first-child { min-width: 0; flex-wrap: wrap; }
  .source-metric-header { align-items: flex-start; flex-direction: column; gap: 4px; }
  .logs { height: 55vh; }
}
</style>
