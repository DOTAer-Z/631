<template>
  <div class="overview-page">
    <!-- 刷新按钮 -->
    <div class="toolbar">
      <el-button :loading="loading" @click="loadData" :icon="Refresh">刷新数据</el-button>
    </div>

    <div v-loading="loading">
      <!-- 统计卡片 -->
      <el-row :gutter="20" class="stats-row">
        <el-col :span="6">
          <el-card shadow="never" class="stat-card">
            <div class="stat-value">{{ stats.fault_type_count }}</div>
            <div class="stat-label">故障类型</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="stat-card">
            <div class="stat-value">{{ stats.log_indexed }} / {{ stats.log_total }}</div>
            <div class="stat-label">日志案例（已入库 / 总计）</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="stat-card">
            <div class="stat-value">{{ stats.diagnosis_total }}</div>
            <div class="stat-label">诊断次数</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="stat-card">
            <div class="stat-value">{{ stats.prediction_total }}</div>
            <div class="stat-label">预测次数</div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 数据集统计卡片 -->
      <el-row :gutter="20" class="stats-row">
        <el-col :span="6">
          <el-card shadow="never" class="stat-card">
            <div class="stat-value">{{ dataset.case_total }}</div>
            <div class="stat-label">数据集用例（Test）</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="stat-card">
            <div class="stat-value">{{ dataset.run_total }}</div>
            <div class="stat-label">运行轮次（故障 {{ dataset.fault_run_count }} / 正常 {{ dataset.normal_run_count }}）</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="stat-card">
            <div class="stat-value">{{ dataset.log_entry_total }}</div>
            <div class="stat-label">日志条目</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" class="stat-card">
            <div class="stat-value">{{ dataset.log_window_total }}</div>
            <div class="stat-label">日志窗口</div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 图表行 -->
      <el-row :gutter="20" class="chart-row">
        <el-col :span="12">
          <el-card shadow="never">
            <template #header><span>数据集故障类型分布</span></template>
            <div ref="dsTypeChartRef" style="height: 280px;" />
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never">
            <template #header><span>故障 / 正常轮次占比</span></template>
            <div ref="dsSplitChartRef" style="height: 280px;" />
          </el-card>
        </el-col>
      </el-row>

      <!-- 图表行 -->
      <el-row :gutter="20" class="chart-row">
        <el-col :span="12">
          <el-card shadow="never">
            <template #header><span>诊断结果分布</span></template>
            <div ref="diagChartRef" style="height: 280px;" />
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never">
            <template #header><span>健康状态分布</span></template>
            <div ref="predChartRef" style="height: 280px;" />
          </el-card>
        </el-col>
      </el-row>

      <!-- 最近诊断记录 -->
      <el-card shadow="never" class="table-card">
        <template #header><span>最近诊断记录</span></template>
        <el-table :data="recentDiagnoses" size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column label="结果" width="100">
            <template #default="{ row }">
              <el-tag :type="row.is_fault ? 'danger' : 'success'" size="small">
                {{ row.is_fault ? '故障' : '正常' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="fault_type_name" label="故障类型" />
          <el-table-column label="通道" width="100">
            <template #default="{ row }">
              <el-tag :type="row.channel_used === 'fast' ? 'success' : 'warning'" size="small">
                {{ row.channel_used === 'fast' ? '⚡ 快通道' : '🧠 慢通道' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="置信度" width="80">
            <template #default="{ row }">
              {{ row.confidence ? (row.confidence * 100).toFixed(0) + '%' : '-' }}
            </template>
          </el-table-column>
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 最近预测记录 -->
      <el-card shadow="never" class="table-card">
        <template #header><span>最近预测记录</span></template>
        <el-table :data="recentPredictions" size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column label="健康状态" width="100">
            <template #default="{ row }">
              <el-tag :type="{ green: 'success', yellow: 'warning', red: 'danger' }[row.health_status]" size="small">
                {{ { green: '健康', yellow: '风险', red: '高危' }[row.health_status] }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="risk_summary" label="风险摘要" show-overflow-tooltip />
          <el-table-column label="时间" width="160">
            <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { getOverview } from '@/api/overview'

const loading = ref(false)
const stats = ref({ fault_type_count: 0, log_total: 0, log_indexed: 0, diagnosis_total: 0, prediction_total: 0 })
const dataset = ref({ case_total: 0, run_total: 0, fault_run_count: 0, normal_run_count: 0, log_entry_total: 0, log_window_total: 0, fault_type_distribution: [] })
const recentDiagnoses = ref([])
const recentPredictions = ref([])
const diagChartRef = ref(null)
const predChartRef = ref(null)
const dsTypeChartRef = ref(null)
const dsSplitChartRef = ref(null)
let diagChart = null
let predChart = null
let dsTypeChart = null
let dsSplitChart = null

function formatDate(dt) {
  if (!dt) return '-'
  const s = String(dt)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  const iso = hasTz ? s : s + 'Z'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

async function loadData() {
  loading.value = true
  try {
    const data = await getOverview()
    stats.value = data.stats
    dataset.value = data.dataset || dataset.value
    recentDiagnoses.value = data.recent_diagnoses
    recentPredictions.value = data.recent_predictions
    await nextTick()
    initDiagChart(data.diagnosis_chart)
    initPredChart(data.prediction_chart)
    initDsTypeChart(data.dataset)
    initDsSplitChart(data.dataset)
  } finally {
    loading.value = false
  }
}

function initDiagChart(d) {
  if (!diagChart) diagChart = echarts.init(diagChartRef.value)
  diagChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [
      {
        name: '故障/正常',
        type: 'pie',
        radius: ['30%', '50%'],
        data: [
          { value: d.fault_count,  name: '故障',  itemStyle: { color: '#ee6666' } },
          { value: d.normal_count, name: '正常',  itemStyle: { color: '#91cc75' } },
        ],
        label: { formatter: '{b}: {c}' },
      },
      {
        name: '诊断通道',
        type: 'pie',
        radius: ['55%', '70%'],
        data: [
          { value: d.fast_channel_count, name: '快通道', itemStyle: { color: '#5470c6' } },
          { value: d.slow_channel_count, name: '慢通道', itemStyle: { color: '#fac858' } },
        ],
        label: { formatter: '{b}: {c}' },
      },
    ],
  })
}

function initPredChart(p) {
  if (!predChart) predChart = echarts.init(predChartRef.value)
  predChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: '60%',
      data: [
        { value: p.green_count,  name: '健康',  itemStyle: { color: '#91cc75' } },
        { value: p.yellow_count, name: '风险',  itemStyle: { color: '#fac858' } },
        { value: p.red_count,    name: '高危',  itemStyle: { color: '#ee6666' } },
      ],
      label: { formatter: '{b}: {c} ({d}%)' },
    }],
  })
}

