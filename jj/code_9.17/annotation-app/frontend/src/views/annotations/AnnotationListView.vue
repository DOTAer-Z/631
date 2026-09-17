<template>
  <div class="page-shell">
    <div class="page-toolbar">
      <div>
        <div class="page-toolbar__title">已标注记录</div>
        <div class="page-toolbar__hint">用于查询历史标注、筛选结果并从当前过滤条件导出数据</div>
      </div>

      <el-space>
        <el-button :loading="store.exportLoading" data-testid="export-csv-button" @click="handleExport('csv', 'current_filter')">
          导出 CSV
        </el-button>
        <el-button :loading="store.exportLoading" data-testid="export-json-button" @click="handleExport('json', 'all')">
          导出 JSON
        </el-button>
      </el-space>
    </div>

    <el-card shadow="never" class="console-card">
      <el-form inline @submit.prevent>
        <el-form-item label="package">
          <el-input v-model.number="filters.package_id" clearable data-testid="filter-package-id" />
        </el-form-item>
        <el-form-item label="task">
          <el-input v-model.number="filters.task_id" clearable data-testid="filter-task-id" />
        </el-form-item>
        <el-form-item label="label">
          <el-select v-model="filters.label" clearable data-testid="filter-label">
            <el-option label="normal" value="normal" />
            <el-option label="abnormal" value="abnormal" />
          </el-select>
        </el-form-item>
        <el-form-item label="anomaly_type">
          <el-input v-model="filters.anomaly_type" clearable data-testid="filter-anomaly-type" />
        </el-form-item>
        <el-form-item label="start_ts">
          <el-input v-model.number="filters.start_ts" clearable data-testid="filter-start-ts" />
        </el-form-item>
        <el-form-item label="end_ts">
          <el-input v-model.number="filters.end_ts" clearable data-testid="filter-end-ts" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" data-testid="filter-submit" @click="applyFilters">查询</el-button>
          <el-button data-testid="filter-reset" @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>

      <el-alert
        v-if="store.lastExport"
        type="success"
        :closable="false"
        show-icon
        :title="`导出完成 (${store.lastExport.format} / ${store.lastExport.scope})，文件: ${store.lastExport.file_path}`"
        class="export-alert"
      />

      <el-table :data="store.items" v-loading="store.loadingList" empty-text="暂无标注">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="package_id" label="package" width="100" />
        <el-table-column prop="task_id" label="task" width="100" />
        <el-table-column prop="window_id" label="window" width="100" />
        <el-table-column label="时间范围" min-width="240">
          <template #default="{ row }">{{ formatRange(row.window_start_ts, row.window_end_ts) }}</template>
        </el-table-column>
        <el-table-column prop="label" label="label" width="110" />
        <el-table-column prop="anomaly_type" label="anomaly_type" width="140">
          <template #default="{ row }">{{ row.anomaly_type || '-' }}</template>
        </el-table-column>
        <el-table-column prop="note" label="note" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ row.note || '-' }}</template>
        </el-table-column>
        <el-table-column label="updated_at" min-width="180">
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

      <div class="pagination">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next"
          :current-page="store.page"
          :page-size="store.pageSize"
          :total="store.total"
          :page-sizes="[20, 50, 100]"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
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
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { triggerAnnotationExportDownload } from '@annotation/api/annotations'
import { useAnnotationStore } from '@annotation/stores/annotationStore'
import { useFaultTypeStore } from '@annotation/stores/faultTypeStore'
import type { AnnotationExportScope, AnnotationLabel, AnnotationListItem } from '@annotation/types/annotation'

const router = useRouter()
const store = useAnnotationStore()
const faultTypeStore = useFaultTypeStore()

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

const filters = reactive<{
  package_id: number | undefined
  task_id: number | undefined
  label: AnnotationLabel | undefined
  anomaly_type: string | undefined
  start_ts: number | undefined
  end_ts: number | undefined
}>({
  package_id: undefined,
  task_id: undefined,
  label: undefined,
  anomaly_type: undefined,
  start_ts: undefined,
  end_ts: undefined
})

function normalizeFilters() {
  return {
    package_id: Number.isFinite(filters.package_id) ? filters.package_id : undefined,
    task_id: Number.isFinite(filters.task_id) ? filters.task_id : undefined,
    label: filters.label,
    anomaly_type: filters.anomaly_type?.trim() || undefined,
    start_ts: Number.isFinite(filters.start_ts) ? filters.start_ts : undefined,
    end_ts: Number.isFinite(filters.end_ts) ? filters.end_ts : undefined
  }
}

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function formatRange(start: number, end: number) {
  return `${formatDateTime(new Date(start * 1000).toISOString())} ~ ${formatDateTime(new Date(end * 1000).toISOString())}`
}

async function applyFilters() {
  try {
    store.setFilters(normalizeFilters())
    await store.fetchList({ page: 1 })
  } catch {
    ElMessage.error('查询标注失败')
  }
}

async function resetFilters() {
  filters.package_id = undefined
  filters.task_id = undefined
  filters.label = undefined
  filters.anomaly_type = undefined
  filters.start_ts = undefined
  filters.end_ts = undefined
  await applyFilters()
}

async function handlePageChange(value: number) {
  try {
    await store.fetchList({ page: value })
  } catch {
    ElMessage.error('分页查询失败')
  }
}

async function handleSizeChange(value: number) {
  try {
    await store.fetchList({ page: 1, page_size: value })
  } catch {
    ElMessage.error('分页查询失败')
  }
}

async function handleExport(format: 'csv' | 'json', scope: AnnotationExportScope) {
  try {
    const result = await store.exportWithFilters(format, scope)
    if (result?.download_url) {
      triggerAnnotationExportDownload(result.download_url, result.file_name)
    }
    ElMessage.success(`导出 ${format.toUpperCase()} 成功`)
  } catch {
    ElMessage.error('导出失败')
  }
}

onMounted(() => {
  void faultTypeStore.fetchAll().catch(() => ElMessage.error('加载故障类型失败'))
  void applyFilters()
})

function viewWindow(row: AnnotationListItem) {
  void router.push({
    name: 'slice-window-browser',
    params: { id: row.package_id, taskId: row.task_id },
    query: { window_id: row.window_id, from: 'annotation-list' }
  })
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
    await store.fetchList()
    await store.fetchStats()
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
    await store.fetchList()
    await store.fetchStats()
    ElMessage.success('标注删除成功')
  } catch {
    ElMessage.error('标注删除失败')
  }
}
</script>

<style scoped>
.export-alert {
  margin: 12px 0;
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
