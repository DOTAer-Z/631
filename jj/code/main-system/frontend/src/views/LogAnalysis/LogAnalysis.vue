<template>
  <div class="log-analysis-page">
    <el-card shadow="never" class="result-card">
      <template #header><span>数据列表 · 筛选</span></template>
      <el-form :inline="true" class="filter-form">
        <el-form-item label="Run ID">
          <el-input
            v-model="filters.runId"
            clearable
            placeholder="nuttx_NuttX_Test_1030_round_2"
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item label="Case ID">
          <el-input
            v-model="filters.caseId"
            clearable
            placeholder="nuttx_NuttX_Test_1030"
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item label="Test Name">
          <el-input
            v-model="filters.testName"
            clearable
            placeholder="Test_1030"
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item label="故障状态">
          <el-select v-model="filters.faultStatus" clearable placeholder="全部" style="width: 120px;">
            <el-option label="故障" value="fault" />
            <el-option label="正常" value="normal" />
          </el-select>
        </el-form-item>
        <el-form-item label="数据源类型">
          <el-select v-model="filters.dataCategory" clearable placeholder="全部" style="width: 150px;">
            <el-option label="结构化数据" value="structured" />
            <el-option label="非结构化数据" value="segment" />
            <el-option label="半结构化数据" value="semi" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-row :gutter="16" class="summary-row">
      <el-col :xs="24" :sm="8">
        <el-card shadow="never" class="summary-card">
          <div class="summary-label">总 Run 数</div>
          <div class="summary-value">{{ total }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never" class="summary-card summary-fault">
          <div class="summary-label">故障 Run</div>
          <div class="summary-value">{{ faultCount }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="never" class="summary-card summary-normal">
          <div class="summary-label">正常 Run</div>
          <div class="summary-value">{{ normalCount }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="result-card">
      <template #header>
        <div class="list-header">
          <span>数据列表（训练 / 检索语料）</span>
          <div class="list-header-actions">
            <el-button size="small" type="primary" @click="goToDataImport">
              <el-icon><Plus /></el-icon> 去数据导入新建
            </el-button>
            <el-button size="small" @click="openAnnotationSummary">
              <el-icon><DataAnalysis /></el-icon> 标注库计数
            </el-button>
            <el-button
              size="small"
              type="danger"
              :disabled="!selectedRows.length"
              @click="handleBatchDelete"
            >
              批量删除（{{ selectedRows.length }}）
            </el-button>
          </div>
        </div>
      </template>
      <el-table
        ref="tableRef"
        :data="rows"
        size="small"
        v-loading="loading"
        @selection-change="handleSelectionChange"
        row-key="run_id"
      >
        <el-table-column type="selection" width="46" reserve-selection />
        <el-table-column prop="run_id" label="Run ID" min-width="260" show-overflow-tooltip>
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="{ structured: 'success', segment: 'warning', semi: 'info' }[row.data_category] || 'info'"
              style="margin-right: 6px;"
            >
              {{ { structured: '结构化', segment: '非结构化', semi: '半结构化' }[row.data_category] || '其他' }}
            </el-tag>
            <span>{{ row.run_id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="case_id" label="Case ID" min-width="200" show-overflow-tooltip />
        <el-table-column label="显示名称" min-width="160">
          <template #default="{ row }">
            <el-tooltip
              v-if="row.import_display_name || row.import_description"
              effect="dark"
              placement="top"
              :disabled="!row.import_description"
            >
              <template #content>
                <div style="max-width: 320px; white-space: pre-wrap;">{{ row.import_description || '' }}</div>
              </template>
              <span class="display-name">
                {{ row.import_display_name || row.import_filename || '-' }}
                <el-icon v-if="row.import_description" class="hint-icon"><InfoFilled /></el-icon>
              </span>
            </el-tooltip>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="标签" min-width="150">
          <template #default="{ row }">
            <template v-if="(row.import_tags || []).length">
              <el-tag
                v-for="tag in row.import_tags"
                :key="tag"
                size="small"
                effect="plain"
                class="meta-tag"
              >{{ tag }}</el-tag>
            </template>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="test_name" label="Test Name" min-width="140" show-overflow-tooltip />
        <el-table-column prop="round_no" label="Round" width="90" />
        <el-table-column prop="fault_type" label="Fault Type" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.fault_type">{{ row.fault_type }}</span>
            <el-tag v-else size="small" type="info" effect="plain">未标注</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="fault_status" label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="getFaultStatusTagType(row.fault_status)">
              {{ formatFaultStatus(row.fault_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="parsed_lines" label="已解析" width="90" />
        <el-table-column prop="total_lines" label="总行数" width="90" />
        <el-table-column prop="error_logs" label="ERROR" width="90" />
        <el-table-column prop="critical_logs" label="CRITICAL" width="100" />
        <el-table-column prop="window_count" label="窗口数" width="90" />
        <el-table-column prop="created_at" label="创建时间" min-width="180">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button link type="success" @click="goToAnalyze(row.run_id)">分析</el-button>
            <el-button link type="primary" @click="goToDetail(row.run_id)">查看</el-button>
            <el-button link type="warning" @click="openEditDialog(row)">编辑</el-button>
            <el-button link type="danger" @click="handleDeleteRun(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrap">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next"
          @current-change="loadRuns"
          @size-change="handlePageSizeChange"
        />
      </div>
    </el-card>

    <!-- 编辑标签弹窗 -->
    <el-dialog
      v-model="editDialog.visible"
      title="编辑 Run 元信息"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-form :model="editDialog.form" label-width="100px">
        <el-form-item label="Run ID">
          <el-input :value="editDialog.runId" readonly />
        </el-form-item>
        <el-form-item label="故障类型">
          <el-select
            v-model="editDialog.form.fault_type_id"
            placeholder="请选择故障类型，留空表示清除标签"
            clearable
            style="width: 100%"
            filterable
          >
            <el-option
              v-for="ft in faultTypeOptions"
              :key="ft.id"
              :label="ft.name"
              :value="ft.id"
            />
          </el-select>
          <div class="form-tip">
            选择后会同时把当前 case 标记为故障样本；可作为训练 / 检索的标签。
          </div>
        </el-form-item>
        <el-form-item label="是否故障">
          <el-radio-group v-model="editDialog.form.is_fault">
            <el-radio :value="null">不修改</el-radio>
            <el-radio :value="true">故障</el-radio>
            <el-radio :value="false">正常</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="Run 名称">
          <el-input
            v-model="editDialog.form.run_name"
            clearable
            placeholder="可选，仅本条 run 的展示名"
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="editDialog.form.description"
            type="textarea"
            :rows="3"
            placeholder="可选，写入 case 描述（同一 case 的多个 run 共享）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="editDialog.saving" @click="submitEdit">
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 标注库(DB2 data_bj)全局计数：去标注的数据进入标注库并计数，但不生成 runs/cases，
         不出现在本列表、不可被分析；此弹窗仅提供计数可见性。 -->
    <el-dialog v-model="annotationSummary.visible" title="标注库计数（data_bj）" width="460px">
      <div v-loading="annotationSummary.loading">
        <el-alert
          v-if="annotationSummary.error"
          type="error"
          :closable="false"
          :title="annotationSummary.error"
          style="margin-bottom: 12px;"
        />
        <el-descriptions v-else :column="1" border>
          <el-descriptions-item label="数据包数">{{ annotationSummary.data.total_packages ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="切片任务数">{{ annotationSummary.data.total_slice_tasks ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="窗口 / 语义段总数">{{ annotationSummary.data.total_windows ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="已标注">{{ annotationSummary.data.annotated_windows ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="待标注">{{ annotationSummary.data.pending_windows ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="正常标注数">{{ annotationSummary.data.normal_count ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="异常标注数">{{ annotationSummary.data.abnormal_count ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="数据包（结构化 / 半结构化 / 非结构化）">
            {{ annotationSummary.data.structured_packages ?? 0 }} / {{ annotationSummary.data.semi_structured_packages ?? 0 }} / {{ annotationSummary.data.unstructured_packages ?? 0 }}
          </el-descriptions-item>
          <el-descriptions-item label="窗口·语义段（结构化 / 半结构化 / 非结构化）">
            {{ annotationSummary.data.structured_windows ?? 0 }} / {{ annotationSummary.data.semi_structured_windows ?? 0 }} / {{ annotationSummary.data.unstructured_windows ?? 0 }}
          </el-descriptions-item>
        </el-descriptions>
        <p class="annotation-summary-hint">标注库数据用于标注，不计入可分析的 run 列表。</p>
      </div>
      <template #footer>
        <el-button @click="loadAnnotationSummary" :loading="annotationSummary.loading">刷新</el-button>
        <el-button type="primary" @click="annotationSummary.visible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, InfoFilled, DataAnalysis } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import {
  listDatasetRuns,
  updateDatasetRun,
  deleteDatasetRun,
  batchDeleteDatasetRuns,
} from '@/api/logAnalysis'
import { listFaultTypes, getAnnotationSummary } from '@/api/knowledgeBase'

import { buildRunListParams, formatFaultStatus, getFaultStatusTagType } from './logAnalysisTransforms'

const router = useRouter()

const loading = ref(false)
const rows = ref([])
const total = ref(0)
const faultCount = ref(0)
const normalCount = ref(0)
const filters = reactive({
  runId: '',
  caseId: '',
  testName: '',
  faultStatus: '',
  dataCategory: '',
})
const pagination = reactive({
  page: 1,
  pageSize: 20,
})

// ── CRUD 状态 ──
const tableRef = ref(null)
const selectedRows = ref([])
const faultTypeOptions = ref([])

// 标注库全局计数弹窗
const annotationSummary = reactive({
  visible: false,
  loading: false,
  error: '',
  data: {}
})
const editDialog = reactive({
  visible: false,
  saving: false,
  runId: '',
  form: {
    fault_type_id: null,
    is_fault: null,
    run_name: '',
    description: '',
  },
})

let loadRunsRequestId = 0

function formatDateTime(value) {
  if (!value) return '-'
  // 无时区后缀的 ISO 字符串视为 UTC，避免被当本地时间解析晚 8 小时
  const s = String(value)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  const iso = hasTz ? s : s + 'Z'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

async function loadRuns() {
  const requestId = ++loadRunsRequestId
  loading.value = true
  try {
    const result = await listDatasetRuns(
      buildRunListParams({
        page: pagination.page,
        pageSize: pagination.pageSize,
        runId: filters.runId.trim(),
        caseId: filters.caseId.trim(),
        testName: filters.testName.trim(),
        faultStatus: filters.faultStatus,
        dataCategory: filters.dataCategory,
      }),
    )

    if (requestId !== loadRunsRequestId) return
    rows.value = result.items || []
    total.value = result.total || 0
    faultCount.value = result.fault_count || 0
    normalCount.value = result.normal_count || 0
  } catch (error) {
    if (requestId !== loadRunsRequestId) return
    ElMessage.error(`加载运行列表失败: ${error.message}`)
  } finally {
    if (requestId === loadRunsRequestId) {
      loading.value = false
    }
  }
}

function handleSearch() {
  pagination.page = 1
  loadRuns()
}

function handleReset() {
  filters.runId = ''
  filters.caseId = ''
  filters.testName = ''
  filters.faultStatus = ''
  filters.dataCategory = ''
  pagination.page = 1
  pagination.pageSize = 20
  loadRuns()
}

function handlePageSizeChange() {
  pagination.page = 1
  loadRuns()
}

function goToDetail(runId) {
  router.push({ name: 'LogAnalysisDetail', params: { runId } })
}

// 4.2 文件选择 → 送「日志分析」：带 run_id 跳转，落地页自动解析
function goToAnalyze(runId) {
  router.push({ name: 'LogParse', query: { run_id: runId } })
}

// ── CRUD 处理 ───────────────────────────────────────────────────────────────

function handleSelectionChange(rows) {
  selectedRows.value = rows
}

async function loadFaultTypes() {
  try {
    const list = await listFaultTypes()
    faultTypeOptions.value = Array.isArray(list) ? list : (list?.items || [])
  } catch (err) {
    // 不致命：弹窗里 select 为空时用户可手动输入
    faultTypeOptions.value = []
  }
}

function goToDataImport() {
  router.push({ name: 'DataImport' }).catch(() => {
    // 路由名兜底：项目里如果是别的名字，就直接跳路径
    router.push('/data-import')
  })
}

async function loadAnnotationSummary() {
  annotationSummary.loading = true
  annotationSummary.error = ''
  try {
    annotationSummary.data = await getAnnotationSummary()
  } catch (error) {
    annotationSummary.error = `无法获取标注库计数：${error.message || error}`
    annotationSummary.data = {}
  } finally {
    annotationSummary.loading = false
  }
}

function openAnnotationSummary() {
  annotationSummary.visible = true
  loadAnnotationSummary()
}

function openEditDialog(row) {
  editDialog.runId = row.run_id
  // 把当前值预填进去：如果列表里 fault_type 有名字，去 options 里反查 id
  let curFtId = null
  if (row.fault_type) {
    const hit = faultTypeOptions.value.find((ft) => ft.name === row.fault_type)
    if (hit) curFtId = hit.id
  }
  editDialog.form.fault_type_id = curFtId
  editDialog.form.is_fault = null  // 默认"不修改"
  editDialog.form.run_name = row.run_name || ''
  editDialog.form.description = ''
  editDialog.visible = true
}

async function submitEdit() {
  const payload = {}
  // fault_type_id：null 表示用户没动；空字符串/0 表示主动清除
  if (editDialog.form.fault_type_id !== null && editDialog.form.fault_type_id !== undefined) {
    payload.fault_type_id = editDialog.form.fault_type_id || 0
  } else if (editDialog.form.fault_type_id === null) {
    // 用户用了 clearable 清空 —— 视为主动清除标签
    // 这里把"未填"和"清空"区分开：vue 里 select clearable 后是 undefined/null
    // 我们保守地不传，后端不会改这个字段
  }

  if (editDialog.form.is_fault !== null) {
    payload.is_fault = editDialog.form.is_fault
  }

  if (editDialog.form.run_name && editDialog.form.run_name.trim()) {
    payload.run_name = editDialog.form.run_name.trim()
  }

  if (editDialog.form.description && editDialog.form.description.trim()) {
    payload.description = editDialog.form.description.trim()
  }

  if (Object.keys(payload).length === 0) {
    ElMessage.info('没有可保存的修改')
    return
  }

  editDialog.saving = true
  try {
    const res = await updateDatasetRun(editDialog.runId, payload)
    ElMessage.success(`已保存（更新字段：${(res.updated_fields || []).join(', ') || '无'}）`)
    editDialog.visible = false
    loadRuns()
  } catch (err) {
    ElMessage.error(`保存失败：${err.message}`)
  } finally {
    editDialog.saving = false
  }
}

async function handleDeleteRun(row) {
  try {
    await ElMessageBox.confirm(
      `将级联删除 run "${row.run_id}" 关联的所有 log_entries / log_windows / 向量数据，确认继续？`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const res = await deleteDatasetRun(row.run_id)
    ElMessage.success(
      `已删除：log_entries ${res.deleted_log_entries} 条、log_windows ${res.deleted_log_windows} 条` +
      (res.deleted_chroma_docs ? `、向量 ${res.deleted_chroma_docs} 条` : '') +
      (res.deleted_case ? '；同时清理空 case' : ''),
    )
    tableRef.value?.clearSelection()
    loadRuns()
  } catch (err) {
    ElMessage.error(`删除失败：${err.message}`)
  }
}

async function handleBatchDelete() {
  if (!selectedRows.value.length) return
  const ids = selectedRows.value.map((r) => r.run_id)
  try {
    await ElMessageBox.confirm(
      `将级联删除选中的 ${ids.length} 条 run 及其全部关联数据，操作不可撤销，确认继续？`,
      '批量删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const res = await batchDeleteDatasetRuns({ run_ids: ids, cleanup_empty_cases: true })
    ElMessage.success(`完成：${res.deleted}/${res.requested} 条已删除`)
    if (res.not_found?.length) {
      ElMessage.warning(`其中 ${res.not_found.length} 条未找到或失败`)
    }
    tableRef.value?.clearSelection()
    loadRuns()
  } catch (err) {
    ElMessage.error(`批量删除失败：${err.message}`)
  }
}

onMounted(() => {
  loadRuns()
  loadFaultTypes()
})
</script>

<style scoped>
.log-analysis-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.muted {
  color: #94a3b8;
}

.meta-tag {
  margin-right: 4px;
  margin-bottom: 2px;
}

.display-name {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.hint-icon {
  color: #94a3b8;
  font-size: 12px;
}

.result-card {
  border-radius: 16px;
}

.section-switcher {
  width: fit-content;
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.list-header-actions {
  display: flex;
  gap: 8px;
}

.annotation-summary-hint {
  margin-top: 12px;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.5;
}

.form-tip {
  margin-top: 4px;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.5;
}

.filter-form {
  display: flex;
  flex-wrap: wrap;
}

.summary-row {
  margin: 0;
}

.summary-card {
  border-radius: 16px;
  background: linear-gradient(135deg, #fff 0%, #f8fafc 100%);
}

.summary-fault {
  background: linear-gradient(135deg, #fff1f2 0%, #ffe4e6 100%);
}

.summary-normal {
  background: linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%);
}

.summary-label {
  color: #475569;
  font-size: 13px;
}

.summary-value {
  margin-top: 6px;
  color: #0f172a;
  font-size: 28px;
  font-weight: 700;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.analysis-pane {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.quick-analysis-form {
  max-width: 960px;
}

.analysis-actions {
  display: flex;
  gap: 12px;
}

.analysis-result {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-top: 16px;
}

.analysis-result-grid {
  max-width: 960px;
}

.analysis-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.analysis-summary-label {
  color: #475569;
  font-size: 13px;
  font-weight: 600;
}

.analysis-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.analysis-pre {
  margin: 0;
  padding: 12px;
  border-radius: 12px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
