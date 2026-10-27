<template>
  <div class="page-shell">
    <div class="page-toolbar">
      <div>
        <div class="page-toolbar__title">数据包管理</div>
        <div class="page-toolbar__hint">上传压缩包、查看导入状态，并进入切片前的数据准备阶段</div>
      </div>

      <div class="page-toolbar__actions">
        <el-button type="primary" data-testid="open-main-system-dialog" @click="openDataImportDialog">从主系统数据导入</el-button>
      </div>
    </div>

    <el-card shadow="never" class="console-card">
      <div class="toolbar-panel">
        <el-form inline @submit.prevent>
          <el-form-item label="搜索">
            <el-input
              v-model="searchKeyword"
              placeholder="按名称或描述搜索"
              clearable
              data-testid="package-search-input"
              @keyup.enter="handleSearch"
              @clear="handleSearch"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="handleSearch">搜索</el-button>
            <el-button @click="handleReset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table :data="store.packages" v-loading="store.loading" empty-text="暂无数据包">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" label="名称" min-width="200" />
        <el-table-column label="种类" width="110">
          <template #default="{ row }">
            <el-tag :type="dataKindTag(row.data_kind)" effect="plain" size="small">
              {{ dataKindLabel(row.data_kind) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="导入状态" width="160">
          <template #default="{ row }">
            <el-tag :type="packageStatusTag(row.import_status)" effect="light">{{ row.import_status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="导入任务" width="150">
          <template #default="{ row }">
            <span>{{ importTaskStatusLabel(row.id) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="异常标记" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.has_abnormal" type="danger" effect="dark" data-testid="package-abnormal-tag">有异常</el-tag>
            <el-tag v-else type="info" effect="plain">无异常</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="源文件 / 行数" min-width="180">
          <template #default="{ row }">{{ row.source_file_count }} / {{ row.source_line_count }}</template>
        </el-table-column>
        <el-table-column label="CPU / 模块" min-width="160">
          <template #default="{ row }">{{ row.cpu_count }} / {{ row.module_count }}</template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '-' }}</template>
        </el-table-column>
        <el-table-column label="创建时间" min-width="180">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-space>
              <el-button link type="primary" @click="goDetail(row.id)">详情</el-button>
              <el-button
                v-if="canPollImport(row.id, row.import_status)"
                link
                type="warning"
                :data-testid="`package-poll-button-${row.id}`"
                @click="pollImport(row.id)"
              >
                轮询导入
              </el-button>
              <el-button link type="danger" @click="openDeleteDialog(row.id, row.name)">删除</el-button>
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
          :page-sizes="[10, 20, 50]"
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>

    <!-- 从主系统数据导入：选择 DB1 的数据导入记录 -->
    <el-dialog v-model="dataImportDialogVisible" title="从主系统数据导入" width="780px">
      <div style="display: flex; gap: 8px; margin-bottom: 12px;">
        <el-input
          v-model="diKeyword"
          placeholder="按文件名搜索"
          clearable
          @keyup.enter="loadDataImports"
          style="max-width: 280px;"
        />
        <el-select v-model="diStatus" placeholder="状态筛选" clearable style="max-width: 160px;" @change="loadDataImports">
          <el-option label="全部" value="" />
          <el-option label="已上传" value="uploaded" />
          <el-option label="已删除" value="deleted" />
        </el-select>
        <el-button @click="loadDataImports" :loading="diLoading">查询</el-button>
      </div>
      <el-table
        :data="diItems"
        height="340"
        v-loading="diLoading"
        highlight-current-row
        @current-change="onDiRowSelect"
      >
        <el-table-column prop="original_filename" label="文件名" min-width="220" show-overflow-tooltip />
        <el-table-column label="大小" width="110">
          <template #default="{ row }">{{ formatFileSize(row.size_bytes) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90" />
        <el-table-column label="摄入统计" min-width="180">
          <template #default="{ row }">
            <span v-if="row.ingest_status">
              {{ row.ingest_status }}
              <template v-if="row.ingested_run_count != null">（{{ row.ingested_run_count }} 条）</template>
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" min-width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!diLoading && !diItems.length" description="主系统暂无数据导入记录，或主系统不可达" />
      <div style="display: flex; justify-content: flex-end; margin-top: 8px;">
        <el-pagination
          v-model:current-page="diPage"
          :page-size="diPageSize"
          :total="diTotal"
          layout="total, prev, pager, next"
          size="small"
          @current-change="loadDataImports"
        />
      </div>
      <el-form label-width="80px" style="margin-top: 12px;">
        <el-form-item label="导入去向">
          <el-radio-group v-model="diDestination">
            <el-radio value="annotate" data-testid="di-dest-annotate">去标注（建数据包 / 切片 / 标注）</el-radio>
            <el-radio value="kb" data-testid="di-dest-kb">入知识库（主系统 runs/cases / RAG）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="已选">
          <span>{{ diSelectedFilename || '（请在上表选择一条）' }}</span>
        </el-form-item>
        <el-form-item label="包名称">
          <el-input v-model="diName" placeholder="留空则默认取主系统文件名" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="diDescription" type="textarea" :rows="2" maxlength="500" show-word-limit />
        </el-form-item>
      </el-form>
      <el-alert
        v-if="diDestination === 'kb' && kbError"
        type="error"
        :title="kbError"
        :closable="false"
        style="margin-bottom: 8px;"
      />
      <el-alert
        v-if="diDestination === 'kb' && kbResult"
        :type="kbResult.ingest_status === 'failed' ? 'error' : 'info'"
        :title="`知识库入库状态：${kbResult.ingest_status || 'pending'}`"
        :description="kbStatusText(kbResult)"
        :closable="false"
        style="margin-bottom: 8px;"
      />
      <template #footer>
        <el-button @click="dataImportDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="!diSelectedImportId"
          :loading="diSubmitting"
          data-testid="di-submit"
          @click="submitDataImport"
        >{{ diDestination === 'kb' ? '开始知识库导入' : '开始标注导入' }}</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="deleteDialogVisible" title="删除数据包" width="520px">
      <div class="danger-delete">
        <p class="danger-delete__text">删除数据包会级联清理导入任务、原始日志、切片任务、窗口和标注。</p>
        <p class="danger-delete__text">请输入包名 <strong>{{ deleteTargetName }}</strong> 以确认删除。</p>
        <el-input v-model="deleteConfirmName" data-testid="package-delete-confirm-input" placeholder="输入完整包名" />
      </div>

      <template #footer>
        <el-button @click="closeDeleteDialog">取消</el-button>
        <el-button type="danger" :loading="store.deleting" data-testid="package-delete-confirm-submit" @click="submitDelete">
          确认删除
        </el-button>
      </template>
    </el-dialog>

    <!-- 从主系统导入（旧版 run 逻辑）已删除 — 替换为上方 data-imports dialog -->
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  importDataImportToKnowledgeBase,
  listMainSystemDataImports
} from '@annotation/api/packages'
import { usePackageStore } from '@annotation/stores/packageStore'
import type { DataImportDetail, DataImportItem, DataKind, ImportStatus } from '@annotation/types/package'

const router = useRouter()
const store = usePackageStore()

const searchKeyword = ref(store.query)

const deleteDialogVisible = ref(false)
const deleteTargetId = ref<number | null>(null)
const deleteTargetName = ref('')
const deleteConfirmName = ref('')

let pollTimer: ReturnType<typeof setInterval> | null = null

function formatDateTime(value: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function packageStatusTag(status: ImportStatus) {
  if (status === 'imported') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'importing') return 'warning'
  return 'info'
}

function dataKindTag(kind: DataKind | undefined) {
  if (kind === 'unstructured') return 'warning'
  if (kind === 'structured') return 'info'
  return 'success'
}

function dataKindLabel(kind: DataKind | undefined) {
  if (kind === 'unstructured') return '非结构化'
  if (kind === 'structured') return '结构化'
  return '半结构化'
}

function importTaskStatusLabel(packageId: number) {
  const task = store.getImportTaskStatusForPackage(packageId)
  return task?.status || '-'
}

function canPollImport(packageId: number, importStatus: ImportStatus) {
  return importStatus !== 'imported' && Boolean(store.getKnownImportTaskId(packageId))
}

async function loadData() {
  try {
    await store.fetchPackages()
  } catch {
    ElMessage.error('获取数据包列表失败')
  }
}

async function pollActiveImports() {
  const candidates = store.packages.filter((item) => canPollImport(item.id, item.import_status))
  for (const item of candidates) {
    try {
      await store.refreshImportProgress(item.id)
    } catch {
      // keep list visible even if a poll attempt fails
    }
  }
}

function startPolling() {
  if (pollTimer) return
  pollTimer = setInterval(() => {
    void pollActiveImports()
  }, 5000)
}

function stopPolling() {
  if (!pollTimer) return
  clearInterval(pollTimer)
  pollTimer = null
}

function handleSearch() {
  store.setQuery(searchKeyword.value.trim())
  void loadData()
}

function handleReset() {
  searchKeyword.value = ''
  handleSearch()
}

function handlePageChange(value: number) {
  store.setPage(value)
  void loadData()
}

function handleSizeChange(value: number) {
  store.setPageSize(value)
  void loadData()
}

function goDetail(id: number) {
  void router.push({ name: 'package-detail', params: { id } })
}

// ── 从主系统数据导入 ───────────────────────────────────────────────────────────
const dataImportDialogVisible = ref(false)
const diItems = ref<DataImportItem[]>([])
const diLoading = ref(false)
const diKeyword = ref('')
const diStatus = ref('')
const diPage = ref(1)
const diPageSize = ref(10)
const diTotal = ref(0)
const diSelectedImportId = ref<string | number>('')
const diSelectedFilename = ref('')
const diName = ref('')
const diDescription = ref('')
const diSubmitting = ref(false)
// 导入去向：annotate=去标注（建数据包/切片/标注）；kb=入知识库（主系统 runs/cases / RAG）
const diDestination = ref<'annotate' | 'kb'>('annotate')
// 「入知识库」触发后主系统返回的状态，用于在对话框内展示进度
const kbResult = ref<DataImportDetail | null>(null)
const kbError = ref('')

function openDataImportDialog() {
  dataImportDialogVisible.value = true
  diSelectedImportId.value = ''
  diSelectedFilename.value = ''
  diName.value = ''
  diDescription.value = ''
  diPage.value = 1
  diDestination.value = 'annotate'
  kbResult.value = null
  kbError.value = ''
  void loadDataImports()
}

async function loadDataImports() {
  diLoading.value = true
  try {
    const data = await listMainSystemDataImports({
      page: diPage.value,
      page_size: diPageSize.value,
      keyword: diKeyword.value.trim() || undefined,
      status: diStatus.value || undefined
    })
    diItems.value = data.items || []
    diTotal.value = data.total || 0
  } catch {
    diItems.value = []
    diTotal.value = 0
    ElMessage.error('无法从主系统获取数据导入列表，请确认主系统在线')
  } finally {
    diLoading.value = false
  }
}

function onDiRowSelect(row: DataImportItem | null) {
  diSelectedImportId.value = row?.import_id || ''
  diSelectedFilename.value = row?.original_filename || ''
}

async function submitDataImport() {
  if (!diSelectedImportId.value) {
    ElMessage.warning('请先在列表中选择一条数据导入记录')
    return
  }
  diSubmitting.value = true
  kbError.value = ''
  kbResult.value = null

  // 入知识库：不建标注包，触发主系统侧摄入（runs/cases / RAG），返回实时 ingest 状态。
  if (diDestination.value === 'kb') {
    try {
      kbResult.value = await importDataImportToKnowledgeBase({
        import_id: diSelectedImportId.value,
        filename: diSelectedFilename.value || undefined
      })
      const st = kbResult.value.ingest_status || 'pending'
      void refreshKbStatus(diSelectedImportId.value)
      ElMessage.success(`已触发知识库入库（${st}），稍后可在主系统知识库查看入库数据`)
    } catch {
      kbError.value = '触发知识库入库失败，请确认主系统在线后重试'
      ElMessage.error(kbError.value)
    } finally {
      diSubmitting.value = false
    }
    return
  }

  // 去标注：建数据包并触发导入（带原始文件名，避免 INVALID_ARCHIVE_TYPE）。
  try {
    const created = await store.importPackageFromDataImport({
      import_id: diSelectedImportId.value,
      filename: diSelectedFilename.value || undefined,
      name: diName.value.trim() || undefined,
      description: diDescription.value.trim() || null
    })
    await store.refreshImportProgress(created.id, created.import_task_id)
    dataImportDialogVisible.value = false
    ElMessage.success('已开始从主系统导入，稍后在列表查看导入状态')
  } catch {
    ElMessage.error('从主系统导入失败，请重试')
  } finally {
    diSubmitting.value = false
  }
}

// 「入知识库」是后台异步，轮询主系统数据导入的 ingest 状态更新对话框内反馈。
async function refreshKbStatus(importId: string | number) {
  try {
    const data = await listMainSystemDataImports({ page: 1, page_size: diPageSize.value })
    const found = (data.items || []).find((it) => String(it.import_id) === String(importId))
    if (found) {
      kbResult.value = found
      const st = found.ingest_status
      if (st === 'ingesting' || st === 'pending') {
        setTimeout(() => void refreshKbStatus(importId), 3000)
      } else if (st === 'completed') {
        ElMessage.success(
          `知识库入库完成：新增/更新 run ${found.ingested_new_run_count ?? 0} / ${found.ingested_updated_run_count ?? 0} 条，` +
            `日志 ${found.ingested_entry_count ?? 0} 条`
        )
      } else if (st === 'failed') {
        ElMessage.error(found.ingest_error || '知识库入库失败')
      }
    }
  } catch {
    // 轮询失败不打断当前对话框，用户可再点一次导入重查
  }
}

// 「入知识库」对话框内的状态汇总文案
function kbStatusText(item: DataImportDetail): string {
  const st = item.ingest_status
  if (st === 'completed') {
    return (
      `新增 run ${item.ingested_new_run_count ?? 0} / 更新 run ${item.ingested_updated_run_count ?? 0}，` +
      `日志 ${item.ingested_entry_count ?? 0} 条，case ${item.ingested_case_count ?? 0} 个`
    )
  }
  if (st === 'failed') {
    return item.ingest_error || '知识库入库失败'
  }
  if (st === 'ingesting') {
    return '正在入库，每 3 秒自动查询一次进度…'
  }
  return '已加入入库队列，等待处理…'
}

// ── 轮询单个导入任务 ──────────────────────────────────────────────────────────
async function pollImport(packageId: number) {
  try {
    const task = await store.refreshImportProgress(packageId)
    if (task?.status === 'completed') {
      ElMessage.success('导入已完成')
    }
    if (task?.status === 'failed') {
      ElMessage.error(task.error_message || '导入失败')
    }
  } catch {
    ElMessage.error('轮询导入任务失败')
  }
}

// ── 删除 ──────────────────────────────────────────────────────────────────────
function openDeleteDialog(id: number, name: string) {
  deleteTargetId.value = id
  deleteTargetName.value = name
  deleteConfirmName.value = ''
  deleteDialogVisible.value = true
}

function closeDeleteDialog() {
  deleteDialogVisible.value = false
  deleteTargetId.value = null
  deleteTargetName.value = ''
  deleteConfirmName.value = ''
}

async function submitDelete() {
  if (!deleteTargetId.value) {
    return
  }

  if (deleteConfirmName.value.trim().toLowerCase() !== deleteTargetName.value.trim().toLowerCase()) {
    ElMessage.error('包名确认不匹配，拒绝删除')
    return
  }

  try {
    await store.removePackage(deleteTargetId.value, deleteConfirmName.value.trim())
    closeDeleteDialog()
    ElMessage.success('删除成功')
  } catch {
    ElMessage.error('删除失败，请重试')
  }
}

onMounted(() => {
  void loadData().then(() => {
    void pollActiveImports()
    startPolling()
  })
})

onUnmounted(() => {
  stopPolling()
})
</script>

<style scoped>
.toolbar-panel {
  margin-bottom: 16px;
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.danger-delete {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.danger-delete__text {
  margin: 0;
  line-height: 1.7;
  color: #475569;
}
</style>
