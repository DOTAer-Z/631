<template>
  <div class="virtual-page">
    <el-card shadow="never" class="intro-card">
      <template #header>
        <div class="card-header">
          <div>
            <div class="page-heading">Euler 虚拟样机</div>
            <div class="page-subtitle">通过 Euler fault-agent 生成故障、正常和时间线加速样本</div>
          </div>
          <div class="header-actions">
            <el-tag :type="agentOnline ? 'success' : 'danger'">
              {{ agentOnline ? 'Agent 在线' : 'Agent 离线' }}
            </el-tag>
            <el-button size="small" :loading="loading" @click="loadAll">
              <el-icon><Refresh /></el-icon>刷新
            </el-button>
          </div>
        </div>
      </template>
      <el-alert
        v-if="loadError"
        type="warning"
        :closable="false"
        :title="loadError"
      />
      <el-alert
        v-if="!agentOnline"
        type="info"
        :closable="false"
        title="Agent 离线：十类故障和操作按钮仍会显示，但任务操作暂时不可用。启动 Euler fault-agent 后点击刷新。"
      />
      <div class="safety-note">
        故障动作只在 Euler 虚拟机内执行；本页面和 631 后端只负责受限 HTTP 调用。合成故障会明确标记为模拟证据。
      </div>
    </el-card>

    <el-card shadow="never" class="catalog-card">
      <template #header>
        <div class="card-header">
          <span>故障类型与生成操作</span>
          <span class="muted">共 {{ faultRows.length }} 类</span>
        </div>
      </template>
      <el-table v-loading="loading" :data="faultRows" stripe border>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column label="故障类型" min-width="220">
          <template #default="{ row }">
            <div class="fault-name">{{ row.label }}</div>
            <div class="fault-meta">
              {{ row.injection_class }}<span v-if="row.injection_class === 'synthetic'"> / simulated</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="能力" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="row.supported ? 'success' : 'warning'">
              {{ row.supported ? '可执行' : '需检查' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="生成操作" min-width="410">
          <template #default="{ row }">
            <el-button size="small" type="danger" :disabled="!row.supported || row.busy" :loading="row.action === 'fault'" @click="createJob(row, 'fault')">故障生成</el-button>
            <el-button size="small" type="success" :disabled="!row.supported || row.busy" :loading="row.action === 'normal'" @click="createJob(row, 'normal')">正常生成</el-button>
            <el-button size="small" type="warning" :disabled="!row.baselineJobId || row.busy" :loading="row.action === 'accelerated'" @click="replayJob(row)">加速生成</el-button>
          </template>
        </el-table-column>
        <el-table-column label="任务操作" min-width="300">
          <template #default="{ row }">
            <el-button size="small" :disabled="!row.latestJobId" @click="viewJob(row.latestJobId)">查看任务</el-button>
            <el-button size="small" type="danger" plain :disabled="!row.runningJobId" @click="cancelJob(row.runningJobId)">停止任务</el-button>
            <el-button size="small" type="primary" plain :disabled="!row.completedJobId" @click="downloadArtifact(row.completedJobId)">下载产物</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!loading && !faultRows.length" description="暂无故障目录" />
    </el-card>

    <el-card shadow="never" class="history-card">
      <template #header>
        <div class="card-header">
          <span>最近任务</span>
          <el-button text size="small" @click="loadJobs">刷新任务</el-button>
        </div>
      </template>
      <el-table :data="recentJobs" size="small">
        <el-table-column prop="job_id" label="任务 ID" width="190" />
        <el-table-column label="类型" width="170">
          <template #default="{ row }">{{ row.sample_class === 'normal' ? '正常' : row.execution_mode === 'accelerated_replay' ? '加速故障' : '故障' }} / {{ row.fault_type }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="130">
          <template #default="{ row }"><el-tag size="small" :type="statusType(row.status)">{{ statusLabel(row.status) }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" min-width="240">
          <template #default="{ row }">
            <el-button size="small" @click="viewJob(row.job_id)">查看任务</el-button>
            <el-button size="small" type="primary" plain :disabled="!isCompleted(row)" @click="downloadArtifact(row.job_id)">下载产物</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-drawer v-model="drawerVisible" title="虚拟样机任务详情" size="520px">
      <el-descriptions v-if="selectedJob" :column="1" border size="small">
        <el-descriptions-item label="任务 ID">{{ selectedJob.job_id }}</el-descriptions-item>
        <el-descriptions-item label="故障类型">{{ requestedFaultType(selectedJob) }}<template v-if="executedFaultType(selectedJob) !== requestedFaultType(selectedJob)"> → {{ executedFaultType(selectedJob) }}</template></el-descriptions-item>
        <el-descriptions-item label="样本类型">{{ selectedJob.job?.sample_class === 'normal' ? '正常' : '故障' }}</el-descriptions-item>
        <el-descriptions-item label="执行模式">{{ selectedJob.job?.execution_mode || selectedJob.summary?.execution_mode || 'baseline' }}</el-descriptions-item>
        <el-descriptions-item label="状态"><el-tag :type="statusType(selectedJob.status)">{{ statusLabel(selectedJob.status) }}</el-tag></el-descriptions-item>
        <el-descriptions-item label="检查点阶段">{{ selectedJob.job?.checkpoint_phase || '-' }}</el-descriptions-item>
        <el-descriptions-item label="加速倍率">{{ selectedJob.summary?.acceleration_factor || '-' }}</el-descriptions-item>
        <el-descriptions-item label="墙钟耗时">{{ selectedJob.summary?.wall_duration_seconds || '-' }}</el-descriptions-item>
        <el-descriptions-item label="错误">{{ selectedJob.error || '-' }}</el-descriptions-item>
      </el-descriptions>
      <el-divider content-position="left">事件</el-divider>
      <el-timeline v-if="events.length">
        <el-timeline-item v-for="(event, index) in events" :key="`${event.timestamp}-${index}`" :timestamp="event.timestamp">
          {{ event.stage }} · {{ event.status }} · {{ event.message }}
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="暂无事件" />
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  cancelVirtualPrototypeJob,
  createVirtualPrototypeJob,
  downloadVirtualPrototypeArtifact,
  getVirtualPrototypeCapabilities,
  getVirtualPrototypeEvents,
  getVirtualPrototypeHealth,
  getVirtualPrototypeJob,
  listVirtualPrototypeJobs,
  replayVirtualPrototypeJob,
} from '@/api/virtualPrototype'
import { executedFaultType, requestedFaultType } from '@/utils/eulerJobState.mjs'

// Agent 离线时仍展示固定目录，避免页面结构依赖网络状态；上线后由实时 capabilities 覆盖。
const FALLBACK_FAULT_CATALOG = [
  { code: 'F1101', label: 'CPU core temperature exceeded', injection_class: 'synthetic' },
  { code: 'F1206', label: 'Process/thread scheduling anomaly', injection_class: 'native_bounded' },
  { code: 'F2301', label: 'Container CPU quota throttling', injection_class: 'native' },
  { code: 'F3409', label: 'Hardware interrupt line surge', injection_class: 'synthetic' },
  { code: 'F4512', label: 'Sustained high system load', injection_class: 'native' },
  { code: 'F4620', label: 'Available memory exhaustion', injection_class: 'native_bounded' },
  { code: 'F5106', label: 'UDP checksum error', injection_class: 'native_if_supported' },
  { code: 'F5203', label: 'Listening port count expansion', injection_class: 'native' },
  { code: 'F5313', label: 'TCP connection failure', injection_class: 'native' },
  { code: 'F5402', label: 'Established TCP connection expansion', injection_class: 'native' },
]

const loading = ref(false)
const agentOnline = ref(false)
const loadError = ref('')
const catalog = ref(FALLBACK_FAULT_CATALOG)
const capabilities = ref({})
const jobs = ref([])
const localJobIds = ref(JSON.parse(localStorage.getItem('virtual-prototype-job-ids') || '[]'))
const drawerVisible = ref(false)
const selectedJob = ref(null)
const events = ref([])
let pollTimer = null

const faultRows = computed(() => catalog.value.map((item) => {
  const related = jobs.value.filter((job) => requestedFaultType(job) === item.code)
  const baseline = related.find((job) => job.job?.sample_class !== 'normal' && job.job?.execution_mode === 'baseline' && isCompleted(job))
    const running = related.find((job) => ['queued', 'preparing', 'running', 'finalizing', 'cancel_requested'].includes(job.status))
    const latest = related[0]
  const completed = hasDownloadableArtifact(latest) ? latest : undefined
  return {
    ...item,
    supported: agentOnline.value && (capabilities.value[item.code] || []).every((cap) => cap.status !== 'unsupported'),
    baselineJobId: baseline?.job_id || '',
    latestJobId: related[0]?.job_id || '',
    runningJobId: running?.job_id || '',
    completedJobId: completed?.job_id || '',
    busy: Boolean(running),
    action: '',
  }
}))
const recentJobs = computed(() => jobs.value.slice(0, 20))

function hasDownloadableArtifact(job) {
  return ['succeeded', 'acceleration_target_not_met'].includes(job?.status)
    && !!job?.artifact_path && !job?.artifact_error
}
function isCompleted(job) { return hasDownloadableArtifact(job) }
function statusType(status) { return status === 'succeeded' ? 'success' : ['failed', 'capability_failed'].includes(status) ? 'danger' : ['queued', 'preparing', 'running', 'finalizing', 'cancel_requested'].includes(status) ? 'warning' : 'info' }
function statusLabel(status) { return ({ queued: '排队中', preparing: '准备中', running: '执行中', finalizing: '收尾中', succeeded: '已完成', partial_failed: '部分失败', failed: '失败', capability_failed: '能力不足', cancelled: '已取消', cancel_requested: '请求停止', acceleration_target_not_met: '未达到加速目标', dry_run: '预演完成' })[status] || status || '未知' }
function rememberJob(jobId) {
  if (!jobId) return
  localJobIds.value = [jobId, ...localJobIds.value.filter((id) => id !== jobId)].slice(0, 50)
  localStorage.setItem('virtual-prototype-job-ids', JSON.stringify(localJobIds.value))
}

async function loadJobs() {
  try {
    const data = await listVirtualPrototypeJobs({ limit: 100, offset: 0 })
    jobs.value = data.jobs || []
  } catch (error) {
    // A task-list timeout does not invalidate a successful /healthz result.
    // Retain the last known jobs so the UI cannot regress to an empty/old view.
  }
}

async function loadAll() {
  loading.value = true
  loadError.value = ''
  let healthy = false
  let healthError = null
  try {
    await getVirtualPrototypeHealth()
    healthy = true
  } catch (error) { healthError = error }
  agentOnline.value = healthy
  if (healthy) {
    try {
      const data = await getVirtualPrototypeCapabilities()
      const allowedCodes = new Set(FALLBACK_FAULT_CATALOG.map(item => item.code))
      catalog.value = (data.fault_catalog || (data.fault_types || []).map((code) => ({ code, label: code, injection_class: 'unknown' }))).filter(item => allowedCodes.has(item.code))
      capabilities.value = data.capabilities || {}
    } catch (error) {
      loadError.value = `Euler fault-agent 能力读取失败：${error.response?.data?.detail || error.message || error}`
    }
    await loadJobs()
  } else {
    catalog.value = FALLBACK_FAULT_CATALOG
    capabilities.value = {}
    loadError.value = `Euler fault-agent 不可用：${healthError?.response?.data?.detail || healthError?.message || healthError || 'healthz 请求失败'}`
  }
  loading.value = false
}

async function createJob(row, sampleClass) {
  try {
    const data = await createVirtualPrototypeJob({ fault_type: row.code, sample_class: sampleClass, sample_count: 1, duration_seconds: 10, seed: 20261008 })
    rememberJob(data.job_id)
    ElMessage.success(`${sampleClass === 'normal' ? '正常' : '故障'}任务已提交：${data.job_id}`)
    await loadJobs()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.message || '任务提交失败')
  }
}

