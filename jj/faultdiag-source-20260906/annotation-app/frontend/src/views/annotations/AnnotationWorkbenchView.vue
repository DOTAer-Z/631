<template>
  <div class="page-shell">
    <div class="page-toolbar">
      <div>
        <div class="page-toolbar__title">数据标注工作台</div>
        <div class="page-toolbar__hint">统计待标注窗口，支持按左侧窗口选择进行标注，并在同页查看已标注记录</div>
      </div>

      <el-space>
        <el-button data-testid="open-fault-types-button" @click="goFaultTypes">故障类型管理</el-button>
        <el-select v-model="exportScope" data-testid="annotation-export-scope-select" style="width: 140px">
          <el-option label="当前筛选" value="current_filter" />
          <el-option label="全部数据" value="all" />
        </el-select>
        <el-select v-model="exportMode" data-testid="annotation-export-mode-select" style="width: 140px">
          <el-option label="轻量导出" value="light" />
          <el-option label="全量导出" value="full" />
        </el-select>
        <el-button data-testid="export-current-json" :loading="store.exportLoading" @click="handleExport('json')">
          导出 JSON
        </el-button>
      </el-space>
    </div>

    <div class="stats-grid">
      <el-card shadow="never" class="console-card stat-card">
        <div class="stat-card__value">{{ store.stats.total_annotations }}</div>
        <div class="stat-card__label">总标注数</div>
      </el-card>
      <el-card shadow="never" class="console-card stat-card">
        <div class="stat-card__value">{{ store.pendingTotal }}</div>
        <div class="stat-card__label">待标注窗口</div>
      </el-card>
      <el-card shadow="never" class="console-card stat-card">
        <div class="stat-card__value">{{ store.stats.normal_count }}</div>
        <div class="stat-card__label">normal</div>
      </el-card>
      <el-card shadow="never" class="console-card stat-card">
        <div class="stat-card__value">{{ store.stats.abnormal_count }}</div>
        <div class="stat-card__label">abnormal</div>
      </el-card>
    </div>

    <el-row :gutter="20">
      <el-col :span="9">
        <el-card shadow="never" class="console-card workbench-panel pending-panel">
          <template #header>
            <div class="panel-header">
              <span>待标注列表</span>
              <span class="panel-header__meta">{{ store.pendingTotal }} 条</span>
            </div>
          </template>

          <el-form inline @submit.prevent class="pending-toolbar">
            <el-form-item label="package">
              <el-input
                v-model.number="pendingFilterForm.package_id"
                clearable
                style="width: 100px"
                data-testid="pending-filter-package-id"
              />
            </el-form-item>
            <el-form-item label="task">
              <el-input
                v-model.number="pendingFilterForm.task_id"
                clearable
                style="width: 100px"
                data-testid="pending-filter-task-id"
              />
            </el-form-item>
            <el-form-item label="行数">
              <el-input
                v-model.number="pendingFilterForm.min_line_count"
                placeholder="≥"
                clearable
                style="width: 80px"
                data-testid="pending-filter-min-line-count"
              />
              <span style="margin: 0 6px">-</span>
              <el-input
                v-model.number="pendingFilterForm.max_line_count"
                placeholder="≤"
                clearable
                style="width: 80px"
                data-testid="pending-filter-max-line-count"
              />
            </el-form-item>
            <el-form-item label="关键字">
              <el-input
                v-model="pendingFilterForm.keyword"
                clearable
                placeholder="搜索日志内容"
                style="width: 160px"
                data-testid="pending-filter-keyword"
              />
            </el-form-item>
            <el-form-item label="排序">
              <el-select
                v-model="pendingFilterForm.sort_by"
                style="width: 130px"
                data-testid="pending-filter-sort-by"
              >
                <el-option label="时间" value="window_start_ts" />
                <el-option label="行数" value="line_count" />
              </el-select>
              <el-select
                v-model="pendingFilterForm.sort_order"
                style="width: 100px; margin-left: 6px"
                data-testid="pending-filter-sort-order"
              >
                <el-option label="升序" value="asc" />
                <el-option label="降序" value="desc" />
              </el-select>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" data-testid="pending-filter-submit" @click="applyPendingFilters">查询</el-button>
              <el-button data-testid="pending-filter-reset" @click="resetPendingFilters">重置</el-button>
            </el-form-item>
          </el-form>

          <el-table :data="store.pendingItems" empty-text="暂无待标注窗口" class="pending-table" @row-click="selectPending">
            <el-table-column prop="window_id" label="窗口" width="90" />
            <el-table-column prop="task_id" label="任务" width="80" />
            <el-table-column prop="line_count" label="行数" width="80" />
            <el-table-column label="时间范围" min-width="220">
              <template #default="{ row }">{{ formatRange(row.window_start_ts, row.window_end_ts) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="170" fixed="right">
              <template #default="{ row }">
                <el-space :size="6">
                  <el-button
                    size="small"
                    data-testid="pending-view-window-button"
                    @click.stop="viewPendingWindow(row)"
                  >
                    查看
                  </el-button>
                  <el-button
                    size="small"
                    type="danger"
                    plain
                    :loading="discardingWindowId === row.window_id"
                    data-testid="pending-discard-button"
                    @click.stop="confirmDiscardPending(row)"
                  >
                    删除
                  </el-button>
                </el-space>
              </template>
            </el-table-column>
          </el-table>

          <div class="pending-pagination">
            <span class="pending-pagination__meta">第 {{ pendingPage }} / {{ pendingTotalPages }} 页，共 {{ store.pendingTotal }} 条</span>
            <el-space>
              <el-button :disabled="pendingPage <= 1" data-testid="pending-prev-page" @click="goPendingPrevPage">上一页</el-button>
              <el-button :disabled="pendingPage >= pendingTotalPages" data-testid="pending-next-page" @click="goPendingNextPage">下一页</el-button>
            </el-space>
          </div>
        </el-card>
      </el-col>

      <el-col :span="15">
        <el-card shadow="never" class="console-card workbench-panel annotation-panel">
          <template #header>
            <div class="panel-header">
              <span>数据标注</span>
              <span class="panel-header__meta">{{ currentWindowLabel }}</span>
            </div>
          </template>

          <el-form label-width="110px">
            <el-form-item label="绑定对象">
              <el-select v-model="form.source_log_file_id" data-testid="annotation-binding-select" clearable placeholder="整窗口">
                <!-- Whole-window is represented by an empty selection (null); ElOption
                     rejects a null value prop, so rely on clearable + placeholder. -->
                <el-option
                  v-for="file in windowFiles"
                  :key="file.source_file_id"
                  :label="file.path"
                  :value="file.source_file_id"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="标签">
              <el-select v-model="form.label" data-testid="annotation-label-select">
                <el-option label="normal" value="normal" />
                <el-option label="abnormal" value="abnormal" />
              </el-select>
            </el-form-item>

            <el-form-item v-if="form.label === 'abnormal'" label="异常类型">
              <el-select
                v-model="form.anomaly_type"
                filterable
                :placeholder="faultTypeStore.items.length ? '选择已定义的故障类型' : '请先在故障类型管理中定义'"
                :disabled="!faultTypeStore.items.length"
                data-testid="annotation-anomaly-select"
              >
                <el-option
                  v-for="item in faultTypeStore.items"
                  :key="item.id"
                  :label="item.name"
                  :value="item.name"
                />
              </el-select>
            </el-form-item>

            <el-form-item label="备注">
              <el-input v-model="form.note" type="textarea" :rows="5" maxlength="20000" show-word-limit data-testid="annotation-note-input" />
            </el-form-item>

            <el-form-item>
              <el-space>
                <el-button type="primary" :loading="store.saving" data-testid="annotation-save-button" @click="saveCurrent(false)">保存</el-button>
                <el-button type="primary" plain :loading="store.saving" data-testid="annotation-save-next-button" @click="saveCurrent(true)">
                  保存并下一窗口
                </el-button>
                <el-button
                  :loading="recommendationLoading"
                  :disabled="currentPendingIndex < 0"
                  data-testid="recommend-button"
                  @click="requestRecommendation"
                >
                  智能推荐
                </el-button>
                <el-button
                  :loading="multiAnalysisLoading"
                  :disabled="currentPendingIndex < 0"
                  data-testid="multi-analyze-button"
                  @click="requestMultiAnalysis"
                >
                  智能分析(多错误)
                </el-button>
                <el-button :disabled="currentPendingIndex <= 0" @click="goPrev">上一窗口</el-button>
                <el-button :disabled="currentPendingIndex < 0 || currentPendingIndex >= store.pendingItems.length - 1" @click="goNext">
                  下一窗口
                </el-button>
                <el-button
                  v-if="store.currentAnnotation"
                  type="danger"
                  :loading="store.deleting"
                  data-testid="annotation-delete-button"
                  @click="deleteCurrent"
                >
                  删除
                </el-button>
              </el-space>
            </el-form-item>
          </el-form>

          <el-card
            v-if="recommendation"
            shadow="never"
            class="recommendation-card"
            data-testid="recommendation-card"
          >
            <div class="recommendation-card__header">
              <span class="recommendation-card__title">大模型推荐(仅供参考)</span>
              <span v-if="recommendation.model" class="recommendation-card__model">{{ recommendation.model }}</span>
            </div>
            <template v-if="recommendation.status === 'success'">
              <div class="recommendation-card__row">
                <span class="recommendation-card__key">建议标签</span>
                <span data-testid="recommendation-label">{{ recommendation.recommended_label }}</span>
              </div>
              <div v-if="recommendation.recommended_anomaly_type" class="recommendation-card__row">
                <span class="recommendation-card__key">建议故障类型</span>
                <span data-testid="recommendation-anomaly-type">{{ recommendation.recommended_anomaly_type }}</span>
              </div>
              <div
                v-if="recommendation.pending_suggestion_id"
                class="recommendation-card__row"
                data-testid="recommendation-suggestion-banner"
              >
                <span class="recommendation-card__key">新故障类型</span>
                <span class="recommendation-card__suggestion">
                  LLM 在已定义类型外提出了新建议（pending #{{ recommendation.pending_suggestion_id }}）
                </span>
              </div>
              <div class="recommendation-card__row">
                <span class="recommendation-card__key">推荐理由</span>
                <span data-testid="recommendation-reason">{{ recommendation.reason || '-' }}</span>
              </div>
              <div class="recommendation-card__actions">
                <el-button type="primary" plain data-testid="recommendation-adopt-button" @click="adoptRecommendation">
                  采纳到表单
                </el-button>
                <el-button
                  v-if="recommendation.pending_suggestion_id"
                  data-testid="recommendation-review-suggestion-button"
                  @click="goReviewSuggestions"
                >
                  去审核新故障类型
                </el-button>
              </div>
            </template>
            <div v-else class="recommendation-card__row" data-testid="recommendation-error">
              推荐不可用：{{ recommendation.error_message || '请检查大模型配置' }}
            </div>
          </el-card>

          <el-card
            v-if="multiAnalysis"
            shadow="never"
            class="recommendation-card"
            data-testid="multi-analysis-card"
          >
            <div class="recommendation-card__header">
              <span class="recommendation-card__title">多错误分析(仅供参考)</span>
              <span v-if="multiAnalysis.model" class="recommendation-card__model">{{ multiAnalysis.model }}</span>
            </div>
            <template v-if="multiAnalysis.status === 'success'">
              <div class="recommendation-card__row">
                <span class="recommendation-card__key">多个故障</span>
                <span>{{ multiAnalysis.multiple_faults ? '是' : '否' }}</span>
              </div>
              <div class="recommendation-card__row">
                <span class="recommendation-card__key">建议细分</span>
                <span>
                  {{ multiAnalysis.suggest_subdivide ? '建议' : '不需要' }}
                  <template v-if="multiAnalysis.suggest_subdivide && multiAnalysis.suggested_window_seconds != null">
                    （建议子窗口长度：每 <strong>{{ multiAnalysis.suggested_window_seconds }}</strong> 秒）
                  </template>
                  <el-button
                    v-if="multiAnalysis.suggest_subdivide"
                    link
                    type="primary"
                    data-testid="multi-analysis-subdivide-button"
                    @click="goSubdivideCurrent"
                  >
                    去细分该窗口
                  </el-button>
                </span>
              </div>
              <el-table :data="multiAnalysis.files" data-testid="multi-analysis-table">
                <el-table-column prop="logical_path" label="文件" min-width="180" />
                <el-table-column label="标签" width="90">
                  <template #default="{ row }">
                    <el-tag :type="row.label === 'abnormal' ? 'danger' : 'success'">
                      {{ row.label === 'abnormal' ? '异常' : '正常' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="anomaly_type" label="故障类型" min-width="120">
                  <template #default="{ row }">{{ row.anomaly_type || '-' }}</template>
                </el-table-column>
                <el-table-column prop="reason" label="理由" min-width="160" show-overflow-tooltip />
                <el-table-column label="操作" width="140">
                  <template #default="{ row, $index }">
                    <el-button size="small" data-testid="multi-analysis-adopt-button" @click="adoptFileSuggestion(row, $index)">
                      采纳为该文件标注
                    </el-button>
                  </template>
                </el-table-column>
              </el-table>
            </template>
            <div v-else class="recommendation-card__row" data-testid="multi-analysis-error">
              分析不可用：{{ multiAnalysis.error_message || '请检查大模型配置' }}
            </div>
          </el-card>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="console-card workbench-panel records-panel">
      <template #header>
        <div class="panel-header">
          <span>已标注记录</span>
          <span class="panel-header__meta">{{ store.total }} 条</span>
        </div>
      </template>

      <el-form inline @submit.prevent class="records-toolbar">
        <el-form-item label="package">
          <el-input v-model.number="filters.package_id" clearable data-testid="record-filter-package-id" />
        </el-form-item>
        <el-form-item label="task">
          <el-input v-model.number="filters.task_id" clearable data-testid="record-filter-task-id" />
        </el-form-item>
        <el-form-item label="label">
          <el-select v-model="filters.label" clearable data-testid="record-filter-label">
            <el-option label="normal" value="normal" />
            <el-option label="abnormal" value="abnormal" />
          </el-select>
        </el-form-item>
        <el-form-item label="anomaly_type">
          <el-input v-model="filters.anomaly_type" clearable data-testid="record-filter-anomaly-type" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" data-testid="record-filter-submit" @click="applyRecordFilters">查询</el-button>
          <el-button data-testid="record-filter-reset" @click="resetRecordFilters">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table :data="store.items" v-loading="store.loadingList" empty-text="暂无标注记录" @sort-change="handleRecordSortChange">
        <el-table-column prop="id" label="ID" width="80" sortable="custom" />
        <el-table-column prop="package_id" label="package" width="100" />
        <el-table-column prop="task_id" label="task" width="100" />
        <el-table-column prop="window_id" label="window" width="100" />
        <el-table-column label="时间范围" min-width="240" prop="window_start_ts" sortable="custom">
          <template #default="{ row }">{{ formatRange(row.window_start_ts, row.window_end_ts) }}</template>
        </el-table-column>
        <el-table-column prop="label" label="label" width="110" sortable="custom" />
        <el-table-column prop="anomaly_type" label="anomaly_type" width="180" sortable="custom">
          <template #default="{ row }">{{ row.anomaly_type || '-' }}</template>
        </el-table-column>
        <el-table-column prop="note" label="note" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ row.note || '-' }}</template>
        </el-table-column>
        <el-table-column label="updated_at" min-width="180" prop="updated_at" sortable="custom">
          <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-space>
              <el-button size="small" data-testid="record-edit-button" @click="openEdit(row)">编辑</el-button>
              <el-button
                size="small"
                type="danger"
                :loading="store.deleting"
                data-testid="record-delete-button"
                @click="confirmDelete(row)"
              >
                删除
              </el-button>
              <el-button size="small" data-testid="record-view-window-button" @click="viewWindow(row)">
                查看窗口
              </el-button>
            </el-space>
          </template>
        </el-table-column>
      </el-table>

      <div class="records-pagination">
        <span class="records-pagination__meta">第 {{ store.page }} / {{ recordsTotalPages }} 页，共 {{ store.total }} 条</span>
        <el-pagination
          background
          layout="prev, pager, next"
          :page-size="store.pageSize"
          :total="store.total"
          :current-page="store.page"
          data-testid="records-pagination"
          @current-change="goRecordsPage"
        />
      </div>
    </el-card>

    <el-dialog
      v-model="editDialogVisible"
      title="编辑标注"
      width="520px"
      :close-on-click-modal="false"
    >
      <el-form :model="editForm" label-width="100px">
        <el-form-item label="标签">
          <el-select v-model="editForm.label" data-testid="record-edit-label-select">
            <el-option label="normal" value="normal" />
            <el-option label="abnormal" value="abnormal" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="editForm.label === 'abnormal'" label="异常类型">
          <el-select
            v-model="editForm.anomaly_type"
            filterable
            :placeholder="faultTypeStore.items.length ? '选择已定义的故障类型' : '请先在故障类型管理中定义'"
            :disabled="!faultTypeStore.items.length"
            data-testid="record-edit-anomaly-select"
          >
            <el-option
              v-for="item in faultTypeStore.items"
              :key="item.id"
              :label="item.name"
              :value="item.name"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="editForm.note"
            type="textarea"
            :rows="5"
            maxlength="20000"
            show-word-limit
            data-testid="record-edit-note-input"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="store.saving"
          data-testid="record-edit-submit-button"
          @click="submitEdit"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { triggerAnnotationExportDownload } from '@annotation/api/annotations'
