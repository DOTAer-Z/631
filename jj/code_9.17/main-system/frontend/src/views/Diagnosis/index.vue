<template>
  <div class="diagnosis-page">
    <!-- 待诊断异常文件（来自日志分析·DB5）-->
    <el-card shadow="never" class="input-card">
      <template #header><span>待诊断异常文件（来自日志分析 · DB5）</span></template>

      <el-alert
        v-if="diagRunId"
        :title="`正在对异常文件 ${diagRunId} 进行大模型根因诊断`"
        type="error"
        :closable="false"
        show-icon
      />
      <div v-if="diagRunId" class="rerun-row">
        <el-button type="primary" :loading="diagnosing" @click="rerun">
          <el-icon><Refresh /></el-icon> 重新诊断
        </el-button>
        <span class="run-tip">
          故障诊断对判定为「异常」的文件整体做根因分析（问题本质 + 处理建议），而非单条日志。
        </span>
      </div>

      <el-table
        :data="abnormalList"
        size="small"
        v-loading="abnormalLoading"
        style="margin-top: 12px;"
      >
        <el-table-column label="文件" min-width="260" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tooltip :content="row.run_id" placement="top"><span>{{ logLabel(row) }}</span></el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.source === 'upload' ? 'primary' : 'info'" effect="plain">{{ sourceLabel(row.source) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fault_score" label="异常评分" width="100">
          <template #default="{ row }">{{ row.fault_score != null ? row.fault_score : '-' }}</template>
        </el-table-column>
        <el-table-column prop="analyzed_at" label="分析时间" width="180">
          <template #default="{ row }">{{ formatDate(row.analyzed_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              :loading="diagnosing && diagRunId === row.run_id"
              @click="diagnoseRow(row)"
            >诊断</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!abnormalLoading && !abnormalList.length"
        description="暂无异常文件。请先在「在线处理 → 日志分析」分析文件，判定为「异常」的会出现在这里。"
      >
        <el-button type="primary" @click="goAnalyze">前往日志分析</el-button>
      </el-empty>

      <div class="pagination" v-if="abnormalTotal > 10">
        <el-pagination
          v-model:current-page="abPage"
          :page-size="10"
          :total="abnormalTotal"
          layout="total, prev, pager, next"
          @current-change="loadAbnormal"
          size="small"
        />
      </div>
    </el-card>

    <!-- 诊断结果 -->
    <el-card v-if="result" shadow="never" class="result-card">
      <template #header><span>诊断结果</span></template>
      <div class="result-content">
        <div class="result-top">
          <el-tag size="large" :type="result.channel_used === 'fast' ? 'success' : 'warning'" class="channel-tag">
            {{ result.channel_used === 'fast' ? '⚡ 快通道' : '🧠 慢通道 (LLM)' }}
          </el-tag>
          <el-alert
            v-if="result.is_fault"
            :title="`检测到故障：${result.fault_type_name || '未知类型'}`"
            type="error"
            :closable="false"
            show-icon
          />
          <el-alert
            v-else
            title="未检测到已知故障类型"
            type="success"
            :closable="false"
            show-icon
          />
        </div>

        <el-descriptions :column="2" border class="result-desc">
          <el-descriptions-item label="相似度得分">
            {{ result.similarity_score != null ? (result.similarity_score * 100).toFixed(1) + '%' : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="置信度">
            {{ result.confidence != null ? (result.confidence * 100).toFixed(1) + '%' : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="故障类型">
            {{ result.fault_type_name || (result.is_fault ? '未知' : '无故障') }}
          </el-descriptions-item>
          <el-descriptions-item label="诊断时间">
            {{ formatDate(result.created_at) }}
          </el-descriptions-item>
        </el-descriptions>

        <!-- 根因 / 问题本质 -->
        <div v-if="result.root_cause" class="root-cause">
          <div class="rc-title">问题本质（根因）{{ result.root_cause_type ? `（${result.root_cause_type}）` : '' }}</div>
          <div class="rc-content">{{ result.root_cause }}</div>
        </div>

        <div v-if="result.recovery_hint" class="recovery">
          <div class="recovery-title">处理建议</div>
          <div class="recovery-content">{{ result.recovery_hint }}</div>
        </div>

        <div v-if="result.llm_reasoning" class="reasoning">
          <div class="reasoning-title">LLM 推理分析</div>
          <div class="reasoning-content">{{ result.llm_reasoning }}</div>
        </div>

        <div v-if="result.reasoning_path?.length" class="reasoning-path">
          <el-collapse>
            <el-collapse-item title="推理路径（检索 / KG / 路由）" name="1">
              <pre class="path-pre">{{ result.reasoning_path.join('\n') }}</pre>
            </el-collapse-item>
          </el-collapse>
        </div>
      </div>
    </el-card>

    <!-- 历史记录 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-header">
          <span>诊断历史</span>
          <div>
            <el-button size="small" type="danger" plain @click="clearNormal">
              清除正常记录
            </el-button>
            <el-button size="small" @click="loadHistory">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
          </div>
        </div>
      </template>
      <el-table :data="history" size="small" v-loading="historyLoading" @row-click="replay" row-class-name="clickable-row">
        <el-table-column prop="is_fault" label="是否故障" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_fault ? 'danger' : 'success'" size="small">
              {{ row.is_fault ? '故障' : '正常' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fault_type_name" label="故障类型" width="130" />
        <el-table-column label="文件" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tooltip :content="row.run_id" placement="top"><span>{{ logLabel(row) }}</span></el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="100">
          <template #default="{ row }">{{ sourceLabel(row.source) }}</template>
        </el-table-column>
        <el-table-column prop="channel_used" label="通道" width="80">
          <template #default="{ row }">
            <el-tag :type="row.channel_used === 'fast' ? 'success' : 'warning'" size="small">
              {{ row.channel_used === 'fast' ? '快' : '慢' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="similarity_score" label="相似度" width="90">
          <template #default="{ row }">{{ row.similarity_score != null ? (row.similarity_score * 100).toFixed(1) + '%' : '-' }}</template>
        </el-table-column>
        <el-table-column prop="confidence" label="置信度" width="90">
          <template #default="{ row }">{{ row.confidence != null ? (row.confidence * 100).toFixed(1) + '%' : '-' }}</template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="165">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="root_cause" label="根因" min-width="200" show-overflow-tooltip>
          <template #default="{ row }">{{ row.root_cause || '-' }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="danger" link @click.stop="removeRecord(row)">删除</el-button>
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
import { diagnoseText, getDiagnosisHistory, deleteDiagnosis, deleteNormalDiagnoses } from '@/api/diagnosis'
import { deleteAnalysisRun } from '@/api/logAnalysis'
import { logLabel, sourceLabel } from '@/utils/logLabel'
import { listAnalysisResults } from '@/api/logAnalysis'

const route = useRoute()
const router = useRouter()

// 诊断目标：由「日志分析」异常判定跳转携带 run_id，或在下方异常列表点「诊断」
const diagRunId = ref(null)
const diagnosing = ref(false)
const result = ref(null)

// DB5 异常文件列表
const abnormalList = ref([])
const abnormalLoading = ref(false)
const abPage = ref(1)
const abnormalTotal = ref(0)

async function loadAbnormal() {
  abnormalLoading.value = true
  try {
    // exclude_diagnosed=true：已诊断过的文件不再出现在「待诊断异常文件」
    const res = await listAnalysisResults({ verdict: 'abnormal', exclude_diagnosed: true, page: abPage.value, page_size: 10 })
    abnormalList.value = res.items || []
    abnormalTotal.value = res.total || 0
  } catch (e) {
    ElMessage.error(`加载异常文件列表失败：${e.message || e}`)
  } finally {
    abnormalLoading.value = false
  }
}

function diagnoseRow(row) {
  if (!row?.run_id) return
  diagRunId.value = String(row.run_id)
  runDiagnose(false)
}

function goAnalyze() {
  router.push({ name: 'LogParse' })
}

const history = ref([])
const historyLoading = ref(false)
const histPage = ref(1)
const histTotal = ref(0)

const formatDate = (d) => {
  if (!d) return ''
  const s = String(d)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  return new Date(hasTz ? s : s + 'Z').toLocaleString('zh-CN')
}

async function runDiagnose(forceRefresh = false) {
  if (!diagRunId.value) return
  // 乐观移除：自动/手动诊断一开始就把该行从「待诊断」移走，避免诊断较慢期间用户重复点触发二次诊断
  abnormalList.value = abnormalList.value.filter(r => r.run_id !== diagRunId.value)
  diagnosing.value = true
  try {
    // skip_gate 默认 True（schema 已收口）：信任预测等级，直接做根因诊断
    result.value = await diagnoseText({
      log_text: '',
      run_id: diagRunId.value,
      force_refresh: forceRefresh,
    })
  } catch (e) {
    ElMessage.error(`诊断失败：${e.message || e}`)
  } finally {
    diagnosing.value = false
    loadHistory()
    // 诊断完成后刷新「待诊断异常文件」——已诊断的会被后端剔除，不再出现在列表
    loadAbnormal()
  }
}

function rerun() {
  runDiagnose(true)
}

// 点击诊断历史某一行：回放该条已存诊断结果（记录本就在 postgres，历史项已带全字段）
function replay(row) {
  if (!row) return
  result.value = row
  ElMessage.success('已回放该条诊断记录')
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const res = await getDiagnosisHistory({ page: histPage.value, page_size: 20 })
    history.value = res.items || []
    histTotal.value = res.total || 0
  } catch (e) {
    ElMessage.error(`加载诊断历史失败：${e.message || e}`)
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
    // 优先走统一级联删除（三页联通）；无 run_id 的旧记录回退删单条
    if (row.run_id) {
      await deleteAnalysisRun(row.run_id)
    } else {
      await deleteDiagnosis(row.id)
    }
    ElMessage.success('已删除')
    loadHistory()
    loadAbnormal()
  } catch (e) {
    ElMessage.error(`删除失败：${e.message || e}`)
  }
}

// 清除全部「正常 / 非故障」诊断历史（故障诊断只对 严重/紧急 报告做，结论必为故障，
// 历史里的"正常"是修复前遗留的脏数据）
async function clearNormal() {
  try {
    await ElMessageBox.confirm('将清除全部「正常/非故障」诊断记录，确定吗？', '清除确认', {
      type: 'warning', confirmButtonText: '清除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    const res = await deleteNormalDiagnoses()
    ElMessage.success(`已清除 ${res?.deleted ?? 0} 条正常记录`)
    if (histPage.value !== 1) histPage.value = 1
    loadHistory()
  } catch (e) {
    ElMessage.error(`清除失败：${e.message || e}`)
  }
}

onMounted(() => {
  loadHistory()
  const runId = route.query.run_id
  if (runId) {
    diagRunId.value = String(runId)
    // 自动诊断：不在此处单独 loadAbnormal（由 runDiagnose 的 finally 刷新，避免"未排除列表"覆盖"已排除列表"）
    runDiagnose(false)
  } else {
    loadAbnormal()
  }
})
</script>

<style scoped>
.diagnosis-page { display: flex; flex-direction: column; gap: 20px; }
.rerun-row { margin-top: 14px; display: flex; align-items: center; gap: 12px; }
.run-tip { font-size: 12px; color: #94a3b8; }
.result-content { display: flex; flex-direction: column; gap: 16px; }
.result-top { display: flex; flex-direction: column; gap: 12px; }
.channel-tag { font-size: 14px; }
.root-cause { background: #fff7ed; border-radius: 8px; padding: 16px; border-left: 4px solid #fb923c; }
.rc-title { font-weight: 600; color: #c2410c; margin-bottom: 8px; }
.rc-content { color: #475569; line-height: 1.7; white-space: pre-wrap; }
.recovery { background: #f0fdf4; border-radius: 8px; padding: 16px; border-left: 4px solid #34d399; }
.recovery-title { font-weight: 600; color: #047857; margin-bottom: 8px; }
.recovery-content { color: #475569; line-height: 1.7; white-space: pre-wrap; }
.reasoning { background: #f8fafc; border-radius: 8px; padding: 16px; border-left: 4px solid #60a5fa; }
.reasoning-title { font-weight: 600; color: #1e40af; margin-bottom: 8px; }
.reasoning-content { color: #475569; line-height: 1.7; white-space: pre-wrap; }
.path-pre { font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0; color: #475569; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.pagination { margin-top: 12px; display: flex; justify-content: flex-end; }
.clickable-row { cursor: pointer; }
</style>