async function replayJob(row) {
  if (!row.baselineJobId) return
  try {
    const data = await replayVirtualPrototypeJob(row.baselineJobId, { sample_count: 1, seed: 20261008, duration_seconds: 10 })
    rememberJob(data.job_id)
    ElMessage.success(`加速任务已提交：${data.job_id}`)
    await loadJobs()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.message || '加速任务提交失败')
  }
}

async function viewJob(jobId) {
  try {
    selectedJob.value = await getVirtualPrototypeJob(jobId)
    const data = await getVirtualPrototypeEvents(jobId)
    events.value = data.events || []
    drawerVisible.value = true
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.message || '任务查询失败')
  }
}

async function cancelJob(jobId) {
  try {
    await ElMessageBox.confirm('将向 Euler agent 发出停止请求，底层系统调用可能不会立即中断。继续吗？', '停止任务', { type: 'warning' })
    await cancelVirtualPrototypeJob(jobId)
    ElMessage.success('已发出停止请求')
    await loadJobs()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.response?.data?.detail || error.message || '停止请求失败')
  }
}

async function downloadArtifact(jobId) {
  try {
    const blob = await downloadVirtualPrototypeArtifact(jobId)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${jobId}.tar.gz`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || error.message || '产物下载失败')
  }
}

onMounted(() => {
  loadAll()
  pollTimer = window.setInterval(loadJobs, 3000)
})
onUnmounted(() => window.clearInterval(pollTimer))
</script>

<style scoped>
.virtual-page { display: flex; flex-direction: column; gap: 16px; }
.card-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.header-actions { display: flex; align-items: center; gap: 10px; }
.page-heading { font-size: 18px; font-weight: 650; color: #1e293b; }
.page-subtitle, .muted, .fault-meta { color: #64748b; font-size: 12px; margin-top: 4px; }
.safety-note { color: #475569; font-size: 13px; line-height: 1.7; margin-top: 12px; }
.fault-name { font-weight: 600; color: #1e293b; }
.history-card { margin-bottom: 12px; }
</style>