import { requestWindowRecommendation, analyzeWindowMulti, getStoredWindowAnalysis } from '@annotation/api/recommendations'
import { getSliceWindowFull } from '@annotation/api/sliceWindows'
import { useAnnotationStore } from '@annotation/stores/annotationStore'
import { useFaultTypeStore } from '@annotation/stores/faultTypeStore'
import type {
  AnnotationExportMode,
  AnnotationExportScope,
  AnnotationLabel,
  AnnotationListItem,
  PendingAnnotationItem,
  PendingSortField,
  PendingSortOrder
} from '@annotation/types/annotation'
import type { Recommendation, WindowMultiAnalysis } from '@annotation/types/recommendation'

const router = useRouter()
const store = useAnnotationStore()
const faultTypeStore = useFaultTypeStore()
const selectedPendingWindowId = ref<number | null>(null)
// Stable context for the window currently open in the workbench. Unlike
// currentPendingIndex (which is derived from the pending list), this survives
// the window leaving the pending list after its first annotation — needed so
// multiple file-level annotations can be adopted in a row.
const selectedWindowCtx = ref<{ package_id: number; task_id: number; window_id: number } | null>(null)
const pendingPage = ref(1)
const pendingPageSize = 10
const exportScope = ref<AnnotationExportScope>('current_filter')
const exportMode = ref<AnnotationExportMode>('light')
const recommendation = ref<Recommendation | null>(null)
const recommendationLoading = ref(false)
const multiAnalysis = ref<WindowMultiAnalysis | null>(null)
const multiAnalysisLoading = ref(false)
// Indices of multi-analysis file rows already adopted; once every row is adopted
// the window is fully annotated and gets removed from the pending list.
const adoptedFileRows = ref<Set<number>>(new Set())

