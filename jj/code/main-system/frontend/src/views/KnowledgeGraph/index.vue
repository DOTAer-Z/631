<template>
  <div class="graph-page">
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>知识图谱</span>
          <el-button size="small" @click="loadGraph" :loading="loading">
            <el-icon><Refresh /></el-icon> 刷新图谱
          </el-button>
        </div>
      </template>

      <div v-if="loading" class="loading-area">
        <el-skeleton :rows="8" animated />
      </div>

      <div v-else-if="!hasData" class="empty-area">
        <el-empty description="知识库暂无数据，请先上传日志到知识库" />
      </div>

      <div v-else ref="chartRef" class="chart-container" />

      <div class="legend">
        <span class="legend-item fault-type">● 故障类型</span>
        <span class="legend-item log-entry">● 日志案例</span>
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
let chart = null

async function loadGraph() {
  loading.value = true
  const data = await getGraph().finally(() => (loading.value = false))
  hasData.value = data.nodes.length > 0
  if (!hasData.value) return

  await nextTick()
  if (!chart) {
    chart = echarts.init(chartRef.value)
  }

  chart.setOption({
    tooltip: {
      formatter: (params) => {
        if (params.dataType === 'node') {
          return `<b>${params.data.name}</b><br/>${params.data.value || ''}`
        }
        return ''
      },
    },
    legend: [{ data: data.categories.map((c) => c.name) }],
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
      },
    ],
  })
}

onMounted(loadGraph)
onUnmounted(() => chart?.dispose())
</script>

<style scoped>
.graph-page {}
.card-header { display: flex; align-items: center; justify-content: space-between; }
.chart-container { height: 550px; }
.loading-area { padding: 40px 0; }
.empty-area { padding: 60px 0; }
.legend { margin-top: 12px; display: flex; gap: 20px; justify-content: center; font-size: 13px; }
.fault-type { color: #5470c6; }
.log-entry { color: #ee6666; }
</style>
