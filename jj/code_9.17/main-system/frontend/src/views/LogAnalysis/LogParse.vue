<template>
  <div class="log-parse-page">
    <!-- 已上传日志列表 -->
    <el-card shadow="never" class="logs-card">
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
          <span>已上传日志列表</span>
          <!-- 筛选条件 -->
          <el-select v-model="fileTypeFilter" placeholder="文件类型" clearable size="small" style="width: 120px;">
            <el-option label="全部" value="" />
            <el-option label=".log" value=".log" />
            <el-option label=".txt" value=".txt" />
            <el-option label=".csv" value=".csv" />
            <el-option label=".json" value=".json" />
          </el-select>
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            size="small"
            style="width: 140px;"
          />
          <el-button size="small" @click="loadUploadedLogs" type="primary">
            <el-icon><Search /></el-icon> 查询
          </el-button>
          <el-button size="small" @click="resetFilters">重置</el-button>
          <el-button type="primary" size="small" :disabled="!selectedLogs.length || taskActive" @click="parseSelectedLogs">
            解析选中日志
          </el-button>
        </div>
      </template>
      <el-table
        :data="uploadedLogs"
        @selection-change="onSelectionChange"
        size="small"
        style="margin-top: 8px;"
      >
        <el-table-column type="selection" width="50" />
        <el-table-column prop="filename" label="文件名" />
        <el-table-column prop="line_count" label="行数" width="100" />
        <el-table-column prop="created_at" label="上传时间" width="170">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="去处" width="150">
          <template #default="{ row }">
            <el-tag v-if="!row.has_analysis" type="info" size="small">未分析</el-tag>
            <el-tag v-else :type="row.has_fault ? 'danger' : 'success'" size="small">
              {{ row.has_fault ? '已传入故障诊断' : '已传入预测预警' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button size="small" type="primary" :disabled="taskActive" @click="parseSingleLog(row.id)">
              解析
            </el-button>
            <el-button
              size="small"
              :type="(parsedResults.has(String(row.id)) || row.has_analysis) ? 'success' : 'info'"
              :disabled="!(parsedResults.has(String(row.id)) || row.has_analysis)"
              @click="viewParsedResult(row.id)"
            >
              查看结果
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="totalUploadedLogs > 20"
        v-model:current-page="uploadedPage"
        :page-size="20"
        :total="totalUploadedLogs"
        layout="prev, pager, next"
        @current-change="loadUploadedLogs"
        style="margin-top: 12px; justify-content: flex-end;"
      />
    </el-card>

    <!-- 分析记录（DB5：与故障诊断/预测预警同源，含上传文件 + 数据库选择）-->
    <el-card shadow="never" class="logs-card">
      <template #header>
        <div style="display:flex; align-items:center; gap:12px;">
          <span>分析记录</span>
          <el-button size="small" @click="loadAnalysisRecords">刷新</el-button>
          <span style="color:#94a3b8; font-size:12px;">与「故障诊断 / 预测预警」同源；删除将联通移除三处（数据库选择的原始数据不受影响）</span>
        </div>
      </template>
      <el-table :data="analysisRecords" size="small" v-loading="recordsLoading">
        <el-table-column label="文件" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tooltip :content="row.run_id" placement="top"><span>{{ logLabel(row) }}</span></el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.source === 'upload' ? 'primary' : 'info'" effect="plain">{{ sourceLabel(row.source) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="判定" width="120">
          <template #default="{ row }">
            <el-tag size="small" :type="row.has_fault ? 'danger' : 'success'">{{ row.has_fault ? '异常' : '正常' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="analyzed_at" label="分析时间" width="170">
          <template #default="{ row }">{{ formatDate(row.analyzed_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-popconfirm title="删除该分析记录（联通移除诊断/预警；数据库原始数据保留）？" @confirm="removeAnalysisRun(row.run_id)">
              <template #reference><el-button size="small" type="danger" link>删除</el-button></template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="analysisTotal > 10"
        v-model:current-page="recordsPage"
        :page-size="10"
        :total="analysisTotal"
        layout="prev, pager, next"
        @current-change="loadAnalysisRecords"
        style="margin-top: 12px; justify-content: flex-end;"
      />
    </el-card>

    <!-- 解析结果 -->
    <template v-if="analyzeResult">
      <!-- 分析结论卡片 -->
      <el-card shadow="never" class="verdict-card" :class="analyzeResult.has_fault ? 'verdict-fault' : 'verdict-normal'">
        <div class="verdict-header">
          <el-tag
            :type="analyzeResult.has_fault ? 'danger' : 'success'"
            size="large"
            effect="dark"
            class="verdict-tag"
          >
            {{ analyzeResult.has_fault ? '故障日志' : '正常日志' }}
          </el-tag>
          <span class="verdict-confidence">
            置信度 <strong>{{ Math.round(analyzeResult.confidence * 100) }}%</strong>
          </span>
          <span class="verdict-next-action">
            建议下一步：<strong>{{ nextActionLabel }}</strong>
          </span>
          <el-button
            :type="analyzeResult.has_fault ? 'danger' : 'success'"
            size="small"
            class="verdict-cta"
            @click="goForkTarget"
          >
            {{ analyzeResult.has_fault ? '去故障诊断 →' : '去预测预警 →' }}
          </el-button>
          <span class="verdict-cache-hint" v-if="parsedResults.size > 1">
            已缓存 {{ parsedResults.size }} 条解析结果
          </span>
        </div>
        <div class="verdict-summary">{{ analyzeResult.summary }}</div>
      </el-card>

      <!-- LLM 语义增强（仅当后端返回了 llm_insight 时展示） -->
      <el-card
        v-if="llmInsight"
        shadow="never"
        class="result-card llm-card"
      >
        <template #header>
          <div style="display: flex; align-items: center; gap: 10px;">
            <span>🤖 LLM 语义增强</span>
            <el-tag size="small" :type="severityTagType">严重度：{{ llmInsight.severity || 'low' }}</el-tag>
            <el-tag v-if="llmInsight.suggested_fault_type" size="small" type="warning">
              推断：{{ llmInsight.suggested_fault_type }}
            </el-tag>
            <el-tag v-if="llmInsight._truncated" size="small" type="info">
              输出截断（已降级展示）
            </el-tag>
          </div>
        </template>
        <div v-if="llmInsight.summary" class="llm-section">
          <div class="info-title">LLM 总结</div>
          <div class="llm-text">{{ llmInsight.summary }}</div>
        </div>
        <div v-if="llmInsight.fault_signals?.length" class="llm-section">
          <div class="info-title">故障信号</div>
          <el-tag
            v-for="sig in llmInsight.fault_signals"
            :key="sig"
            size="small"
            type="danger"
            effect="plain"
            style="margin: 2px;"
          >{{ sig }}</el-tag>
        </div>
        <div v-if="llmInsight.parsed_events?.length" class="llm-section">
          <div class="info-title">关键事件</div>
          <el-table :data="llmInsight.parsed_events" size="small" border>
            <el-table-column label="ID" width="60">
              <template #default="{ row, $index }">#{{ row.id != null ? row.id : $index + 1 }}</template>
            </el-table-column>
            <el-table-column prop="ts" label="时间" width="180" />
            <el-table-column prop="level" label="级别" width="80" />
            <el-table-column prop="module" label="模块" width="140" />
            <el-table-column prop="message" label="消息" />
            <el-table-column prop="evidence_line" label="行号" width="70" />
          </el-table>
        </div>
        <div v-if="llmInsight.root_cause_analysis" class="llm-section">
          <div class="info-title">根因分析</div>
          <div class="llm-text">{{ llmInsight.root_cause_analysis }}</div>
        </div>
        <div v-if="llmInsight.recovery_hint" class="llm-section">
          <div class="info-title">处理建议</div>
          <div class="llm-text">{{ llmInsight.recovery_hint }}</div>
        </div>
      </el-card>

      <!-- 异常评分分布统计 -->
      <el-card v-if="analyzeResult.score_distribution" shadow="never" class="result-card">
        <template #header><span>异常评分分布统计</span></template>
        <div ref="scoreDistributionChartRef" style="height: 300px;" />
      </el-card>

      <!-- 解析详情卡片 -->
      <el-card shadow="never" class="result-card">
        <template #header><span>解析详情 — {{ analyzeResult.filename }}</span></template>

        <!-- 日志级别统计 -->
        <el-row :gutter="20">
          <el-col :span="10">
            <div ref="levelChartRef" style="height: 200px;" />
          </el-col>
          <el-col :span="14">
            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="总行数">{{ analyzeResult.line_count }}</el-descriptions-item>
              <el-descriptions-item label="ERROR">
                <el-tag type="danger" size="small">{{ analyzeResult.log_level_stats.error_count }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="WARNING">
                <el-tag type="warning" size="small">{{ analyzeResult.log_level_stats.warning_count }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="INFO">
                <el-tag type="info" size="small">{{ analyzeResult.log_level_stats.info_count }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="DEBUG">
                <el-tag size="small">{{ analyzeResult.log_level_stats.debug_count }}</el-tag>
              </el-descriptions-item>
            </el-descriptions>

            <!-- 错误码：有内容才展示 -->
            <div v-if="analyzeResult.extracted_error_codes.length" class="info-section">
              <div class="info-title">提取到的错误码</div>
              <el-tag
                v-for="code in analyzeResult.extracted_error_codes"
                :key="code"
                type="danger"
                size="small"
                style="margin: 2px;"
              >{{ code }}</el-tag>
            </div>

            <!-- 提取的结构化数据 -->
            <div v-if="analyzeResult.structured_data" class="info-section">
              <div class="info-title">结构化数据</div>
              <el-table :data="structuredDataList" size="small" border style="margin-top: 8px;">
                <el-table-column prop="field" label="字段" width="120" />
                <el-table-column prop="value" label="值" />
              </el-table>
            </div>
          </el-col>
        </el-row>

        <!-- 堆栈信息：有内容才展示 -->
        <div v-if="analyzeResult.extracted_stack_traces.length" class="info-section">
          <div class="info-title">堆栈信息</div>
          <el-collapse>
            <el-collapse-item
              v-for="(trace, i) in analyzeResult.extracted_stack_traces"
              :key="i"
              :title="`堆栈 ${i + 1}`"
            >
              <pre class="stack-trace">{{ trace }}</pre>
            </el-collapse-item>
          </el-collapse>
        </div>

        <!-- 原文预览：折叠式，默认截断 1200 字符 -->
        <div class="info-section">
          <div class="info-title">原文预览</div>
          <el-scrollbar max-height="200px">
            <pre class="raw-content">{{ displayedRaw }}</pre>
          </el-scrollbar>
          <el-button
            v-if="analyzeResult.raw_content.length > 1200"
            link
            size="small"
            class="raw-toggle"
            @click="rawExpanded = !rawExpanded"
          >
            {{ rawExpanded ? '收起' : `展开全部（共 ${analyzeResult.raw_content.length} 字符）` }}
          </el-button>
        </div>
      </el-card>
    </template>

    <el-dialog
      v-model="taskDialogVisible"
      title="日志解析"
      width="min(520px, 92vw)"
      :close-on-click-modal="false"
      :close-on-press-escape="taskTerminal"
      :show-close="taskTerminal"
    >
      <div class="parse-task-status">
        <el-progress
          :percentage="clampTaskProgress(activeTask?.progress)"
          :status="activeTask?.state === 'failed' ? 'exception' : activeTask?.state === 'succeeded' ? 'success' : undefined"
          :stroke-width="10"
        />
        <div class="parse-task-meta">
          <span>{{ parseTaskStageLabel(activeTask?.stage) }}</span>
          <span v-if="activeTask?.current_run_id" class="parse-task-current">
            {{ activeTask.current_run_id }}
          </span>
        </div>
        <div v-if="activeTask?.run_ids?.length > 1" class="parse-task-count">
          已完成 {{ activeTask.completed_count }} / {{ activeTask.run_ids.length }}
        </div>
        <el-alert
          v-if="activeTask?.state === 'failed'"
          :title="activeTask.error_message || activeTask.fail_details?.[0]?.message || '解析失败'"
          type="error"
          :closable="false"
          show-icon
        />
      </div>
      <template #footer>
        <el-button
          v-if="!taskTerminal && activeTask?.stage !== 'persisting'"
          :icon="CircleClose"
          :loading="activeTask?.state === 'cancelling'"
          @click="cancelActiveTask"
        >
          {{ activeTask?.state === 'cancelling' ? '正在取消' : '取消解析' }}
        </el-button>
        <el-button v-else-if="taskTerminal" type="primary" @click="taskDialogVisible = false">
          关闭
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onBeforeUnmount, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { CircleClose, Search } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import {
  cancelLogParseTask,
  createLogParseTask,
  deleteAnalysisRun,
  getAnalysisResult,
  getLogParseTask,
  listAnalysisResults,
  listUploadedLogs,
} from '@/api/logAnalysis'
import {
  clampTaskProgress,
  isTerminalParseTask,
  parseTaskStageLabel,
} from './logParseTaskState'
import { logLabel, sourceLabel } from '@/utils/logLabel'

const route = useRoute()
const router = useRouter()

// 分析记录（DB5，三页同源）
const analysisRecords = ref([])
const recordsLoading = ref(false)
const recordsPage = ref(1)
const analysisTotal = ref(0)

async function loadAnalysisRecords() {
  recordsLoading.value = true
  try {
    const res = await listAnalysisResults({ page: recordsPage.value, page_size: 10 })
    analysisRecords.value = res.items || []
    analysisTotal.value = res.total || 0
  } catch (e) {
    ElMessage.error(`加载分析记录失败：${e.message || e}`)
  } finally {
    recordsLoading.value = false
  }
}

async function removeAnalysisRun(runId) {
  try {
    await deleteAnalysisRun(runId)
    ElMessage.success('已删除（诊断/预警同步移除；数据库原始数据保留）')
    loadAnalysisRecords()
    loadUploadedLogs()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message || e}`)
  }
}

// 已上传日志列表状态
const uploadedLogs = ref([])
const totalUploadedLogs = ref(0)
const uploadedPage = ref(1)
const fileTypeFilter = ref('')
const dateRange = ref([])
const selectedLogs = ref([])

// 解析状态
const analyzing = ref(false)
const activeTask = ref(null)
const taskDialogVisible = ref(false)
const ACTIVE_TASK_KEY = 'log-analysis-active-parse-task'
let taskPollTimer = null
let taskPollToken = 0
let taskPollFailures = 0
const analyzeResult = ref(null)
const levelChartRef = ref(null)
let levelChart = null
const scoreDistributionChartRef = ref(null)
let scoreDistributionChart = null
const rawExpanded = ref(false)

// 解析结果缓存：log_id -> AnalyzeLogOut
// 单个解析 / 批量解析后都写进这个 Map，"查看结果"按钮读它即可回看
// 用 shallowRef 包一层让模板中的 .has() 能响应（普通 Map 不会触发 reactivity，
// 这里改用 ref(Map) 然后写时整体替换 / 创建新 Map 实例）
const parsedResults = ref(new Map())



// 计算属性
const taskTerminal = computed(() => isTerminalParseTask(activeTask.value?.state))
const taskActive = computed(() => Boolean(activeTask.value) && !taskTerminal.value)

const nextActionLabel = computed(() => {
  if (!analyzeResult.value) return ''
  return analyzeResult.value.next_action === 'fault_location' ? '故障定位' : '预测预警'
})

const displayedRaw = computed(() => {
  if (!analyzeResult.value) return ''
  const content = analyzeResult.value.raw_content
  if (rawExpanded.value || content.length <= 1200) return content
  return content.slice(0, 1200) + '...'
})

const structuredDataList = computed(() => {
  if (!analyzeResult.value?.structured_data) return []
  const data = analyzeResult.value.structured_data
  return [
    { field: '时间戳', value: data.timestamp || '-' },
    { field: '日志级别', value: data.log_level || '-' },
    { field: '设备ID', value: data.device_id || '-' },
    { field: '模块名称', value: data.module_name || '-' },
    { field: '错误码', value: data.error_code || '-' },
    { field: '异常描述', value: data.exception_description || '-' }
  ]
})

// LLM 语义增强（后端 structured_data.llm_insight）
// LLM 未启用时为 null，前端用 v-if 隐藏整张卡片
const llmInsight = computed(() => {
  return analyzeResult.value?.structured_data?.llm_insight || null
})

const severityTagType = computed(() => {
  const sev = llmInsight.value?.severity
  return {
    critical: 'danger',
    high: 'danger',
    medium: 'warning',
    low: 'info',
  }[sev] || 'info'
})

// 工具函数
function formatDate(dt) {
  if (!dt) return '-'
  const s = String(dt)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  const iso = hasTz ? s : s + 'Z'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function onSelectionChange(rows) {
  selectedLogs.value = rows
}

function resetFilters() {
  fileTypeFilter.value = ''
  dateRange.value = []
  uploadedPage.value = 1
  loadUploadedLogs()
}

function initLevelChart(stats) {
  if (!levelChart) levelChart = echarts.init(levelChartRef.value)
  levelChart.setOption({
    title: { text: '日志级别分布', left: 'center', top: 0, textStyle: { fontSize: 13 } },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] },
    grid: { left: 70, right: 20, top: 30, bottom: 20 },
    series: [{
      type: 'bar',
      data: [
        { value: stats.debug_count,   itemStyle: { color: '#94a3b8' } },
        { value: stats.info_count,    itemStyle: { color: '#60a5fa' } },
        { value: stats.warning_count, itemStyle: { color: '#fbbf24' } },
        { value: stats.error_count,   itemStyle: { color: '#f87171' } },
      ],
      label: { show: true, position: 'right' },
    }],
  })
}

function initScoreDistributionChart(distribution) {
  if (!scoreDistributionChart) scoreDistributionChart = echarts.init(scoreDistributionChartRef.value)
  scoreDistributionChart.setOption({
    title: { text: '异常评分分布', left: 'center', top: 0, textStyle: { fontSize: 13 } },
    xAxis: {
      type: 'category',
      data: ['0-2', '2-4', '4-6', '6-8', '8-10'],
      name: '评分区间'
    },
    yAxis: {
      type: 'value',
      name: '日志数量'
    },
    grid: { left: 70, right: 20, top: 30, bottom: 30 },
    series: [{
      type: 'bar',
      data: [
        distribution['0-2'] || 0,
        distribution['2-4'] || 0,
        distribution['4-6'] || 0,
        distribution['6-8'] || 0,
        distribution['8-10'] || 0
      ],
      itemStyle: {
        color: function(params) {
          const colors = ['#34d399', '#60a5fa', '#fbbf24', '#fb923c', '#f87171']
          return colors[params.dataIndex]
        }
      },
      label: { show: true, position: 'top' },
    }],
  })
}

// 加载已上传日志
async function loadUploadedLogs() {
  try {
    const params = {
      page: uploadedPage.value,
      page_size: 20,
      file_type: fileTypeFilter.value,
      start_date: dateRange.value[0] ? dateRange.value[0].toISOString() : null,
      end_date: dateRange.value[1] ? dateRange.value[1].toISOString() : null
    }
    const result = await listUploadedLogs(params)
    uploadedLogs.value = result.items
    totalUploadedLogs.value = result.total
  } catch (error) {
    ElMessage.error(`加载日志列表失败: ${error.message}`)
  }
}

function stopTaskPolling() {
  taskPollToken += 1
  if (taskPollTimer != null) clearTimeout(taskPollTimer)
  taskPollTimer = null
}

function scheduleTaskPoll(taskId, token) {
  taskPollTimer = setTimeout(() => pollTask(taskId, token), 1000)
}

async function handleTerminalTask(task) {
  stopTaskPolling()
  analyzing.value = false
  sessionStorage.removeItem(ACTIVE_TASK_KEY)

  const successIds = Object.keys(task.results || {})
  successIds.forEach(id => cacheResult(id, task.results[id]))
  if (successIds.length) {
    const last = successIds[successIds.length - 1]
    analyzeResult.value = parsedResults.value.get(String(last))
    await renderResultCharts(analyzeResult.value)
  }

  await Promise.all([loadUploadedLogs(), loadAnalysisRecords()])
  if (task.state === 'succeeded') {
    if (task.fail_count > 0) {
      ElMessage.warning(`解析完成：成功 ${task.success_count} 条，失败 ${task.fail_count} 条`)
    } else {
      ElMessage.success(`日志解析完成：成功 ${task.success_count} 条`)
    }
  } else if (task.state === 'cancelled') {
    ElMessage.info('日志解析已取消')
  } else {
    ElMessage.error(task.error_message || task.fail_details?.[0]?.message || '日志解析失败')
  }
}

async function pollTask(taskId, token) {
  if (token !== taskPollToken) return
  try {
    const task = await getLogParseTask(taskId)
    if (token !== taskPollToken) return
    taskPollFailures = 0
    activeTask.value = task
    if (isTerminalParseTask(task.state)) {
      await handleTerminalTask(task)
      return
    }
  } catch (error) {
    if (token !== taskPollToken) return
    taskPollFailures += 1
    if (taskPollFailures === 3) {
      ElMessage.warning('解析任务状态暂时无法获取，正在重试')
    }
  }
  if (token === taskPollToken) scheduleTaskPoll(taskId, token)
}

function startTaskPolling(taskId) {
  stopTaskPolling()
  const token = taskPollToken
  taskPollFailures = 0
  pollTask(taskId, token)
}

async function startParseTask(runIds) {
  if (taskActive.value) return ElMessage.warning('已有解析任务正在执行')
  analyzing.value = true
  try {
    const task = await createLogParseTask({ run_ids: runIds.map(String) })
    activeTask.value = task
    taskDialogVisible.value = true
    sessionStorage.setItem(ACTIVE_TASK_KEY, task.id)
    startTaskPolling(task.id)
  } catch (error) {
    analyzing.value = false
    ElMessage.error(`解析任务创建失败: ${error.message}`)
  }
}

function parseSingleLog(logId) {
  return startParseTask([logId])
}

function parseSelectedLogs() {
  if (!selectedLogs.value.length) return ElMessage.warning('请选择要解析的日志')
  return startParseTask(selectedLogs.value.map(log => log.id))
}

async function cancelActiveTask() {
  const task = activeTask.value
  if (!task || task.state === 'cancelling' || isTerminalParseTask(task.state)) return
  try {
    activeTask.value = await cancelLogParseTask(task.id)
    if (isTerminalParseTask(activeTask.value.state)) {
      await handleTerminalTask(activeTask.value)
    }
  } catch (error) {
    ElMessage.error(`取消解析失败: ${error.message}`)
  }
}

// 查看缓存中的解析结果（按行点击）
async function viewParsedResult(logId) {
  let cached = parsedResults.value.get(String(logId))
  // 本地无缓存（如刚从别的页面回来）则从后端拉取已持久化的解析结果，避免重新解析
  if (!cached) {
    try {
      const res = await getAnalysisResult(logId)
      cached = res?.result?.result || null
      if (cached) cacheResult(logId, cached)
    } catch (error) {
      cached = null
    }
  }
  if (!cached) return ElMessage.warning('该条尚无解析结果，请先点「解析」')
  analyzeResult.value = cached
  await renderResultCharts(cached)
  // 平滑滚动到结果区域
  await nextTick()
  const el = document.querySelector('.verdict-card')
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

// 把解析结果写进缓存。用「重新赋值新 Map」触发响应式更新（模板里 .has() 才会刷新）
function cacheResult(logId, payload) {
  const next = new Map(parsedResults.value)
  next.set(String(logId), payload)
  parsedResults.value = next
}

// 渲染结果区域的两个图表（解析 / 查看结果共用）
async function renderResultCharts(result) {
  if (!result) return
  await nextTick()
  if (levelChartRef.value && result.log_level_stats) initLevelChart(result.log_level_stats)
  if (scoreDistributionChartRef.value && result.score_distribution) {
    initScoreDistributionChart(result.score_distribution)
  }
}

// 分叉跳转：异常→故障诊断 / 正常→预测预警（带 run_id）
function goForkTarget() {
  const runId = analyzeResult.value?.run_id
  const query = runId ? { run_id: runId } : {}
  if (analyzeResult.value?.has_fault) {
    router.push({ name: 'Diagnosis', query })
  } else {
    router.push({ name: 'Prediction', query })
  }
}

// 初始化
onMounted(() => {
  loadUploadedLogs()
  loadAnalysisRecords()
  const savedTaskId = sessionStorage.getItem(ACTIVE_TASK_KEY)
  if (savedTaskId) {
    taskDialogVisible.value = true
    analyzing.value = true
    startTaskPolling(savedTaskId)
    return
  }
  // 从「在线处理 → 文件选择」带 run_id 跳转进来时，自动解析该文件
  const rid = route.query.run_id
  if (rid) {
    parseSingleLog(String(rid))
  }
})

onBeforeUnmount(() => {
  stopTaskPolling()
})
</script>

<style scoped>
.log-parse-page { display: flex; flex-direction: column; gap: 20px; }
.logs-card, .result-card, .verdict-card { margin-bottom: 20px; }
.parse-task-status { display: flex; flex-direction: column; gap: 14px; }
.parse-task-meta { display: flex; justify-content: space-between; gap: 16px; color: #475569; font-size: 13px; }
.parse-task-current { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #64748b; }
.parse-task-count { color: #64748b; font-size: 13px; }
.info-section { margin-top: 16px; }
.info-title { font-size: 13px; font-weight: 600; color: #475569; margin-bottom: 6px; }
.stack-trace { font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0; }
.raw-content { font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0; }

/* 分析结论卡片 */
.verdict-card {
  margin-bottom: 16px;
  border-left: 4px solid #e2e8f0;
}
.verdict-fault {
  border-left-color: #f87171;
  background-color: #fff5f5;
}
.verdict-normal {
  border-left-color: #34d399;
  background-color: #f0fdf4;
}
.verdict-header {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.verdict-tag {
  font-size: 14px;
  padding: 0 14px;
  height: 30px;
  line-height: 30px;
}
.verdict-confidence {
  font-size: 14px;
  color: #475569;
}
.verdict-confidence strong {
  color: #1e293b;
  font-size: 16px;
}
.verdict-next-action {
  font-size: 13px;
  color: #64748b;
  background: #f1f5f9;
  padding: 3px 10px;
  border-radius: 4px;
}
.verdict-next-action strong {
  color: #334155;
}
.verdict-summary {
  font-size: 14px;
  color: #334155;
  line-height: 1.7;
  padding-top: 4px;
  border-top: 1px solid rgba(0,0,0,0.06);
}

/* 原文折叠按钮 */
.raw-toggle {
  margin-top: 6px;
  color: #6366f1;
  font-size: 12px;
}

/* 缓存条数提示 */
.verdict-cache-hint {
  font-size: 12px;
  color: #2563eb;
  background: #eff6ff;
  padding: 2px 10px;
  border-radius: 4px;
  margin-left: auto;
}

/* LLM 语义增强卡片 */
.llm-card {
  border-left: 4px solid #2563eb;
}
.llm-section {
  margin-bottom: 14px;
}
.llm-section:last-child {
  margin-bottom: 0;
}
.llm-text {
  font-size: 13px;
  color: #334155;
  line-height: 1.7;
  background: #f8fafc;
  padding: 8px 12px;
  border-radius: 4px;
  border-left: 2px solid #e2e8f0;
}
</style>
