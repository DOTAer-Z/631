<template>
  <div class="software-prediction-page">
    <el-card shadow="never" class="subsection-card">
      <template #header>
        <div class="card-header">
          <span>软件状态预测（大模型分级）</span>
          <el-button size="small" :loading="loading" @click="loadReports">
            <el-icon><Refresh /></el-icon> 刷新接入报告
          </el-button>
        </div>
      </template>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="软件状态预测的对象是「多源状态数据接入」后生成的接入报告整体（当前数据源为接入日志，其他源预留）。请选择一份报告，用大模型对其运行状态进行分级（一般 / 严重 / 紧急）；分级结果已存入数据库，点击任意报告行可回放其分级详情；分级为严重或紧急时，可一键跳转故障诊断（对该报告做根因分析）。"
        style="margin-bottom: 16px;"
      />

      <el-table :data="reports" size="small" v-loading="loading" @row-click="viewGradeDetail" row-class-name="clickable-row">
        <el-table-column label="报告ID" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ reportKey(row) || '-' }}</template>
        </el-table-column>
        <el-table-column prop="accessTime" label="接入时间" min-width="170">
          <template #default="{ row }">{{ formatDate(row.accessTime) }}</template>
        </el-table-column>
        <el-table-column prop="source" label="数据来源" width="130">
          <template #default="{ row }">{{ sourceLabel(row.source) }}</template>
        </el-table-column>
        <el-table-column prop="totalLogs" label="抽取数量" width="90" />
        <el-table-column prop="taggedLogs" label="标记数量" width="90" />
        <el-table-column label="分级结果" min-width="120">
          <template #default="{ row }">
            <template v-if="gradeOf(row)">
              <el-tag :type="levelTag(gradeOf(row).health_status)" effect="dark">
                {{ levelLabel(gradeOf(row).health_status) }}
              </el-tag>
            </template>
            <span v-else class="muted">未分级</span>
          </template>
        </el-table-column>
        <el-table-column label="风险说明" min-width="240">
          <template #default="{ row }">
            <span v-if="gradeOf(row)">{{ gradeOf(row).risk_summary || '-' }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="320" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              :loading="gradingId === reportKey(row)"
              @click.stop="doGrade(row)"
            >
              {{ gradeOf(row) ? '重新分级' : '大模型分级' }}
            </el-button>
            <el-button
              v-if="gradeOf(row) && isSevere(gradeOf(row).health_status)"
              size="small"
              type="danger"
              @click.stop="gotoDiagnosis(row)"
            >
              跳转诊断
            </el-button>
            <el-button size="small" type="danger" link @click.stop="removeReport(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!loading && !reports.length" description="暂无接入报告，请先在「多源状态数据接入」接入数据生成报告" />

      <div class="pagination">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="loadReports"
          small
        />
      </div>
    </el-card>

    <!-- 分级详情 -->
    <el-card v-if="activeGrade" shadow="never" class="subsection-card">
      <template #header><span>分级详情 — {{ activeReportName }}</span></template>
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
        <el-button type="danger" @click="gotoDiagnosisById(activeGrade.run_id, activeGrade.health_status)">
          该报告状态为{{ levelLabel(activeGrade.health_status) }}，前往故障诊断（大模型根因）
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { getAccessResults, gradeReport, getPredictionByRun, deleteReport } from '@/api/prediction'

const router = useRouter()

const reports = ref([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)
const gradingId = ref(null)
// report_id -> grade result
const grades = ref({})
const activeGrade = ref(null)
const activeReportName = ref('')

// 报告唯一键：优先 report_id，回退 run_id / id
function reportKey(row) {
  return row.report_id || row.run_id || row.id
}
function gradeOf(row) {
  return grades.value[reportKey(row)] || null
}

// green=一般 / yellow=严重 / red=紧急
function levelLabel(status) {
  return { green: '一般', yellow: '严重', red: '紧急' }[status] || status
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
function sourceLabel(s) {
  return { database: '日志分析结果库', file: '文件上传' }[s] || s || '-'
}

const formatDate = (d) => {
  if (!d) return ''
  const s = String(d)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  return new Date(hasTz ? s : s + 'Z').toLocaleString('zh-CN')
}

async function loadReports() {
  loading.value = true
  try {
    const res = await getAccessResults({ page: page.value, pageSize: pageSize.value })
    reports.value = res.items || []
    total.value = res.total || 0
    await loadGrades()
  } catch (e) {
    ElMessage.error(`加载接入报告失败：${e.message || e}`)
  } finally {
    loading.value = false
  }
}

// 从数据库回显各报告已存的分级结果（分级本就落库，离开页面后回来仍可见）
async function loadGrades() {
  const keys = reports.value.map(reportKey).filter(Boolean)
  const map = { ...grades.value }
  await Promise.all(
    keys.map(async (k) => {
      try {
        const res = await getPredictionByRun(k)
        if (res && res.health_status) map[k] = res
      } catch {
        /* 该报告暂无分级记录，跳过 */
      }
    })
  )
  grades.value = map
}

// 点击某报告行：回放它已存的分级详情（不重新调用大模型）
function viewGradeDetail(row) {
  const g = gradeOf(row)
  if (!g) {
    ElMessage.info('该报告尚未分级，请点击「大模型分级」')
    return
  }
  activeGrade.value = g
  activeReportName.value = `${sourceLabel(row.source)} · ${formatDate(row.accessTime)}`
}

async function doGrade(row) {
  const key = reportKey(row)
  if (!key) {
    ElMessage.warning('该报告缺少 report_id，无法分级，请重新接入生成新报告')
    return
  }
  gradingId.value = key
  try {
    const res = await gradeReport({ report_id: key })
    grades.value = { ...grades.value, [key]: res }
    activeGrade.value = res
    activeReportName.value = `${sourceLabel(row.source)} · ${formatDate(row.accessTime)}`
    ElMessage.success(`分级完成：${levelLabel(res.health_status)}`)
  } catch (e) {
    ElMessage.error(`分级失败：${e.message || e}`)
  } finally {
    gradingId.value = null
  }
}

function gotoDiagnosis(row) {
  const g = gradeOf(row)
  gotoDiagnosisById(reportKey(row), g?.health_status || 'yellow')
}
function gotoDiagnosisById(reportId, status) {
  router.push({ name: 'Diagnosis', query: { run_id: reportId, level: status || 'yellow' } })
}

// 级联删除整份报告（连带分级、诊断记录），三页同时消失
async function removeReport(row) {
  const key = reportKey(row)
  if (!key) {
    ElMessage.warning('该报告缺少 report_id，无法删除')
    return
  }
  try {
    await ElMessageBox.confirm('删除将级联移除该报告及其分级、诊断记录，确定删除吗？', '删除确认', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await deleteReport(key)
    const map = { ...grades.value }
    delete map[key]
    grades.value = map
    if (activeGrade.value && activeGrade.value.run_id === key) {
      activeGrade.value = null
      activeReportName.value = ''
    }
    ElMessage.success('已删除')
    loadReports()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message || e}`)
  }
}

loadReports()
</script>

<style scoped>
.software-prediction-page { padding: 20px; }
.subsection-card { margin-bottom: 20px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.muted { color: #94a3b8; }
.pagination { margin-top: 16px; text-align: right; }
.risk-details { margin-top: 16px; }
.rd-title { font-size: 13px; font-weight: 600; color: #475569; margin-bottom: 8px; }
.goto-row { margin-top: 16px; }
.clickable-row { cursor: pointer; }
</style>
