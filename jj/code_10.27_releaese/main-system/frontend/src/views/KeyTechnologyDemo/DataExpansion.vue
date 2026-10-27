<template>
  <div class="demo-page">
    <el-card shadow="never">
      <template #header>
        <div class="head">
          <div><div class="title">增广数据样本</div><div class="sub">Euler 十类故障数据生成</div></div>
          <el-tag :type="online ? 'success' : 'danger'">{{ online ? 'Agent 在线' : 'Agent 离线' }}</el-tag>
        </div>
      </template>
    </el-card>
    <el-card shadow="never">
      <template #header><div class="head"><span>故障数据生成</span><el-button size="small" :loading="loading" @click="load">刷新</el-button></div></template>
      <el-table :data="rows" stripe border>
        <el-table-column prop="code" label="代码" width="90" />
        <el-table-column prop="label" label="故障类型" min-width="310" />
        <el-table-column label="能力" width="135">
          <template #default="{ row }"><el-tag size="small" :type="row.supported ? 'success' : 'info'">{{ row.capabilityLabel }}</el-tag></template>
        </el-table-column>
        <el-table-column label="生成与任务操作" min-width="500">
          <template #default="{ row }">
            <el-select v-model="generationModes[row.code]" size="small" class="generation-mode" aria-label="故障数据生成方式" @change="onGenerationModeChange(row, $event)">
              <el-option label="注入生成" value="native" />
              <el-option label="算法生成" value="local_file" />
            </el-select>
            <el-tooltip :content="row.mode === 'local_file' ? '重新选择本地文件；读取和校验不依赖 Agent 在线' : !online ? 'Agent 离线' : guestBusy ? 'Agent 正在执行其他任务' : !row.supported ? '当前故障能力不可用' : '一次生成 15 个样本，不自动入库'">
              <span><el-button type="danger" size="small" :disabled="row.mode === 'local_file' ? false : !online || !row.supported || guestBusy" :loading="submitting === row.code" @click="generate(row)">故障数据生成</el-button></span>
            </el-tooltip>
            <el-button size="small" :disabled="!row.latest" @click="view(row.latest)">查看任务</el-button>
            <el-button type="danger" plain size="small" :disabled="!online || !row.running" @click="cancel(row.running)">停止任务</el-button>
            <el-button type="primary" plain size="small" :disabled="!online || !row.completed" @click="download(row.completed)">下载产物</el-button>
            <div v-if="row.latest" class="sub">当前任务：{{ row.latest }}</div>
          </template>
        </el-table-column>
        <el-table-column label="保存到数据库（当前任务）" min-width="265">
          <template #default="{ row }">
            <el-tooltip :content="!online ? 'Agent 离线' : '仅保存此任务，重复保存不会增加记录'">
              <span><el-button type="success" size="small" :disabled="!canSave(row.completed)" :loading="isSaving(row.completed)" @click="save(row.completed)">{{ saveLabel(row.completed) }}</el-button></span>
            </el-tooltip>
            <el-button v-if="importState(row.completed) === 'success'" size="small" link type="primary" @click="goToFiles(row.completed)">前往在线处理</el-button>
          </template>
        </el-table-column>
      </el-table>
      <input ref="localFileInput" class="local-file-input" type="file" aria-label="选择本地故障文件（不限扩展名）" @change="onLocalFileChange" />
    </el-card>
    <section class="analysis-section">
      <div class="head analysis-heading">
        <span>大模型分析</span>
        <div class="analysis-actions">
          <el-button type="primary" :icon="DataAnalysis" :loading="startingAnalysis" :disabled="analysisBusy || !analysis?.pending" @click="analyze">大模型分析</el-button>
          <el-button :icon="Refresh" circle aria-label="刷新分析结果" title="刷新分析结果" :loading="analysisLoading" @click="refreshAnalysis" />
        </div>
      </div>
      <el-alert v-if="analysisError" type="error" :closable="false" :title="analysisError" />
      <div v-if="analysis?.batch" class="batch-progress">
        <span>{{ analysisState(analysis.batch.state) }} · {{ analysis.batch.processed }} / {{ analysis.batch.total }} · 本批失败 {{ analysis.batch.failed }} · {{ analysis.batch.model || '-' }}</span>
        <el-progress :percentage="analysis.batch.total ? Math.round(100 * analysis.batch.processed / analysis.batch.total) : 0" :status="analysis.batch.state === 'completed' ? 'success' : ['partial_failed', 'interrupted'].includes(analysis.batch.state) ? 'exception' : undefined" />
        <el-alert v-if="analysis.batch.error_message" type="warning" :closable="false" :title="analysis.batch.error_message" />
      </div>
      <div class="head diagnosis-heading">
        <span>逐文件诊断结果</span>
        <div class="diagnosis-accuracy">
          <span>诊断正确率：{{ percent(analysis?.overall?.accuracy) }}<template v-if="analysis?.overall?.evaluated">（{{ analysis.overall.correct }} / {{ analysis.overall.evaluated }}）</template></span>
        </div>
      </div>
      <el-table :data="analysis?.items || []" stripe>
        <el-table-column prop="name" label="文件" min-width="230" />
        <el-table-column label="来源" min-width="120"><template #default="{ row }">{{ evidenceLabel(row.evidence_mode) }}</template></el-table-column>
        <el-table-column prop="true_label" label="真实类型" min-width="180" />
        <el-table-column label="预测类型" min-width="180"><template #default="{ row }">{{ row.predicted_label || '-' }}</template></el-table-column>
        <el-table-column label="诊断方式" min-width="160"><template #default="{ row }">{{ diagnosisModeLabel(row.diagnosis_mode) }}</template></el-table-column>
        <el-table-column label="结果" width="110"><template #default="{ row }"><el-tag :type="row.correct === true ? 'success' : row.correct === false ? 'danger' : 'info'">{{ row.state !== 'success' ? analysisState(row.state) : row.correct === null ? '无法评估' : row.correct ? '正确' : '错误' }}</el-tag></template></el-table-column>
        <el-table-column label="操作" width="100"><template #default="{ row }"><el-button link type="primary" @click="analysisDetail = row; analysisDrawer = true">查看结果</el-button></template></el-table-column>
      </el-table>
      <el-pagination class="analysis-pagination" layout="prev, pager, next, total" :total="analysis?.total || 0" :page-size="20" v-model:current-page="analysisPage" @current-change="refreshAnalysis" />
    </section>
    <el-card shadow="never">
      <template #header>最近任务 · 手动保存产物</template>
      <el-table :data="expansionJobs" stripe>
        <el-table-column prop="job_id" label="任务 ID" min-width="210" />
        <el-table-column label="实际故障" min-width="260"><template #default="{ row }">{{ executionLabel(row) }}</template></el-table-column>
        <el-table-column label="生成状态" width="110"><template #default="{ row }">{{ jobStatusLabel(row.status) }}</template></el-table-column>
        <el-table-column label="样本" width="150"><template #default="{ row }">{{ sampleProgress(row) }}</template></el-table-column>
        <el-table-column label="入库状态" min-width="180">
          <template #default="{ row }">
            <el-tooltip v-if="imports[row.job_id]?.error_message" :content="imports[row.job_id].error_message"><el-tag type="danger">保存失败（悬停查看原因）</el-tag></el-tooltip>
            <span v-else>{{ stateLabel(row.job_id) }}</span>
            <div v-if="imports[row.job_id]?.result" class="sub">{{ imports[row.job_id].result.sample_count }} 份样本 / {{ imports[row.job_id].result.run_count }} 条运行记录 / {{ imports[row.job_id].result.entry_count }} 条日志</div>
          </template>
        </el-table-column>
        <el-table-column label="操作" min-width="390">
          <template #default="{ row }">
            <el-button size="small" @click="view(row.job_id)">查看任务</el-button>
            <el-button size="small" :disabled="!online || !hasArtifact(row)" @click="download(row.job_id)">下载产物</el-button>
            <el-button type="success" size="small" :disabled="!canSave(row.job_id)" :loading="isSaving(row.job_id)" @click="save(row.job_id)">{{ saveLabel(row.job_id) }}</el-button>
            <el-button v-if="importState(row.job_id) === 'success'" size="small" link type="primary" @click="goToFiles(row.job_id)">前往在线处理</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
    <el-drawer v-model="analysisDrawer" title="诊断结果" size="min(640px, 100vw)">
      <el-descriptions v-if="analysisDetail" :column="1" border>
        <el-descriptions-item label="文件">{{ analysisDetail.name }}</el-descriptions-item>
        <el-descriptions-item label="真实类型">{{ analysisDetail.true_label }}</el-descriptions-item>
        <el-descriptions-item label="预测类型">{{ analysisDetail.predicted_label || '-' }}</el-descriptions-item>
        <el-descriptions-item label="诊断来源">{{ analysisDetail.source === 'online' ? '已有在线诊断' : analysisDetail.source === 'batch' ? '批量大模型分析' : '-' }}</el-descriptions-item>
        <el-descriptions-item label="诊断方式">{{ diagnosisModeLabel(analysisDetail.diagnosis_mode) }}</el-descriptions-item>
        <el-descriptions-item label="模型">{{ analysisDetail.model || '历史记录未保存模型名称' }}</el-descriptions-item>
        <el-descriptions-item label="置信度">{{ analysisDetail.prediction?.confidence ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="诊断依据">{{ analysisDetail.prediction?.reasoning || '-' }}</el-descriptions-item>
        <el-descriptions-item label="根因">{{ analysisDetail.prediction?.root_cause || '-' }}</el-descriptions-item>
        <el-descriptions-item label="输入日志">{{ analysisDetail.input_info?.line_count !== undefined ? `${analysisDetail.input_info.line_count} 条原始日志，模型输入 ${analysisDetail.input_info.input_characters} 字符${analysisDetail.input_info.truncated ? '（已精简）' : ''}` : '-' }}</el-descriptions-item>
        <el-descriptions-item v-if="analysisDetail.input_info?.request_estimated_tokens_upper_bound" label="输入预算">{{ analysisDetail.input_info.request_estimated_tokens_upper_bound }} token 保守估算上限 / {{ analysisDetail.input_info.request_token_budget }}（非精确分词）</el-descriptions-item>
        <el-descriptions-item v-if="analysisDetail.input_info?.attempts" label="调用次数">{{ analysisDetail.input_info.attempts }}</el-descriptions-item>
        <el-descriptions-item label="错误">{{ analysisDetail.error_message || '-' }}</el-descriptions-item>
      </el-descriptions>
    </el-drawer>
    <el-drawer v-model="localFileDrawer" title="本地故障文件读取" size="min(720px, 100vw)">
      <p v-if="localFileFault">当前故障：{{ localFileFault.label }}（{{ localFileFault.code }}）</p>
      <p v-if="readingLocalFile">正在读取文件…</p>
      <el-alert v-if="localFileError" type="error" :closable="false" :title="localFileError" />
      <template v-if="localFilePreview">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="文件名">{{ localFilePreview.name }}</el-descriptions-item>
          <el-descriptions-item label="文件大小">{{ localFilePreview.size.toLocaleString() }} 字节</el-descriptions-item>
          <el-descriptions-item label="读取大小">{{ localFilePreview.readBytes.toLocaleString() }} 字节{{ localFilePreview.truncated ? '（部分预览）' : '' }}</el-descriptions-item>
          <el-descriptions-item label="编码">{{ localFilePreview.encoding }}</el-descriptions-item>
          <el-descriptions-item label="内容中的故障类型">{{ localFilePreview.codes.map(faultLabel).join('、') || '未识别' }}</el-descriptions-item>
          <el-descriptions-item v-if="localFileJob" label="生成任务">{{ localFileJob }}</el-descriptions-item>
        </el-descriptions>
        <el-alert class="file-match" :type="localFilePreview.status === 'matched' ? 'success' : localFilePreview.status === 'mismatch' ? 'error' : 'warning'" :closable="false" :title="localFilePreview.message" />
        <p class="note">文件只在浏览器中读取并校验故障代码，不执行其中的命令或脚本；匹配成功后会提交受控的 Agent 故障生成任务，产物仍需手动保存到数据库。</p>
        <pre class="local-file-preview">{{ localFilePreview.preview || '（空文件）' }}</pre>
      </template>
    </el-drawer>
    <el-drawer v-model="drawer" title="任务详情" size="600px">
      <el-descriptions v-if="selected" :column="1" border>
        <el-descriptions-item label="任务 ID">{{ selected.job_id }}</el-descriptions-item>
        <el-descriptions-item label="实际故障">{{ executionLabel(selected) }}</el-descriptions-item>
        <el-descriptions-item label="数据来源">{{ evidenceLabel(selected.evidence_mode) }}</el-descriptions-item>
        <el-descriptions-item v-if="selected.simulation_reason" label="模拟原因">{{ selected.simulation_reason }}</el-descriptions-item>
        <el-descriptions-item v-if="selected.missing_capabilities?.length" label="缺失能力">{{ selected.missing_capabilities.join('、') }}</el-descriptions-item>
        <el-descriptions-item label="样本统计">{{ sampleProgress(selected) }}</el-descriptions-item>
        <el-descriptions-item label="生成状态">{{ jobStatusLabel(selected.status) }}</el-descriptions-item>
        <el-descriptions-item label="错误">{{ selected.error || selected.artifact_error || '-' }}</el-descriptions-item>
        <el-descriptions-item label="入库状态">{{ stateLabel(selected.job_id) }}</el-descriptions-item>
        <el-descriptions-item v-if="imports[selected.job_id]?.error_message" label="保存失败原因">{{ imports[selected.job_id].error_message }}</el-descriptions-item>
      </el-descriptions>
      <p v-if="selected"><el-button type="success" :disabled="!canSave(selected.job_id)" :loading="isSaving(selected.job_id)" @click="save(selected.job_id)">{{ saveLabel(selected.job_id) }}</el-button><el-button v-if="importState(selected.job_id) === 'success'" @click="goToFiles(selected.job_id)">前往在线处理</el-button></p>
      <el-divider content-position="left">执行事件</el-divider>
      <el-timeline v-if="events.length"><el-timeline-item v-for="(event, index) in events" :key="index" :timestamp="event.timestamp">{{ event.stage }} · {{ event.status }} · {{ event.message }}</el-timeline-item></el-timeline>
      <el-empty v-else description="暂无事件" />
    </el-drawer>
  </div>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { DataAnalysis, Refresh } from '@element-plus/icons-vue'
import { FAULT_LABELS, EXECUTED_FAULT_LABELS, readLocalFaultFile } from '@/utils/localFaultFile.mjs'
import { automaticGenerationSelection } from '@/utils/eulerGeneration.mjs'
import { isActiveJob, requestedFaultType, executedFaultType } from '@/utils/eulerJobState.mjs'
import {
  cancelVirtualPrototypeJob, createVirtualPrototypeJob, downloadVirtualPrototypeArtifact,
  getVirtualPrototypeCapabilities, getVirtualPrototypeEvents, getVirtualPrototypeHealth,
  getVirtualPrototypeJob, listVirtualPrototypeJobs, listVirtualPrototypeImports, saveVirtualPrototypeArtifact,
  getExpansionAnalysis, startExpansionAnalysis,
} from '@/api/virtualPrototype'

const fallback = Object.entries(FAULT_LABELS).map(([code, label]) => ({ code, label }))
const router = useRouter()
const online = ref(false), loading = ref(false), catalog = ref(fallback), capabilities = ref({})
const generationModes = ref(Object.fromEntries(fallback.map(fault => [fault.code, 'native'])))
const alternatives = ref({})
const jobs = ref([]), submittedJobs = ref({}), imports = ref({}), submitting = ref(''), requestingSave = ref({})
const latestSubmitted = ref(loadLatestSubmitted())
const drawer = ref(false), selected = ref(null), events = ref([]), drawerJobId = ref('')
const localFileInput = ref(null), localFileDrawer = ref(false), localFileFault = ref(null)
const localFilePreview = ref(null), localFileError = ref(''), readingLocalFile = ref(false)
const localFileJob = ref('')
let localFileReadId = 0
const analysis = ref(null), analysisError = ref(''), analysisLoading = ref(false), startingAnalysis = ref(false)
const analysisPage = ref(1), analysisDrawer = ref(false), analysisDetail = ref(null)
const analysisBusy = computed(() => ['queued', 'running'].includes(analysis.value?.batch?.state))
const percent = value => value === null || value === undefined ? '-' : `${value}%`
const diagnosisModeLabel = value => ({ ground_truth_assisted: '批量诊断', logs_only: '日志诊断（历史）', existing: '已有诊断' }[value] || '-')
const evidenceLabel = value => ({ simulated: '模拟演示数据', observed: '真实注入数据' }[value] || '来源未知')
const analysisState = value => ({ queued: '排队中', running: '诊断中', pending: '待诊断', success: '已诊断', failed: '失败', completed: '全部完成', partial_failed: '部分失败', interrupted: '已中断' }[value] || value)
let timer, refreshing = false, disposed = false, detailRequestId = 0, detailInFlightId = ''
function loadLatestSubmitted() {
  if (typeof localStorage === 'undefined') return {}
  try {
    const value = JSON.parse(localStorage.getItem('euler-expansion-latest-jobs') || '{}')
    return value && typeof value === 'object' ? value : {}
  } catch { return {} }
}
function rememberSubmitted(code, jobId) {
  if (!code || !jobId) return
  latestSubmitted.value = { ...latestSubmitted.value, [code]: jobId }
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem('euler-expansion-latest-jobs', JSON.stringify(latestSubmitted.value))
  } catch { /* Browser storage restrictions must not hide an accepted job. */ }
}
function jobFaultType(job) { return executedFaultType(job) || requestedFaultType(job) }
function jobCreatedAt(job) {
  const value = job?.created_at || job?.job?.created_at || job?.summary?.created_at
  const time = value ? Date.parse(value) : NaN
  return Number.isFinite(time) ? time : 0
}
function newestJobs(items) {
  return [...items].sort((left, right) => {
    const time = jobCreatedAt(right) - jobCreatedAt(left)
    return time || String(right.job_id || '').localeCompare(String(left.job_id || ''))
  })
}
const knownJobs = computed(() => {
  // Keep the submission response until the Agent list includes it. This also
  // keeps the exact job ID available if the list omits requested-fault fields.
  const byId = new Map()
  for (const job of jobs.value) byId.set(job.job_id, job)
  for (const job of Object.values(submittedJobs.value)) byId.set(job.job_id, job)
  return newestJobs([...byId.values()])
})
const expansionJobs = computed(() => knownJobs.value.filter(job => !job.job?.sample_id && job.job?.execution_mode === 'baseline'))
const guestBusy = computed(() => readingLocalFile.value || !!submitting.value || knownJobs.value.some(job => isActiveJob(job.status)))
const hasArtifact = job => ['succeeded', 'acceleration_target_not_met'].includes(job?.status)
  && !!job.artifact_path && !job.artifact_error
const selectionFor = code => automaticGenerationSelection(code, capabilities.value, alternatives.value)
const capabilityAvailable = code => selectionFor(code).supported
const rows = computed(() => catalog.value.map(fault => {
  const matching = newestJobs(expansionJobs.value.filter(job => jobFaultType(job) === fault.code))
  const mode = generationModes.value[fault.code] || ''
  const supported = online.value && capabilityAvailable(fault.code)
  const rememberedId = latestSubmitted.value[fault.code]
  // Resolve the remembered submission by ID before filtering by fault type.
  const remembered = rememberedId
    ? knownJobs.value.find(job => job.job_id === rememberedId)
    : undefined
  const newest = matching[0]
  // A submission from this browser always wins for this row. A task that is
  // still running or failed must not fall back to an older successful task.
  const latest = rememberedId ? (remembered || { job_id: rememberedId }) : newest
  const completed = hasArtifact(latest) ? latest : undefined
  return {
    ...fault,
    mode,
    supported,
    capabilityLabel: !online.value ? 'Agent 离线' : !mode ? '请选择方式' : supported ? '可执行' : '能力不可用',
    running: isActiveJob(latest?.status) ? latest.job_id : undefined,
    latest: latest?.job_id,
    completed: completed?.job_id,
  }
}))
const faultLabel = code => EXECUTED_FAULT_LABELS[code] || code || '-'
const executionLabel = job => {
  const requested = requestedFaultType(job)
  const executed = executedFaultType(job) || requested
  return `${faultLabel(executed)}（${executed || '-'}）`
}
const sampleProgress = job => {
  const summary = job.summary || {}
  const total = summary.sample_total ?? job.job?.sample_count
  if (total === undefined) return '-'
  return `${summary.sample_succeeded ?? 0} 成功 / ${summary.sample_failed ?? 0} 失败 / ${total} 总数`
}
const jobStatusLabel = state => ({ queued: '排队中', preparing: '准备中', running: '执行中', finalizing: '正在归档', cancel_requested: '请求停止', succeeded: '生成成功', partial_failed: '部分失败', capability_failed: '能力不足', failed: '生成失败', cancelled: '已停止', interrupted: '已中断', acceleration_target_not_met: '未达到加速目标' }[state] || state || '未知')
const importState = id => imports.value[id]?.state || 'not_saved'
const isSaving = id => !!requestingSave.value[id] || ['pending', 'saving'].includes(importState(id))
const stateLabel = id => ({ not_saved: '未保存', pending: '等待保存', saving: '保存中', success: '已保存', failed: '保存失败' }[importState(id)])
const saveLabel = id => isSaving(id) ? '保存中' : importState(id) === 'success' ? '已保存' : importState(id) === 'failed' ? '重试保存' : '保存到数据库'
const jobForId = id => knownJobs.value.find(job => job.job_id === id)
const canSave = id => online.value && !!id && !isSaving(id) && importState(id) !== 'success'
  && jobForId(id)?.status === 'succeeded' && hasArtifact(jobForId(id))

async function refreshImports() {
  try {
    const result = await listVirtualPrototypeImports()
    if (disposed) return
    const next = Object.fromEntries((result.imports || []).map(item => [item.job_id, item]))
    for (const [id, item] of Object.entries(next)) {
      if (item.state === 'success' && ['pending', 'saving'].includes(importState(id))) ElMessage.success(`产物已保存：${item.result?.run_count || 0} 个文件，可在在线处理中选择`)
    }
    imports.value = next
  } catch { /* Retain the last known state during a transient backend outage. */ }
}
async function refreshJobs() {
  if (!online.value) return
  const listing = listVirtualPrototypeJobs({ limit: 100, offset: 0 }).then(result => {
    if (!disposed) jobs.value = result.jobs || []
  }).catch(() => { /* /healthz is the sole online signal. */ })
  // Exact-ID polling also works when the list is stale, incomplete or fails.
  // Once a detail response reaches a terminal state it needs no more polling.
  const details = [...new Set(Object.values(latestSubmitted.value).filter(Boolean))].filter(id => {
    const job = submittedJobs.value[id]
    return !job || !job.status || isActiveJob(job.status)
  }).map(async id => {
    try {
      const job = await getVirtualPrototypeJob(id)
      if (!disposed && job?.job_id === id) submittedJobs.value = { ...submittedJobs.value, [id]: job }
    } catch { /* Retain this job's state; never substitute a historical job. */ }
  })
  await Promise.all([listing, ...details])
}
async function refresh() {
  if (refreshing || disposed) return
  refreshing = true
  try { await Promise.all([refreshJobs(), refreshImports(), refreshAnalysis()]) }
  finally { refreshing = false }
}
async function load() {
  loading.value = true
  try {
    let healthy = false
    try {
      await getVirtualPrototypeHealth()
      healthy = true
    } catch { healthy = false }
    online.value = healthy
    if (healthy) {
      try {
        const result = await getVirtualPrototypeCapabilities()
        capabilities.value = result.capabilities || {}
        alternatives.value = result.alternatives || {}
        catalog.value = fallback
      } catch { /* capability timeout does not mean Agent offline */ }
    } else { catalog.value = fallback; capabilities.value = {}; alternatives.value = {} }
  } finally {
    loading.value = false
  }
  await refresh()
}
async function refreshAnalysis() {
  if (analysisLoading.value || disposed) return
  analysisLoading.value = true
  try {
    const result = await getExpansionAnalysis({ page: analysisPage.value, page_size: 20 })
    if (!disposed) { analysis.value = result; analysisError.value = '' }
  } catch (error) {
    if (!disposed) analysisError.value = error.response?.data?.detail || '分析结果加载失败'
  } finally { analysisLoading.value = false }
}
async function analyze() {
  try {
    await ElMessageBox.confirm(`使用在线处理当前启用的大模型分析 ${analysis.value?.pending || 0} 个待诊断文件？仅包含十类故障生成后手动保存的产物，已有诊断会跳过，可能产生模型调用费用。`, '大模型分析', { type: 'info' })
    startingAnalysis.value = true
    await startExpansionAnalysis()
    ElMessage.success('分析任务已提交，已诊断文件会跳过')
    await refreshAnalysis()
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.response?.data?.detail || '分析提交失败') }
  finally { startingAnalysis.value = false }
}
async function submitGeneration(row, expectedExecutedFaultType) {
  if (!online.value) throw new Error('Agent 离线，未提交生成任务')
  if (!capabilityAvailable(row.code)) throw new Error('当前故障能力不可用，未提交生成任务')
  if (submitting.value || knownJobs.value.some(job => isActiveJob(job.status))) {
    throw new Error('Agent 正在执行其他任务，未提交生成任务')
  }
  submitting.value = row.code
  try {
    const result = await createVirtualPrototypeJob({ fault_type: expectedExecutedFaultType || row.code, task_kind: 'augmentation', sample_class: 'fault', sample_count: 15, seed: 20261008 })
    rememberSubmitted(row.code, result.job_id)
    submittedJobs.value = { ...submittedJobs.value, [result.job_id]: {
      ...result, status: result.status || 'queued',
      job: { fault_type: expectedExecutedFaultType || row.code, execution_mode: 'baseline', ...result.job },
    } }
    ElMessage.success(`任务已提交：${result.job_id}；将生成 15 个样本，完成后可手动保存`)
    await refreshJobs()
    return result
  }
  finally { submitting.value = '' }
}

