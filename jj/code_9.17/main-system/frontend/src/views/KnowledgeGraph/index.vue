<template>
  <div class="graph-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>知识图谱</span>
          <div class="header-actions">
            <el-radio-group v-model="viewMode" size="small" @change="loadGraph">
              <el-radio-button value="overview">总览</el-radio-button>
              <el-radio-button value="fault_type">按故障类型</el-radio-button>
              <el-radio-button value="root_cause">按根因</el-radio-button>
              <el-radio-button value="subsystem">按子系统</el-radio-button>
              <el-radio-button value="case">按案例</el-radio-button>
            </el-radio-group>
            <el-input
              v-model="focus"
              size="small"
              clearable
              placeholder="焦点：故障类型 / 根因 / 子系统 / run_id"
              style="width: 260px;"
              @keyup.enter="loadGraph"
            />
            <el-button size="small" @click="loadGraph" :loading="loading">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
          </div>
        </div>
      </template>

      <el-alert
        v-if="loadError"
        type="error"
        :closable="false"
        :title="loadError"
        style="margin-bottom: 12px;"
      >
        <template #default>
          <div style="font-size: 12px; line-height: 1.7;">
            图谱文件位于后端容器 <code>/app/outputs/kg/</code>。
            该 run 尚未入库时先到「在线处理 → 文件选择」勾选已标注故障类型的 run，
            点「批量入知识库」。
          </div>
        </template>
      </el-alert>

      <!--
        ★ 图表容器必须【始终渲染】：原来用 v-else 挂在 loading/hasData 之后，
        首次渲染时容器还不存在，echarts.init(ref) 拿到 null →
        "Cannot read properties of null (reading 'getAttribute')"。
        改成 v-show 后 DOM 一直在，init 一定有节点可用。
      -->
      <div ref="chartRef" v-show="!loading && hasData" class="chart-container" />

      <div v-if="loading" class="loading-area">
        <el-skeleton :rows="8" animated />
      </div>

      <div v-else-if="!hasData" class="empty-area">
        <el-empty description="知识图谱暂无数据">
          <div class="empty-hint">
            在「在线处理 → 文件选择」勾选已标注故障类型的 run，点「批量入知识库」即可生成图谱节点。
          </div>
        </el-empty>
      </div>

      <div v-if="hasData" class="legend">
        <span
          v-for="(cat, i) in categories"
          :key="cat"
          class="legend-item"
          :style="{ color: catColor(i) }"
        >● {{ cat }}（{{ countByCategory(i) }}）</span>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import * as echarts from 'echarts'
import { getGraph } from '@/api/graph'

const chartRef = ref(null)
const loading = ref(false)
const hasData = ref(false)
const loadError = ref('')
const viewMode = ref('overview')
const focus = ref('')
const categories = ref([])
const catCounts = ref([])
let chart = null
let resizeObserver = null

// 与后端 graph_service._NODE_TYPE_STYLE / _KG_CATEGORIES 的配色保持一致
const CAT_COLORS = [
  '#5470c6', '#91cc75', '#ee6666', '#fac858',
  '#73c0de', '#3ba272', '#fc8452', '#9a60b4', '#ea7ccc',
]
const catColor = (i) => CAT_COLORS[i] || '#ea7ccc'
const countByCategory = (i) => catCounts.value[i] ?? 0

function ensureChart() {
  if (!chartRef.value) return null
  if (!chart) {
    chart = echarts.init(chartRef.value)
    // 容器被 v-show 隐藏过再显示时尺寸会变成 0，用 ResizeObserver 兜住
    if (typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(() => chart?.resize())
      resizeObserver.observe(chartRef.value)
    }
  }
  return chart
}

async function loadGraph() {
  loading.value = true
  loadError.value = ''
  try {
    const data = await getGraph({
      view: viewMode.value,
      focus: focus.value.trim() || undefined,
    })
    hasData.value = (data.nodes || []).length > 0
    categories.value = (data.categories || []).map((c) => c.name)
    const counts = new Array(categories.value.length).fill(0)
    for (const n of data.nodes || []) {
      if (typeof n.category === 'number' && counts[n.category] !== undefined) {
        counts[n.category] += 1
      }
    }
    catCounts.value = counts
    if (!hasData.value) return

    loading.value = false
    await nextTick()
    const inst = ensureChart()
    if (!inst) return
    inst.resize()

    inst.setOption(
      {
        tooltip: {
          formatter: (params) => {
            if (params.dataType === 'node') {
              const d = params.data || {}
              const lines = [`<b>${d.label || d.name}</b>`]
              if (d.type) lines.push(`类型：${d.type}`)
              const props = d.data || {}
              for (const key of ['run_id', 'case_id', 'window_total', 'log_entry_id']) {
                if (props[key] !== undefined && props[key] !== null && props[key] !== '') {
                  lines.push(`${key}：${props[key]}`)
                }
              }
              if (d.value && d.value !== d.name && d.value !== d.label) {
                lines.push(String(d.value).slice(0, 200))
              }
              return lines.join('<br/>')
            }
            return params.data?.label ? `关系：${params.data.label}` : ''
          },
        },
        legend: [{ data: categories.value, top: 0 }],
        series: [
          {
            type: 'graph',
            layout: 'force',
            data: data.nodes,
            links: data.edges,
            categories: data.categories,
            roam: true,
            draggable: true,
            label: { show: true, position: 'right', fontSize: 12 },
            force: { repulsion: 200, gravity: 0.05, edgeLength: 120 },
            lineStyle: { color: '#94a3b8', width: 1.5, curveness: 0.1 },
            emphasis: { focus: 'adjacency' },
            edgeLabel: {
              show: false,
              formatter: (p) => p.data?.label || '',
            },
          },
        ],
      },
      true, // notMerge：切换视图时清掉旧 series，避免节点残留
    )
  } catch (error) {
    loadError.value = `加载知识图谱失败：${error.message || error}`
    hasData.value = false
  } finally {
    loading.value = false
    // 兜底：nextTick 之外再排一次，覆盖「首帧容器尚未拿到尺寸」的情况
    nextTick(() => {
      if (hasData.value && !loadError.value) chart?.resize()
    })
  }
}

onMounted(loadGraph)
onUnmounted(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.card-header { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px; }
.header-actions { display: flex; gap: 8px; align-items: center; }
.chart-container { height: 550px; width: 100%; }
.loading-area { padding: 40px 0; }
.empty-area { padding: 60px 0; }
.empty-hint { font-size: 12px; color: #94a3b8; }
.legend { margin-top: 12px; display: flex; gap: 16px; justify-content: center; font-size: 13px; flex-wrap: wrap; }
.legend-item { white-space: nowrap; }
</style>
