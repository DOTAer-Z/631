<template>
  <div class="prediction-page">
    <!-- 待预测正常文件（来自日志分析·DB5）-->
    <el-card shadow="never" class="subsection-card">
      <template #header>
        <div class="card-header">
          <span>预测预警 · 正常文件状态分级（来自日志分析 · DB5）</span>
          <el-button size="small" :loading="loading" @click="loadNormal">
            <el-icon><Refresh /></el-icon> 刷新
          </el-button>
        </div>
      </template>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="预测预警面向「日志分析」判定为『正常』的文件，做前瞻性风险预测与预警：大模型评估其潜在风险与劣化趋势、未来是否可能演变为故障，给出预警等级（无预警 / 需关注 / 高风险）与需关注项。分级结果已存入数据库，点击任意行可回放；若预警为高风险，可一键跳转故障诊断做深入分析。"
        style="margin-bottom: 16px;"
      />

      <el-table :data="normalList" size="small" v-loading="loading" @row-click="viewGrade" row-class-name="clickable-row">
        <el-table-column label="文件" min-width="260" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tooltip :content="row.run_id" placement="top">
              <span>{{ logLabel(row) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.source === 'upload' ? 'primary' : 'info'" effect="plain">{{ sourceLabel(row.source) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="analyzed_at" label="分析时间" width="170">
          <template #default="{ row }">{{ formatDate(row.analyzed_at) }}</template>
        </el-table-column>
        <el-table-column label="分级结果" min-width="110">
          <template #default="{ row }">
            <el-tag v-if="gradeOf(row)" :type="levelTag(gradeOf(row).health_status)" effect="dark">
              {{ levelLabel(gradeOf(row).health_status) }}
            </el-tag>
            <span v-else class="muted">未分级</span>
          </template>
        </el-table-column>
        <el-table-column label="风险说明" min-width="240">
          <template #default="{ row }">
            <span v-if="gradeOf(row)">{{ gradeOf(row).risk_summary || '-' }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              :loading="gradingId === row.run_id"
              @click.stop="doGrade(row)"
            >
              {{ gradeOf(row) ? '重新分级' : '大模型分级' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!loading && !normalList.length"
        description="暂无正常文件。请先在「在线处理 → 日志分析」分析文件，判定为『正常』的会出现在这里。"
      >
        <el-button type="primary" @click="goAnalyze">前往日志分析</el-button>
      </el-empty>

      <div class="pagination" v-if="normalTotal > 10">
        <el-pagination
          v-model:current-page="page"
          :page-size="10"
          :total="normalTotal"
          layout="total, prev, pager, next"
          @current-change="loadNormal"
          size="small"
        />
      </div>
    </el-card>

    <!-- 分级详情 -->
    <el-card v-if="activeGrade" shadow="never" class="subsection-card">
      <template #header><span>分级详情 — {{ activeName }}</span></template>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="状态等级">
          <el-tag :type="levelTag(activeGrade.health_status)" effect="dark">{{ levelLabel(activeGrade.health_status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="风险总结">{{ activeGrade.risk_summary || '-' }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="activeGrade.risk_details?.length" class="risk-details">
        <div class="rd-title">风险明细</div>
        <el-table :data="activeGrade.risk_details" size="small" border>
          <el-table-column prop="type" label="类型" width="160" />
          <el-table-column prop="level" label="级别" width="120">
            <template #default="{ row }">
              <el-tag :type="riskLevelTag(row.level)" size="small">{{ row.level }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="detail" label="描述" />
        </el-table>
      </div>
      <div v-if="isSevere(activeGrade.health_status)" class="goto-row">
        <el-alert
          :title="`该文件预警等级为【${levelLabel(activeGrade.health_status)}】——请参考上方「风险明细」中的规避/预防措施提前处置；预测预警已由大模型给出建议，无需转故障诊断。`"
          :type="activeGrade.health_status === 'red' ? 'error' : 'warning'"
          :closable="false"
          show-icon
        />
      </div>
    </el-card>

    <!-- 预警记录（分级历史）-->
    <el-card shadow="never" class="subsection-card">
      <template #header>
        <div class="card-header">
          <span>预警记录</span>
          <el-button size="small" @click="loadHistory">
            <el-icon><Refresh /></el-icon> 刷新
          </el-button>
        </div>
      </template>
      <el-table :data="history" size="small" v-loading="historyLoading" @row-click="viewHistoryGrade" row-class-name="clickable-row">
        <el-table-column label="文件" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tooltip :content="row.run_id" placement="top"><span>{{ logLabel(row) }}</span></el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="100">
          <template #default="{ row }">{{ sourceLabel(row.source) }}</template>
        </el-table-column>
        <el-table-column label="状态等级" width="110">
          <template #default="{ row }">
            <el-tag :type="levelTag(row.health_status)" effect="dark" size="small">{{ levelLabel(row.health_status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="risk_summary" label="风险说明" min-width="240" show-overflow-tooltip />
        <el-table-column prop="created_at" label="时间" width="170">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="danger" link @click="removeRecord(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pagination">
        <el-pagination v-model:current-page="histPage" :page-size="20" :total="histTotal" layout="total, prev, pager, next" @current-change="loadHistory" size="small" />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { useRoute, useRouter } from 'vue-router'
import { gradeRun, getPredictionByRun, getPredictionHistory, deletePrediction } from '@/api/prediction'
import { listAnalysisResults, deleteAnalysisRun } from '@/api/logAnalysis'
import { logLabel, sourceLabel } from '@/utils/logLabel'

const route = useRoute()
const router = useRouter()

// DB5 正常文件列表
const normalList = ref([])
const loading = ref(false)
const page = ref(1)
const normalTotal = ref(0)
const gradingId = ref(null)
// run_id -> grade result
const grades = ref({})
const activeGrade = ref(null)
const activeName = ref('')

// 预警记录（分级历史）
const history = ref([])
const historyLoading = ref(false)
const histPage = ref(1)
const histTotal = ref(0)

// 预测预警是「预警」语义（对象都是正常文件），用预警措辞而非"故障"措辞：
// green=无预警 / yellow=需关注 / red=高风险
function levelLabel(status) {
  return { green: '无预警', yellow: '需关注', red: '高风险' }[status] || status || '-'
}
function levelTag(status) {
  return { green: 'success', yellow: 'warning', red: 'danger' }[status] || 'info'
}
function isSevere(status) {
  return status === 'yellow' || status === 'red'
}
function riskLevelTag(level) {
  return { high: 'danger', medium: 'warning', low: 'info' }[String(level).toLowerCase()] || 'info'
}
function gradeOf(row) {
  return grades.value[row.run_id] || null
}

const formatDate = (d) => {
  if (!d) return ''
  const s = String(d)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  return new Date(hasTz ? s : s + 'Z').toLocaleString('zh-CN')
}

async function loadNormal() {
  loading.value = true
  try {
    const res = await listAnalysisResults({ verdict: 'normal', page: page.value, page_size: 10 })
    normalList.value = res.items || []
    normalTotal.value = res.total || 0
    await loadGrades()
  } catch (e) {
    ElMessage.error(`加载正常文件列表失败：${e.message || e}`)
  } finally {
    loading.value = false
  }
}

// 回显各文件已存分级（分级本就落库，离开页面回来仍可见）
async function loadGrades() {
  const keys = normalList.value.map((r) => r.run_id).filter(Boolean)
  const map = { ...grades.value }
  await Promise.all(
    keys.map(async (k) => {
      try {
        const res = await getPredictionByRun(k)
        if (res && res.health_status) map[k] = res
      } catch {
        /* 暂无分级记录，跳过 */
      }
    })
  )
  grades.value = map
}

function viewGrade(row) {
  const g = gradeOf(row)
  if (!g) {
    ElMessage.info('该文件尚未分级，请点击「大模型分级」')
    return
  }
  activeGrade.value = g
  activeName.value = row.filename || row.run_id
}

// 点击「预警记录」某行：回显该条分级详情（行本身即含 health_status/risk_summary/risk_details）
function viewHistoryGrade(row) {
  if (!row || !row.health_status) return
  activeGrade.value = row
  activeName.value = logLabel(row)
}

async function doGrade(row) {
  if (!row?.run_id) return
  gradingId.value = row.run_id
  try {
    const res = await gradeRun({ run_id: row.run_id })
    grades.value = { ...grades.value, [row.run_id]: res }
    activeGrade.value = res
    activeName.value = row.filename || row.run_id
    ElMessage.success(`分级完成：${levelLabel(res.health_status)}`)
    loadHistory()
  } catch (e) {
    ElMessage.error(`分级失败：${e.message || e}`)
  } finally {
    gradingId.value = null
  }
}

function goAnalyze() {
  router.push({ name: 'LogParse' })
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const res = await getPredictionHistory({ page: histPage.value, page_size: 20 })
    history.value = res.items || []
    histTotal.value = res.total || 0
  } catch (e) {
    ElMessage.error(`加载预警记录失败：${e.message || e}`)
  } finally {
    historyLoading.value = false
  }
}

async function removeRecord(row) {
  try {
    await ElMessageBox.confirm('删除将联通移除该文件的分析/诊断/预警记录（数据库原始数据保留），确定吗？', '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    if (row.run_id) {
      await deleteAnalysisRun(row.run_id)
    } else {
      await deletePrediction(row.id)
    }
    ElMessage.success('已删除')
    loadHistory()
    loadNormal()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message || e}`)
  }
}

onMounted(() => {
  loadNormal()
  loadHistory()
  // 从「日志分析」正常判定跳转携带 run_id 时，自动对其分级
  const rid = route.query.run_id
  if (rid) {
    doGrade({ run_id: String(rid), filename: String(rid) })
  }
})
</script>

<style scoped>
.prediction-page { display: flex; flex-direction: column; gap: 20px; padding: 0; }
.subsection-card { margin-bottom: 0; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.muted { color: #94a3b8; }
.pagination { margin-top: 16px; text-align: right; }
.risk-details { margin-top: 16px; }
.rd-title { font-size: 13px; font-weight: 600; color: #475569; margin-bottom: 8px; }
.goto-row { margin-top: 16px; }
.clickable-row { cursor: pointer; }
</style>