async function generate(row) {
  if (!row.mode) { ElMessage.warning('请选择注入生成或算法生成'); return }
  if (row.mode === 'local_file') {
    chooseLocalFile(row)
    return
  }
  try { await submitGeneration(row) }
  catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.response?.data?.detail || error.message || '任务提交失败') }
}
function onGenerationModeChange(row, mode) {
  if (mode === 'local_file') chooseLocalFile({ ...row, mode })
}
function chooseLocalFile(row) {
  ++localFileReadId
  readingLocalFile.value = false
  localFileFault.value = { code: row.code, label: row.label, row }
  localFilePreview.value = null
  localFileError.value = ''
  localFileJob.value = ''
  // Reset so selecting the same file again still fires change.
  localFileInput.value.value = ''
  localFileInput.value.click()
}
async function onLocalFileChange(event) {
  const file = event.target.files?.[0]
  if (!file || !localFileFault.value) return
  const readId = ++localFileReadId
  const code = localFileFault.value.code
  localFileDrawer.value = true
  readingLocalFile.value = true
  try {
    const preview = await readLocalFaultFile(file, code)
    if (disposed || readId !== localFileReadId) return
    localFilePreview.value = preview
    if (preview.status !== 'matched') {
      localFileError.value = preview.message
      ElMessage.error(preview.message)
      return
    }
    if (preview.executedFaultType !== code) {
      throw new Error(`文件实际故障为 ${preview.executedFaultType}，与当前选择的故障不匹配`)
    }
    const result = await submitGeneration(localFileFault.value.row)
    if (!disposed && readId === localFileReadId) localFileJob.value = result.job_id
  } catch (error) {
    if (!disposed && readId === localFileReadId && error !== 'cancel' && error !== 'close') {
      localFileError.value = error.response?.data?.detail || error.message || '文件读取或任务提交失败'
      ElMessage.error(localFileError.value)
    }
  } finally {
    if (!disposed && readId === localFileReadId) readingLocalFile.value = false
  }
}
async function save(id) {
  if (!canSave(id)) return
  try {
    requestingSave.value[id] = true
    const result = await saveVirtualPrototypeArtifact(id)
    imports.value[id] = result
    ElMessage.success(result.state === 'success' ? '此任务已经保存' : '已开始保存，关闭页面也会继续处理')
    await refreshImports()
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error(error.response?.data?.detail || '保存请求失败') }
  finally { requestingSave.value[id] = false }
}
function goToFiles(id) {
  drawer.value = false
  // Filter by Euler task_id so all 15 samples (fault + baseline = 30 runs)
  // from this explicit save are visible, instead of only the first case.
  router.push({ path: '/online-processing/select', query: { data_category: 'semi', task_id: id } })
}
async function refreshDrawer(id = drawerJobId.value, { notify = false } = {}) {
  if (!drawer.value || !id || disposed || (!notify && detailInFlightId === id)) return false
  const requestId = ++detailRequestId
  detailInFlightId = id
  try {
    const [job, eventResult] = await Promise.all([
      getVirtualPrototypeJob(id),
      getVirtualPrototypeEvents(id),
    ])
    // A slower response for a previous job must never overwrite the task the
    // user is currently viewing. This also protects against closing/reopening
    // the drawer while an old request is still in flight.
    if (disposed || !drawer.value || drawerJobId.value !== id || requestId !== detailRequestId) return false
    if (job?.job_id !== id) return false
    selected.value = job
    events.value = eventResult.events || []
    return true
  } catch (error) {
    if (notify && drawer.value && drawerJobId.value === id) ElMessage.error('任务查询失败')
    return false
  } finally { if (requestId === detailRequestId) detailInFlightId = '' }
}
async function view(id) {
  if (!id) return
  drawerJobId.value = id
  // Clear the previous task immediately so the new drawer cannot briefly show
  // historical progress while its first request is being loaded.
  selected.value = null
  events.value = []
  drawer.value = true
  await refreshDrawer(id, { notify: true })
}
async function cancel(id) {
  try {
    await ElMessageBox.confirm('确认停止任务？底层有界动作可能需要完成当前阶段。', '停止任务', { type: 'warning' })
    await cancelVirtualPrototypeJob(id)
    await refreshJobs()
  } catch (error) { if (error !== 'cancel' && error !== 'close') ElMessage.error('停止请求失败') }
}
async function download(id) {
  try {
    const blob = await downloadVirtualPrototypeArtifact(id)
    const url = URL.createObjectURL(blob.data || blob)
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `${id}.tar.gz`; anchor.click()
    URL.revokeObjectURL(url)
  } catch { ElMessage.error('产物下载失败') }
}
onMounted(() => { refreshAnalysis(); load(); timer = setInterval(() => { refresh(); refreshDrawer() }, 3000) })
onUnmounted(() => { disposed = true; clearInterval(timer) })
</script>
<style scoped>
.demo-page { display: flex; flex-direction: column; gap: 16px; }
.head { display: flex; justify-content: space-between; align-items: center; }
.title { font-size: 18px; font-weight: 650; }
.sub, .note { color: #64748b; font-size: 13px; }
.note { line-height: 1.7; }
.local-file-input { display: none; }
.generation-mode { width: 145px; margin-right: 8px; vertical-align: middle; }
.file-match { margin-top: 16px; }
.local-file-preview { max-height: 55vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; background: #f8fafc; padding: 12px; font-size: 13px; }
.analysis-section { border-top: 1px solid #dce2e8; border-bottom: 1px solid #dce2e8; padding: 16px 0; }
.analysis-heading { gap: 12px; flex-wrap: wrap; font-weight: 600; margin-bottom: 16px; }
.analysis-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; max-width: 100%; }
.analysis-actions :deep(.el-button + .el-button) { margin-left: 0; }
.diagnosis-heading { gap: 12px; flex-wrap: wrap; margin: 16px 0; font-weight: 600; }
.diagnosis-accuracy { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 14px; }
.batch-progress { margin-bottom: 16px; font-size: 13px; line-height: 2; overflow-wrap: anywhere; }
.analysis-pagination { margin-top: 12px; flex-wrap: wrap; }
:deep(.el-descriptions__content) { overflow-wrap: anywhere; white-space: pre-wrap; }
</style>
