<template>
  <div class="log-analysis-detail-page">
    <el-page-header content="日志运行详情" @back="goBackToList" />

    <el-card shadow="never" class="result-card" v-loading="detailLoading">
      <template #header>
        <div class="detail-header">
          <div>
            <div class="detail-title">{{ detail?.run_name || route.params.runId }}</div>
            <div class="detail-subtitle">{{ detail?.test_name || '-' }}</div>
          </div>
          <el-tag v-if="detail" :type="getFaultStatusTagType(detail.fault_status)" size="large">
            {{ formatFaultStatus(detail.fault_status) }}
          </el-tag>
        </div>
      </template>

      <el-descriptions v-if="detail" :column="3" border>
        <el-descriptions-item label="Run ID">{{ detail.run_id }}</el-descriptions-item>
        <el-descriptions-item label="Case ID">{{ detail.case_id || '-' }}</el-descriptions-item>
        <el-descriptions-item label="系统">{{ detail.system_id || '-' }}</el-descriptions-item>
        <el-descriptions-item label="Fault Type">{{ detail.fault_type || '-' }}</el-descriptions-item>
        <el-descriptions-item label="子系统">{{ detail.subsystem || '-' }}</el-descriptions-item>
        <el-descriptions-item label="Round">{{ detail.round_no ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ formatDateTime(detail.start_time) }}</el-descriptions-item>
        <el-descriptions-item label="结束时间">{{ formatDateTime(detail.end_time) }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatDateTime(detail.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="已解析">{{ detail.parsed_lines }}</el-descriptions-item>
        <el-descriptions-item label="总行数">{{ detail.total_lines }}</el-descriptions-item>
        <el-descriptions-item label="条目总数">{{ detail.entry_count }}</el-descriptions-item>
        <el-descriptions-item label="ERROR">{{ detail.error_logs }}</el-descriptions-item>
        <el-descriptions-item label="CRITICAL">{{ detail.critical_logs }}</el-descriptions-item>
        <el-descriptions-item label="窗口总数">{{ detail.windows_total }}</el-descriptions-item>
      </el-descriptions>

      <div v-if="detail" class="detail-grid">
        <el-card shadow="never">
          <template #header><span>级别分布</span></template>
          <div v-if="levelDistributionRows.length" class="stats-list">
            <div v-for="item in levelDistributionRows" :key="item.label" class="stats-row">
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
            </div>
          </div>
          <el-empty v-else description="暂无数据" :image-size="80" />
        </el-card>

        <el-card shadow="never">
          <template #header><span>Top Modules</span></template>
          <div v-if="topModules.length" class="stats-list">
            <div v-for="item in topModules" :key="`${item.module}-${item.count}`" class="stats-row">
              <span>{{ item.module || '-' }}</span>
              <strong>{{ item.count ?? 0 }}</strong>
            </div>
          </div>
          <el-empty v-else description="暂无数据" :image-size="80" />
        </el-card>
      </div>
    </el-card>

    <el-card shadow="never" class="diagnosis-card">
      <template #header>
        <div class="diagnosis-header">
          <span>故障诊断</span>
          <el-button
            type="primary"
            size="small"
            :loading="diagnosisRunning"
            @click="runDiagnosis"
          >
            {{ diagnosis ? '重新诊断' : '运行诊断' }}
          </el-button>
        </div>
      </template>

      <div v-loading="diagnosisLoading">
        <el-descriptions v-if="diagnosis" :column="2" border>
          <el-descriptions-item label="是否故障">
            <el-tag :type="diagnosis.is_fault ? 'danger' : 'success'">
              {{ diagnosis.is_fault ? '是' : '否' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="故障类型">{{ diagnosis.fault_type_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="通道">{{ diagnosis.channel_used || '-' }}</el-descriptions-item>
          <el-descriptions-item label="置信度">
            {{ diagnosis.confidence != null ? diagnosis.confidence.toFixed(4) : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="相似度">
            {{ diagnosis.similarity_score != null ? diagnosis.similarity_score.toFixed(4) : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="诊断时间">{{ formatDateTime(diagnosis.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="诊断说明" :span="2">
            {{ diagnosis.llm_reasoning || '-' }}
          </el-descriptions-item>
        </el-descriptions>
        <el-empty v-else description="该日志尚未诊断，点击「运行诊断」生成结果（结果会存库，下次打开直接复看）" :image-size="80" />
      </div>
    </el-card>

    <el-card shadow="never" class="result-card">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="log_entries" name="entries">
          <el-table :data="entryRows" size="small" v-loading="entriesLoading">
            <el-table-column prop="timestamp" label="时间" min-width="180">
              <template #default="{ row }">{{ formatDateTime(row.timestamp) }}</template>
            </el-table-column>
            <el-table-column prop="level" label="级别" width="100" />
            <el-table-column prop="module" label="模块" min-width="140" show-overflow-tooltip />
            <el-table-column prop="file_path" label="文件" min-width="180" show-overflow-tooltip />
            <el-table-column prop="line_no" label="行号" width="90" />
            <el-table-column prop="message" label="消息" min-width="420" show-overflow-tooltip />
          </el-table>
          <div class="pagination-wrap">
            <el-pagination
              v-model:current-page="entryPagination.page"
              :page-size="entryPagination.pageSize"
              :total="entryTotal"
              layout="total, prev, pager, next"
              @current-change="loadEntries"
            />
          </div>
        </el-tab-pane>

        <el-tab-pane label="log_windows" name="windows">
          <el-table :data="windowRows" size="small" v-loading="windowsLoading">
            <el-table-column prop="window_id" label="Window ID" min-width="260" show-overflow-tooltip />
            <el-table-column prop="start_time" label="开始时间" min-width="180">
              <template #default="{ row }">{{ formatDateTime(row.start_time) }}</template>
            </el-table-column>
            <el-table-column prop="end_time" label="结束时间" min-width="180">
              <template #default="{ row }">{{ formatDateTime(row.end_time) }}</template>
            </el-table-column>
            <el-table-column prop="strategy" label="策略" width="120" />
            <el-table-column prop="entry_count" label="条目数" width="90" />
            <el-table-column prop="error_events" label="错误事件" width="100" />
            <el-table-column label="关键事件" min-width="280">
              <template #default="{ row }">
                {{ formatKeyEvents(row.key_events) }}
              </template>
            </el-table-column>
            <el-table-column label="预览" min-width="320" show-overflow-tooltip>
              <template #default="{ row }">
                {{ formatWindowPreview(row.text_preview) }}
              </template>
            </el-table-column>
          </el-table>
          <div class="pagination-wrap">
            <el-pagination
              v-model:current-page="windowPagination.page"
              :page-size="windowPagination.pageSize"
              :total="windowTotal"
              layout="total, prev, pager, next"
              @current-change="loadWindows"
            />
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import {
  getDatasetRunDetail,
  listDatasetRunEntries,
  listDatasetRunWindows,
} from '@/api/logAnalysis'
import { diagnoseText, getDiagnosisByRun } from '@/api/diagnosis'

import { formatFaultStatus, formatWindowPreview, getFaultStatusTagType } from './logAnalysisTransforms'

const route = useRoute()
const router = useRouter()

const activeTab = ref('entries')
const detail = ref(null)
const detailLoading = ref(false)
const entriesLoading = ref(false)
const windowsLoading = ref(false)

const entryRows = ref([])
const entryTotal = ref(0)
const entryPagination = ref({
  page: 1,
  pageSize: 20,
})

const windowRows = ref([])
const windowTotal = ref(0)
const windowPagination = ref({
  page: 1,
  pageSize: 10,
})
let detailRequestId = 0
let entriesRequestId = 0
let windowsRequestId = 0

// ── 故障诊断（按 run_id 持久化，可重复读取，无需每次重新点击）──
const diagnosis = ref(null)
const diagnosisLoading = ref(false)
const diagnosisRunning = ref(false)
let diagnosisRequestId = 0

const levelDistributionRows = computed(() => {
  const distribution = detail.value?.level_distribution || {}
  return Object.entries(distribution).map(([label, value]) => ({ label, value }))
})

const topModules = computed(() => detail.value?.top_modules || [])

function goBackToList() {
  router.push({ name: 'LogAnalysisMain' })
}

function formatDateTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function formatKeyEvents(events) {
  if (!Array.isArray(events) || events.length === 0) return '-'
  return events
    .map((event) => event?.message || event?.raw_line || event?.level || '')
    .filter(Boolean)
    .join(' | ') || '-'
}

async function loadDetail() {
  const requestId = ++detailRequestId
  const runId = route.params.runId
  if (!runId) return

  detailLoading.value = true
  try {
    const result = await getDatasetRunDetail(runId)
    if (requestId !== detailRequestId) return
    detail.value = result
  } catch (error) {
    if (requestId !== detailRequestId) return
    ElMessage.error(`加载运行详情失败: ${error.message}`)
  } finally {
    if (requestId === detailRequestId) {
      detailLoading.value = false
    }
  }
}

async function loadDiagnosis() {
  const requestId = ++diagnosisRequestId
  const runId = route.params.runId
  if (!runId) return
  diagnosisLoading.value = true
  try {
    const result = await getDiagnosisByRun(runId)
    if (requestId !== diagnosisRequestId) return
    diagnosis.value = result || null
  } catch (error) {
    if (requestId !== diagnosisRequestId) return
    // 无记录或读取失败时静默：诊断面板显示“尚未诊断”
    diagnosis.value = null
  } finally {
    if (requestId === diagnosisRequestId) {
      diagnosisLoading.value = false
    }
  }
}

async function runDiagnosis() {
  const runId = route.params.runId
  if (!runId) return
  diagnosisRunning.value = true
  try {
    const result = await diagnoseText({ log_text: '', run_id: runId, force_refresh: true })
    diagnosis.value = result
    ElMessage.success('诊断完成')
  } catch (error) {
    ElMessage.error(`诊断失败: ${error.message}`)
  } finally {
    diagnosisRunning.value = false
  }
}

async function loadEntries() {
  const requestId = ++entriesRequestId
  const runId = route.params.runId
  if (!runId) return

  entriesLoading.value = true
  try {
    const result = await listDatasetRunEntries(runId, {
      page: entryPagination.value.page,
      page_size: entryPagination.value.pageSize,
    })
    if (requestId !== entriesRequestId) return
    entryRows.value = result.items || []
    entryTotal.value = result.total || 0
  } catch (error) {
    if (requestId !== entriesRequestId) return
    ElMessage.error(`加载日志条目失败: ${error.message}`)
  } finally {
    if (requestId === entriesRequestId) {
      entriesLoading.value = false
    }
  }
}

async function loadWindows() {
  const requestId = ++windowsRequestId
  const runId = route.params.runId
  if (!runId) return

  windowsLoading.value = true
  try {
    const result = await listDatasetRunWindows(runId, {
      page: windowPagination.value.page,
      page_size: windowPagination.value.pageSize,
    })
    if (requestId !== windowsRequestId) return
    windowRows.value = result.items || []
    windowTotal.value = result.total || 0
  } catch (error) {
    if (requestId !== windowsRequestId) return
    ElMessage.error(`加载日志窗口失败: ${error.message}`)
  } finally {
    if (requestId === windowsRequestId) {
      windowsLoading.value = false
    }
  }
}

watch(activeTab, (value) => {
  if (value === 'windows' && windowRows.value.length === 0) {
    loadWindows()
  }
})

watch(
  () => route.params.runId,
  async (runId) => {
    detailRequestId += 1
    entriesRequestId += 1
    windowsRequestId += 1
    detail.value = null
    detailLoading.value = false
    entryRows.value = []
    entryTotal.value = 0
    entriesLoading.value = false
    entryPagination.value.page = 1
    windowRows.value = []
    windowTotal.value = 0
    windowsLoading.value = false
    windowPagination.value.page = 1
    diagnosisRequestId += 1
    diagnosis.value = null
    diagnosisLoading.value = false
    diagnosisRunning.value = false

    if (!runId) return

    await loadDetail()
    await loadEntries()
    void loadDiagnosis()

    if (activeTab.value === 'windows') {
      await loadWindows()
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.log-analysis-detail-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.result-card {
  border-radius: 16px;
}

.diagnosis-card {
  border-radius: 16px;
  margin-top: 16px;
}

.diagnosis-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.detail-title {
  color: #0f172a;
  font-size: 20px;
  font-weight: 700;
}

.detail-subtitle {
  margin-top: 4px;
  color: #64748b;
  font-size: 13px;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}

.stats-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.stats-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #334155;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

@media (max-width: 900px) {
  .detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
