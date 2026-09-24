<template>
  <div class="level-alarm-page">
    <!-- 等级维护（静态配置参考） -->
    <el-card shadow="never" class="subsection-card">
      <template #header><span>等级维护</span></template>
      <el-table :data="alarmLevels" style="width: 100%">
        <el-table-column prop="level_name" label="等级名称" />
        <el-table-column label="健康状态" width="160">
          <template #default="{ row }">
            <el-tag :type="row.tag">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="desc" label="说明" />
        <el-table-column prop="processing_time" label="建议处理时限" width="140" />
      </el-table>
    </el-card>

    <!-- 预警记录（真实分级结果） -->
    <el-card shadow="never" class="subsection-card">
      <template #header>
        <div class="card-header">
          <span>预警记录（来自软件状态预测的大模型分级）</span>
          <el-button size="small" @click="loadRecords"><el-icon><Refresh /></el-icon> 刷新</el-button>
        </div>
      </template>

      <el-form :model="query" inline>
        <el-form-item label="预警等级">
          <el-select v-model="query.level" placeholder="全部" clearable style="width: 140px" @change="applyFilter">
            <el-option label="全部" value="" />
            <el-option label="一般" value="green" />
            <el-option label="严重" value="yellow" />
            <el-option label="紧急" value="red" />
          </el-select>
        </el-form-item>
      </el-form>

      <el-table :data="filteredRecords" size="small" v-loading="loading">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="run_id" label="关联报告" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.run_id || '-' }}</template>
        </el-table-column>
        <el-table-column label="预警等级" width="120">
          <template #default="{ row }">
            <el-tag :type="levelTag(row.health_status)">{{ levelLabel(row.health_status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="risk_summary" label="风险总结" min-width="280" show-overflow-tooltip />
        <el-table-column prop="created_at" label="预警时间" width="180">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button
              v-if="isSevere(row.health_status) && row.run_id"
              size="small"
              type="danger"
              @click="gotoDiagnosis(row)"
            >
              跳转诊断
            </el-button>
            <el-button size="small" type="danger" link @click="removeRecord(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pagination">
        <el-pagination v-model:current-page="page" :page-size="20" :total="total" layout="total, prev, pager, next" @current-change="loadRecords" small />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { getPredictionHistory, deletePrediction, deleteReport } from '@/api/prediction'

const router = useRouter()

const alarmLevels = ref([
  { level_name: '一般', status: 'green', tag: 'success', desc: '系统运行正常，无明显风险', processing_time: '常规巡检' },
  { level_name: '严重', status: 'yellow', tag: 'warning', desc: '存在潜在风险，需关注', processing_time: '60 分钟内' },
  { level_name: '紧急', status: 'red', tag: 'danger', desc: '高风险，可能即将发生故障', processing_time: '30 分钟内' },
])

const records = ref([])
const loading = ref(false)
const page = ref(1)
const total = ref(0)
const query = ref({ level: '' })

function levelLabel(status) {
  return { green: '一般', yellow: '严重', red: '紧急' }[status] || status
}
function levelTag(status) {
  return { green: 'success', yellow: 'warning', red: 'danger' }[status] || 'info'
}
function isSevere(status) {
  return status === 'yellow' || status === 'red'
}

const formatDate = (d) => {
  if (!d) return ''
  const s = String(d)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  return new Date(hasTz ? s : s + 'Z').toLocaleString('zh-CN')
}

const filteredRecords = computed(() => {
  if (!query.value.level) return records.value
  return records.value.filter((r) => r.health_status === query.value.level)
})

function applyFilter() {
  /* 纯前端过滤，无需重新请求 */
}

async function loadRecords() {
  loading.value = true
  try {
    const res = await getPredictionHistory({ page: page.value, page_size: 20 })
    records.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    ElMessage.error(`加载预警记录失败：${e.message || e}`)
  } finally {
    loading.value = false
  }
}

function gotoDiagnosis(row) {
  router.push({ name: 'Diagnosis', query: { run_id: row.run_id, level: row.health_status || 'yellow' } })
}

async function removeRecord(row) {
  try {
    await ElMessageBox.confirm('删除将级联移除该报告及其分级、诊断记录，确定删除吗？', '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    // 该分级关联到一份接入报告(run_id=report_id) → 级联删整份报告；
    // 无 run_id 的孤立分级记录回退按记录 ID 删除。
    if (row.run_id) {
      await deleteReport(row.run_id)
    } else {
      await deletePrediction(row.id)
    }
    ElMessage.success('已删除')
    loadRecords()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message || e}`)
  }
}

onMounted(loadRecords)
</script>

<style scoped>
.level-alarm-page { padding: 20px; }
.subsection-card { margin-bottom: 20px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.muted { color: #94a3b8; }
.pagination { margin-top: 16px; text-align: right; }
</style>