const editDialogVisible = ref(false)
const editingRecordId = ref<number | null>(null)
const editForm = reactive<{
  label: AnnotationLabel
  anomaly_type: string | null
  note: string
}>({
  label: 'normal',
  anomaly_type: null,
  note: ''
})

const form = reactive<{
  label: AnnotationLabel
  anomaly_type: string | null
  note: string
  source_log_file_id: number | null
}>({
  label: 'normal',
  anomaly_type: null,
  note: '',
  source_log_file_id: null
})

// Files available in the current window, for file-level annotation binding.
const windowFiles = ref<Array<{ source_file_id: number; path: string }>>([])

const filters = reactive<{
  package_id: number | undefined
  task_id: number | undefined
  label: AnnotationLabel | undefined
  anomaly_type: string | undefined
}>({
  package_id: undefined,
  task_id: undefined,
  label: undefined,
  anomaly_type: undefined
})

const pendingFilterForm = reactive<{
  package_id: number | undefined
  task_id: number | undefined
  min_line_count: number | undefined
  max_line_count: number | undefined
  keyword: string
  sort_by: PendingSortField
  sort_order: PendingSortOrder
}>({
  package_id: undefined,
  task_id: undefined,
  min_line_count: undefined,
  max_line_count: undefined,
  keyword: '',
  sort_by: 'window_start_ts',
  sort_order: 'asc'
})