function initDsTypeChart(d) {
  if (!d) return
  if (!dsTypeChart) dsTypeChart = echarts.init(dsTypeChartRef.value)
  const items = d.fault_type_distribution || []
  dsTypeChart.setOption({
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 8, right: 28, top: 16, bottom: 8, containLabel: true },
    xAxis: { type: 'value', minInterval: 1 },
    yAxis: { type: 'category', data: items.map(i => i.fault_type), axisLabel: { fontSize: 11 } },
    series: [{
      type: 'bar',
      data: items.map(i => i.count),
      barWidth: '55%',
      itemStyle: { color: '#5470c6', borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right' },
    }],
  })
}

function initDsSplitChart(d) {
  if (!d) return
  if (!dsSplitChart) dsSplitChart = echarts.init(dsSplitChartRef.value)
  dsSplitChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['35%', '60%'],
      data: [
        { value: d.fault_run_count,  name: '故障轮', itemStyle: { color: '#ee6666' } },
        { value: d.normal_run_count, name: '正常轮', itemStyle: { color: '#91cc75' } },
      ],
      label: { formatter: '{b}: {c} ({d}%)' },
    }],
  })
}

onMounted(loadData)

onUnmounted(() => {
  diagChart?.dispose()
  predChart?.dispose()
  dsTypeChart?.dispose()
  dsSplitChart?.dispose()
  diagChart = null
  predChart = null
  dsTypeChart = null
  dsSplitChart = null
})
</script>

<style scoped>
.overview-page { display: flex; flex-direction: column; gap: 20px; }
.toolbar { display: flex; justify-content: flex-end; }
.stats-row { margin-bottom: 0; }
.stat-card { text-align: center; padding: 8px 0; }
.stat-value { font-size: 32px; font-weight: 700; color: #1e293b; line-height: 1.2; }
.stat-label { font-size: 13px; color: #64748b; margin-top: 4px; }
.chart-row { margin-top: 0; }
.table-card { }
</style>
