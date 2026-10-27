<template>
  <div class="prediction-page">
    <!-- 预测输入区 -->
    <el-row :gutter="20">
      <el-col :span="14">
        <el-card shadow="never" class="input-card">
          <template #header><span>日志输入</span></template>
          <el-tabs v-model="inputMode">
            <el-tab-pane label="上传日志文件" name="file">
              <el-upload
                ref="uploadRef"
                :auto-upload="false"
                :limit="1"
                accept=".log,.txt,.csv,.json"
                :on-change="onFileChange"
                :on-remove="() => selectedFile = null"
                drag
              >
                <el-icon size="48" color="#94a3b8"><UploadFilled /></el-icon>
                <div class="upload-text">拖拽或点击上传日志文件</div>
              </el-upload>
            </el-tab-pane>
            <el-tab-pane label="粘贴文本" name="text">
              <el-input v-model="logText" type="textarea" :rows="7" placeholder="在此粘贴日志内容..." />
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </el-col>

      <el-col :span="10">
        <el-card shadow="never" class="metrics-card">
          <template #header><span>系统指标</span></template>
          <div class="metrics-form">
            <div v-for="m in metrics" :key="m.key" class="metric-item">
              <div class="metric-label">{{ m.label }}</div>
              <el-slider v-model="metricsData[m.key]" :max="m.max" :step="0.1" show-input input-size="small" />
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <div class="submit-row">
      <el-button type="primary" :loading="predicting" @click="doPredict" size="large">
        <el-icon><DataLine /></el-icon> 开始预测
      </el-button>
      <el-button size="large" :disabled="predicting" @click="clearAll">清空</el-button>
    </div>

    <!-- 预测结果 -->
    <el-card v-if="result" shadow="never" class="result-card">
      <template #header><span>预测结果</span></template>
      <div class="result-content">
        <div class="health-status" :class="result.health_status">
          <div class="status-circle">
            <el-icon size="36"><component :is="statusIcon(result.health_status)" /></el-icon>
          </div>
          <div class="status-info">
            <div class="status-label">{{ statusLabel(result.health_status) }}</div>
            <div class="status-summary">{{ result.risk_summary }}</div>
          </div>
        </div>

        <el-collapse v-if="result.risk_details?.length" class="risk-details">
          <el-collapse-item title="风险详情" name="1">
            <div v-for="(item, i) in result.risk_details" :key="i" class="risk-item">
              <el-tag :type="levelType(item.level)" size="small" class="risk-tag">{{ item.type }}</el-tag>
              <span class="risk-detail">{{ item.detail }}</span>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-card>

    <!-- 历史记录 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>预测历史</span>
          <el-button size="small" @click="loadHistory"><el-icon><Refresh /></el-icon> 刷新</el-button>
        </div>
      </template>
      <el-table :data="history" size="small" v-loading="historyLoading">
        <el-table-column prop="health_status" label="健康状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.health_status)" size="small">
              {{ statusLabel(row.health_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="risk_summary" label="风险摘要" show-overflow-tooltip />
        <el-table-column prop="cpu_usage" label="CPU%" width="75">
          <template #default="{ row }">{{ row.cpu_usage != null ? row.cpu_usage + '%' : '-' }}</template>
        </el-table-column>
        <el-table-column prop="memory_usage" label="内存%" width="75">
          <template #default="{ row }">{{ row.memory_usage != null ? row.memory_usage + '%' : '-' }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="165">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div class="pagination">
        <el-pagination v-model:current-page="histPage" :page-size="20" :total="histTotal" layout="total, prev, pager, next" @current-change="loadHistory" small />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { predictFile, predictText, getPredictionHistory } from '@/api/prediction'

const uploadRef = ref(null)
const inputMode = ref('file')
const selectedFile = ref(null)
const logText = ref('')
const predicting = ref(false)
const result = ref(null)

function clearAll() {
  logText.value = ''
  selectedFile.value = null
  result.value = null
  uploadRef.value?.clearFiles()
}

const metricsData = reactive({ cpu_usage: 0, memory_usage: 0, disk_usage: 0, temperature: 0 })
const metrics = [
  { key: 'cpu_usage', label: 'CPU 使用率 (%)', max: 100 },
  { key: 'memory_usage', label: '内存使用率 (%)', max: 100 },
  { key: 'disk_usage', label: '磁盘使用率 (%)', max: 100 },
  { key: 'temperature', label: '系统温度 (°C)', max: 120 },
]

const history = ref([])
const historyLoading = ref(false)
const histPage = ref(1)
const histTotal = ref(0)

const formatDate = (d) => d ? new Date(d).toLocaleString('zh-CN') : ''
const statusLabel = (s) => ({ green: '系统健康', yellow: '存在风险', red: '高风险' }[s] || s)
const statusIcon = (s) => ({ green: 'CircleCheck', yellow: 'Warning', red: 'CircleClose' }[s] || 'Warning')
const statusTagType = (s) => ({ green: 'success', yellow: 'warning', red: 'danger' }[s] || 'info')
const levelType = (l) => ({ low: 'success', medium: 'warning', high: 'danger' }[l] || 'info')

function onFileChange(file) { selectedFile.value = file.raw }

async function doPredict() {
  if (inputMode.value === 'file') {
    if (!selectedFile.value) return ElMessage.warning('请选择日志文件')
    predicting.value = true
    const fd = new FormData()
    fd.append('file', selectedFile.value)
    Object.entries(metricsData).forEach(([k, v]) => fd.append(k, v))
    result.value = await predictFile(fd).finally(() => (predicting.value = false))
  } else {
    if (!logText.value.trim()) return ElMessage.warning('请输入日志内容')
    predicting.value = true
    result.value = await predictText({ log_text: logText.value, ...metricsData }).finally(() => (predicting.value = false))
  }
  loadHistory()
}

async function loadHistory() {
  historyLoading.value = true
  const res = await getPredictionHistory({ page: histPage.value, page_size: 20 }).finally(() => (historyLoading.value = false))
  history.value = res.items
  histTotal.value = res.total
}

onMounted(loadHistory)
</script>

<style scoped>
.prediction-page { display: flex; flex-direction: column; gap: 20px; }
.metrics-form { display: flex; flex-direction: column; gap: 20px; }
.metric-item {}
.metric-label { font-size: 13px; color: #64748b; margin-bottom: 6px; font-weight: 500; }
.submit-row { display: flex; justify-content: center; }
.result-content { display: flex; flex-direction: column; gap: 16px; }
.health-status {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 20px 24px;
  border-radius: 12px;
}
.health-status.green { background: #f0fdf4; border: 2px solid #86efac; }
.health-status.yellow { background: #fffbeb; border: 2px solid #fcd34d; }
.health-status.red { background: #fef2f2; border: 2px solid #fca5a5; }
.status-circle {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.green .status-circle { background: #dcfce7; color: #16a34a; }
.yellow .status-circle { background: #fef9c3; color: #ca8a04; }
.red .status-circle { background: #fee2e2; color: #dc2626; }
.status-label { font-size: 20px; font-weight: 700; margin-bottom: 6px; }
.green .status-label { color: #15803d; }
.yellow .status-label { color: #a16207; }
.red .status-label { color: #b91c1c; }
.status-summary { color: #475569; line-height: 1.6; }
.risk-item { display: flex; align-items: flex-start; gap: 10px; margin-bottom: 10px; }
.risk-tag { flex-shrink: 0; }
.risk-detail { color: #475569; font-size: 13px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.pagination { margin-top: 12px; display: flex; justify-content: flex-end; }
.upload-text { font-size: 14px; color: #64748b; margin-top: 10px; }
</style>