const discardingWindowId = ref<number | null>(null)

const currentPendingIndex = computed(() => {
  if (!selectedPendingWindowId.value) return -1
  return store.pendingItems.findIndex((item) => item.window_id === selectedPendingWindowId.value)
})

const pendingTotalPages = computed(() => {
  return Math.max(1, Math.ceil(store.pendingTotal / pendingPageSize))
})

const currentWindowLabel = computed(() => {
  const current = store.pendingItems[currentPendingIndex.value]
  if (!current) return '当前未选择窗口'
  return `window #${current.window_id}`
})

watch(
  () => form.label,
  (value) => {
    if (value === 'normal') {
      form.anomaly_type = null
    }
  }
)

watch(
  () => editForm.label,
  (value) => {
    if (value === 'normal') {
      editForm.anomaly_type = null
    }
  }
)

function goFaultTypes() {
  router.push({ name: 'fault-type-manage' })
}

function goReviewSuggestions() {
  router.push({ name: 'fault-type-suggestion-list' })
}

function viewWindow(row: { package_id: number; task_id: number; window_id: number }) {
  void router.push({
    name: 'slice-window-browser',
    params: { id: row.package_id, taskId: row.task_id },
    query: { window_id: row.window_id, from: 'annotation' }
  })
}

function viewPendingWindow(row: PendingAnnotationItem) {
  viewWindow(row)
}

function openEdit(row: AnnotationListItem) {
  editingRecordId.value = row.id
  editForm.label = row.label
  editForm.anomaly_type = row.anomaly_type || null
  editForm.note = row.note || ''
  editDialogVisible.value = true
}

async function submitEdit() {
  if (editingRecordId.value === null) return
  if (editForm.label === 'abnormal' && !editForm.anomaly_type) {
    ElMessage.error('abnormal 标注必须选择已定义的故障类型')
    return
  }
  const payload = {
    label: editForm.label,
    anomaly_type: editForm.label === 'normal' ? null : editForm.anomaly_type?.trim() || null,
    note: editForm.note.trim() || null
  }
  try {
    await store.updateCurrent(editingRecordId.value, payload)
    await store.refreshWorkbenchData()
    editDialogVisible.value = false
    ElMessage.success('编辑保存成功')
  } catch (error) {
    const detail = (error as { response?: { data?: { message?: string } } }).response?.data?.message
    ElMessage.error(detail || '编辑保存失败')
  }
}

async function confirmDelete(row: AnnotationListItem) {
  try {
    await ElMessageBox.confirm(`确认删除窗口 ${row.window_id} 的标注?`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await store.deleteCurrent(row.id)
    await store.refreshWorkbenchData()
    ElMessage.success('标注删除成功')
  } catch {
    ElMessage.error('标注删除失败')
  }
}

function formatRange(start: number, end: number) {
  return `${formatTimestamp(start)} ~ ${formatTimestamp(end)}`
}

function formatTimestamp(value: number) {
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function normalizeFilters() {
  return {
    package_id: Number.isFinite(filters.package_id) ? filters.package_id : undefined,
    task_id: Number.isFinite(filters.task_id) ? filters.task_id : undefined,
    label: filters.label,
    anomaly_type: filters.anomaly_type?.trim() || undefined
  }
}

function resetForm() {
  form.label = 'normal'
  form.anomaly_type = null
  form.note = ''
}

function syncFormFromCurrent() {
  const current = store.currentAnnotation
  form.label = current?.label || 'normal'
  form.anomaly_type = current?.anomaly_type || null
  form.note = current?.note || ''
  form.source_log_file_id = null
}

async function refreshPendingPageAndSelect(
  page: number,
  mode: 'first' | 'last' | 'index' = 'first',
  index = 0
) {
  const normalizedPage = Math.max(1, page)
  await store.fetchPending({ page: normalizedPage, page_size: pendingPageSize })
  pendingPage.value = normalizedPage

  const items = store.pendingItems
  if (!items.length) {
    selectedPendingWindowId.value = null
    store.currentAnnotation = null
    resetForm()
    return
  }

  const target =
    mode === 'last'
      ? items[items.length - 1]
      : mode === 'index'
        ? items[Math.min(index, items.length - 1)]
        : items[0]

  await selectPending(target)
}

async function loadWorkbench() {
  try {
    await store.refreshWorkbenchData()
    await refreshPendingPageAndSelect(1, 'first')
  } catch {
    ElMessage.error('加载标注工作台失败')
  }
}

async function selectPending(item: PendingAnnotationItem) {
  try {
    selectedPendingWindowId.value = item.window_id
    selectedWindowCtx.value = {
      package_id: item.package_id,
      task_id: item.task_id,
      window_id: item.window_id
    }
    recommendation.value = null
    multiAnalysis.value = null
    adoptedFileRows.value = new Set()
    await store.fetchForWindow(item.window_id)
    await loadWindowFiles(item.window_id)
    syncFormFromCurrent()
    // 回显已持久化的多错误分析（无则保持空，不报错）
    void loadStoredAnalysis(item.window_id)
  } catch {
    ElMessage.error('加载窗口标注失败')
  }
}

async function loadStoredAnalysis(windowId: number) {
  try {
    const stored = await getStoredWindowAnalysis(windowId)
    // 期间用户可能已切到别的窗口，避免错位回填
    if (selectedWindowCtx.value?.window_id === windowId) {
      multiAnalysis.value = stored
    }
  } catch {
    // 404 = 该窗口尚无分析记录，正常情况，忽略
  }
}

async function loadWindowFiles(windowId: number) {
  windowFiles.value = []
  form.source_log_file_id = null
  try {
    const full = await getSliceWindowFull(windowId)
    const files: Array<{ source_file_id: number; path: string }> = []
    const walk = (nodes: typeof full.tree.root) => {
      for (const node of nodes) {
        if (node.type === 'file' && node.source_file_id != null) {
          files.push({ source_file_id: node.source_file_id, path: node.path || node.name })
        }
        walk(node.children || [])
      }
    }
    walk(full.tree.root)
    windowFiles.value = files
  } catch {
    // Binding selector simply stays whole-window only if files can't be loaded.
  }
}

async function requestRecommendation() {
  const current = store.pendingItems[currentPendingIndex.value]
  if (!current) {
    ElMessage.warning('当前没有可推荐窗口')
    return
  }
  recommendationLoading.value = true
  try {
    recommendation.value = await requestWindowRecommendation(current.window_id)
    if (recommendation.value?.status === 'success') {
      ElMessage.success('已生成推荐')
    } else {
      ElMessage.warning('推荐不可用，请检查大模型配置')
    }
  } catch {
    ElMessage.error('生成推荐失败')
  } finally {
    recommendationLoading.value = false
  }
}

async function requestMultiAnalysis() {
  const ctx = selectedWindowCtx.value
  if (!ctx) {
    ElMessage.warning('当前没有可分析窗口')
    return
  }
  multiAnalysisLoading.value = true
  try {
    adoptedFileRows.value = new Set()
    multiAnalysis.value = await analyzeWindowMulti(ctx.window_id)
    if (multiAnalysis.value?.status !== 'success') {
      ElMessage.warning('多错误分析不可用，请检查大模型配置')
    }
  } catch {
    ElMessage.error('多错误分析失败')
  } finally {
    multiAnalysisLoading.value = false
  }
}

async function adoptFileSuggestion(file: WindowMultiAnalysis['files'][number], rowIndex: number) {
  // Target the analyzed window directly, not the pending-list index: the window
  // drops out of the pending list after its first annotation, so each
  // subsequent file must still resolve to the same window to be adoptable.
  const windowId = multiAnalysis.value?.window_id ?? selectedWindowCtx.value?.window_id
  if (!windowId) {
    return
  }
  try {
    await store.createForWindow(windowId, {
      label: file.label,
      anomaly_type: file.label === 'abnormal' ? file.anomaly_type : null,
      note: file.reason,
      source_log_file_id: file.source_log_file_id
    })
    // Light refresh only — do NOT reload the pending list here, or the current
    // window would disappear mid-adopt and break the next file's adoption.
    await store.fetchStats()
    await store.fetchList({ page: 1 })
    ElMessage.success(`已为 ${file.logical_path} 采纳标注`)

    // Once every per-file suggestion has been adopted, the window is fully
    // annotated — reload the pending list so it drops out (and stats update).
    adoptedFileRows.value.add(rowIndex)
    const totalRows = multiAnalysis.value?.files.length ?? 0
    if (totalRows > 0 && adoptedFileRows.value.size >= totalRows) {
      // Capture position before refresh — refreshWorkbenchData reloads the
      // pending list, dropping this now-annotated window and shifting indices.
      const savedPage = pendingPage.value
      const savedIndex = Math.max(0, currentPendingIndex.value)
      await store.refreshWorkbenchData()
      await refreshPendingPageAndSelect(savedPage, 'index', savedIndex)
      ElMessage.success('该窗口所有文件标注已采纳，已从待标注列表移除')
    }
  } catch {
    ElMessage.error('采纳文件标注失败')
  }
}

function goSubdivideCurrent() {
  const ctx = selectedWindowCtx.value
  if (!ctx) {
    return
  }
  const query: Record<string, string | number> = { window_id: ctx.window_id, from: 'annotation' }
  // Carry the AI-suggested sub-window length so the browser can pre-fill the dialog.
  const suggestedSeconds = multiAnalysis.value?.suggested_window_seconds
  if (suggestedSeconds != null) {
    query.suggest_seconds = suggestedSeconds
  }
  void router.push({
    name: 'slice-window-browser',
    params: { id: ctx.package_id, taskId: ctx.task_id },
    query
  })
}

function adoptRecommendation() {
  const rec = recommendation.value
  if (!rec || rec.status !== 'success' || !rec.recommended_label) {
    return
  }
  form.label = rec.recommended_label
  form.anomaly_type = rec.recommended_label === 'abnormal' ? rec.recommended_anomaly_type : null
  // Carry the recommendation reason into the note so it's saved alongside the label.
  if (rec.reason) {
    form.note = rec.reason
  }
  ElMessage.success('已采纳到表单，请确认后保存')
}

async function saveCurrent(goNextAfterSave: boolean) {
  const current = store.pendingItems[currentPendingIndex.value]
  if (!current) {
    ElMessage.warning('当前没有可标注窗口')
    return
  }

  const savedIndex = currentPendingIndex.value
  const savedPage = pendingPage.value
  const shouldAdvancePage = goNextAfterSave && savedIndex >= store.pendingItems.length - 1 && savedPage < pendingTotalPages.value

  if (form.label === 'abnormal' && !form.anomaly_type) {
    ElMessage.error('abnormal 标注必须选择 anomaly_type')
    return
  }

  const anomalyType = form.label === 'normal' ? null : form.anomaly_type?.trim() || null

  if (form.label === 'abnormal' && !anomalyType) {
    ElMessage.error('abnormal 标注必须填写 anomaly_type')
    return
  }

  const payload = {
    label: form.label,
    anomaly_type: anomalyType,
    note: form.note.trim() || null,
    source_log_file_id: form.source_log_file_id
  }

  try {
    if (form.source_log_file_id != null) {
      // File-level annotation: backend upserts by (window, file) composite key.
      await store.createForWindow(current.window_id, payload)
    } else if (store.currentAnnotation) {
      await store.updateCurrent(store.currentAnnotation.id, payload)
    } else {
      await store.createForWindow(current.window_id, payload)
    }
    await store.refreshWorkbenchData()
    if (shouldAdvancePage) {
      await refreshPendingPageAndSelect(savedPage + 1, 'first')
    } else {
      await refreshPendingPageAndSelect(savedPage, 'index', savedIndex)
    }
    ElMessage.success('标注保存成功')
    if (goNextAfterSave) {
      await goNext()
    }
  } catch {
    ElMessage.error('标注保存失败')
  }
}

async function deleteCurrent() {
  if (!store.currentAnnotation) {
    return
  }

  const savedIndex = currentPendingIndex.value
  const savedPage = pendingPage.value

  try {
    await store.deleteCurrent(store.currentAnnotation.id)
    await store.refreshWorkbenchData()
    await refreshPendingPageAndSelect(savedPage, 'index', savedIndex)
    ElMessage.success('标注删除成功')
  } catch {
    ElMessage.error('标注删除失败')
  }
}

async function goPrev() {
  const previous = store.pendingItems[currentPendingIndex.value - 1]
  if (previous) {
    await selectPending(previous)
    return
  }
  if (pendingPage.value > 1) {
    await refreshPendingPageAndSelect(pendingPage.value - 1, 'last')
  }
}

async function goNext() {
  const next = store.pendingItems[currentPendingIndex.value + 1]
  if (next) {
    await selectPending(next)
    return
  }
  if (pendingPage.value < pendingTotalPages.value) {
    await refreshPendingPageAndSelect(pendingPage.value + 1, 'first')
  }
}

async function goPendingPrevPage() {
  if (pendingPage.value <= 1) return
  try {
    await refreshPendingPageAndSelect(pendingPage.value - 1, 'first')
  } catch {
    ElMessage.error('加载待标注上一页失败')
  }
}

async function goPendingNextPage() {
  if (pendingPage.value >= pendingTotalPages.value) return
  try {
    await refreshPendingPageAndSelect(pendingPage.value + 1, 'first')
  } catch {
    ElMessage.error('加载待标注下一页失败')
  }
}

function normalizePendingFilters() {
  const trimmedKeyword = pendingFilterForm.keyword.trim()
  return {
    package_id: Number.isFinite(pendingFilterForm.package_id) ? pendingFilterForm.package_id : undefined,
    task_id: Number.isFinite(pendingFilterForm.task_id) ? pendingFilterForm.task_id : undefined,
    min_line_count: Number.isFinite(pendingFilterForm.min_line_count) ? pendingFilterForm.min_line_count : undefined,
    max_line_count: Number.isFinite(pendingFilterForm.max_line_count) ? pendingFilterForm.max_line_count : undefined,
    keyword: trimmedKeyword || undefined,
    sort_by: pendingFilterForm.sort_by,
    sort_order: pendingFilterForm.sort_order
  }
}

async function applyPendingFilters() {
  store.setPendingFilters(normalizePendingFilters())
  try {
    await refreshPendingPageAndSelect(1, 'first')
  } catch {
    ElMessage.error('加载待标注列表失败')
  }
}

async function resetPendingFilters() {
  pendingFilterForm.package_id = undefined
  pendingFilterForm.task_id = undefined
  pendingFilterForm.min_line_count = undefined
  pendingFilterForm.max_line_count = undefined
  pendingFilterForm.keyword = ''
  pendingFilterForm.sort_by = 'window_start_ts'
  pendingFilterForm.sort_order = 'asc'
  await applyPendingFilters()
}

async function confirmDiscardPending(row: PendingAnnotationItem) {
  try {
    await ElMessageBox.confirm(
      `确认删除窗口 ${row.window_id}？删除后该窗口将从待标注列表移除（重新切片可恢复）。`,
      '删除待标注窗口',
      { type: 'warning' }
    )
  } catch {
    return
  }

  const savedIndex = currentPendingIndex.value
  const savedPage = pendingPage.value
  discardingWindowId.value = row.window_id
  try {
    await store.discardPendingWindow(row.window_id)
    if (selectedPendingWindowId.value === row.window_id) {
      selectedPendingWindowId.value = null
      store.currentAnnotation = null
      resetForm()
    }
    await store.fetchStats()
    await refreshPendingPageAndSelect(savedPage, 'index', savedIndex)
    ElMessage.success('已删除待标注窗口')
  } catch (error) {
    const detail = (error as { response?: { data?: { message?: string } } }).response?.data?.message
    ElMessage.error(detail || '删除待标注窗口失败')
  } finally {
    discardingWindowId.value = null
  }
}

async function handleExport(format: 'csv' | 'json') {
  try {
    const result = await store.exportWithFilters(format, exportScope.value, exportMode.value)
    if (result?.download_url) {
      triggerAnnotationExportDownload(result.download_url, result.file_name)
    }
    ElMessage.success(`导出成功，共 ${result?.item_count ?? 0} 条`)
  } catch {
    ElMessage.error('导出失败')
  }
}

async function applyRecordFilters() {
  try {
    store.setFilters(normalizeFilters())
    await store.fetchList({ page: 1 })
    await store.fetchStats()
    await store.fetchPending()
    await store.fetchWorkbench()
  } catch {
    ElMessage.error('查询标注记录失败')
  }
}

async function resetRecordFilters() {
  filters.package_id = undefined
  filters.task_id = undefined
  filters.label = undefined
  filters.anomaly_type = undefined
  await applyRecordFilters()
}

const recordsTotalPages = computed(() => Math.max(1, Math.ceil(store.total / (store.pageSize || 20))))

async function goRecordsPage(page: number) {
  try {
    await store.fetchList({ page })
  } catch {
    ElMessage.error('加载标注记录失败')
  }
}

const _RECORD_SORT_PROPS = new Set(['id', 'label', 'anomaly_type', 'updated_at', 'window_start_ts'])

async function handleRecordSortChange(payload: { prop?: string; order?: string | null }) {
  // Element Plus emits order = 'ascending' | 'descending' | null (cleared).
  const prop = payload.prop
  if (!prop || !_RECORD_SORT_PROPS.has(prop) || !payload.order) {
    // Cleared sort -> fall back to default updated_at desc.
    await store.fetchList({ page: 1, sort_by: 'updated_at', sort_order: 'desc' })
    return
  }
  const order = payload.order === 'ascending' ? 'asc' : 'desc'
  try {
    await store.fetchList({ page: 1, sort_by: prop as never, sort_order: order })
  } catch {
    ElMessage.error('排序失败')
  }
}

onMounted(() => {
  void faultTypeStore.fetchAll().catch(() => ElMessage.error('加载故障类型失败'))
  void loadWorkbench()
})

defineExpose({ viewPendingWindow, confirmDiscardPending, applyPendingFilters, goReviewSuggestions })
</script>

<style scoped>
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 20px;
}

.stat-card :deep(.el-card__body) {
  padding: 24px 20px;
}

.stat-card__value {
  font-size: 30px;
  font-weight: 700;
  color: #0f172a;
}

.stat-card__label {
  margin-top: 8px;
  font-size: 13px;
  color: #64748b;
}

.workbench-panel {
  min-height: 320px;
}

.pending-panel :deep(.el-card__body),
.annotation-panel :deep(.el-card__body),
.records-panel :deep(.el-card__body) {
  padding: 22px 24px;
}

.pending-table {
  margin-top: 4px;
}

.pending-pagination {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.records-pagination {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.records-pagination__meta {
  font-size: 13px;
  color: #64748b;
}

.pending-pagination__meta {
  font-size: 13px;
  color: #64748b;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
}

.panel-header__meta {
  font-size: 13px;
  color: #64748b;
}

.records-panel {
  margin-top: 24px;
}

.records-toolbar {
  margin-bottom: 20px;
}

.annotation-panel :deep(.el-form-item) {
  margin-bottom: 18px;
}

.annotation-panel :deep(.el-form-item:last-child) {
  margin-top: 10px;
}

.recommendation-card {
  margin-top: 16px;
  border: 1px dashed #94a3b8;
  background: #f8fafc;
}

.recommendation-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-weight: 600;
}

.recommendation-card__model {
  font-size: 12px;
  color: #64748b;
  font-weight: 400;
}

.recommendation-card__row {
  display: flex;
  gap: 12px;
  margin-bottom: 6px;
  font-size: 13px;
  color: #0f172a;
}

.recommendation-card__key {
  flex: 0 0 88px;
  color: #64748b;
}

.recommendation-card__actions {
  margin-top: 12px;
}

@media (max-width: 960px) {
  .pending-pagination {
    flex-direction: column;
    align-items: flex-start;
  }
}

@media (max-width: 960px) {
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
}
</style>
