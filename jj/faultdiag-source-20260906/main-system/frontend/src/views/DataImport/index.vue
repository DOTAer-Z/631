<template>
  <div class="data-import-page">
    <el-card shadow="never" class="upload-card">
      <template #header><span>压缩包上传</span></template>
      <el-row :gutter="16">
        <el-col :xs="24" :md="12">
          <el-upload
            ref="uploadRef"
            v-model:file-list="uploadFileList"
            class="upload-panel"
            drag
            :auto-upload="false"
            :limit="1"
            accept=".zip,.tar,.tar.gz,.tgz"
            :before-upload="beforeUpload"
            :on-change="handleFileChange"
            :on-remove="handleFileRemove"
            :on-exceed="handleFileExceed"
          >
            <el-icon size="42" color="#94a3b8"><UploadFilled /></el-icon>
            <div class="upload-text">拖拽或点击选择压缩包</div>
            <div class="upload-tip">支持 zip / tar / tar.gz / tgz，单文件最大 512MB</div>
          </el-upload>
        </el-col>
        <el-col :xs="24" :md="12">
          <el-form :model="uploadForm" label-width="90px">
            <el-form-item label="显示名称">
              <el-input v-model="uploadForm.displayName" placeholder="可选" clearable />
            </el-form-item>
            <el-form-item label="描述">
              <el-input v-model="uploadForm.description" type="textarea" :rows="3" placeholder="可选" />
            </el-form-item>
            <el-form-item label="标签">
              <el-input v-model="uploadForm.tagsText" placeholder="可选，逗号分隔" clearable />
            </el-form-item>
            <el-form-item label="创建人">
              <el-input v-model="uploadForm.createdBy" placeholder="可选" clearable />
            </el-form-item>
          </el-form>
          <div class="action-row">
            <el-button type="primary" :loading="uploading" @click="handleUpload">上传</el-button>
            <el-button :disabled="uploading" @click="clearUploadSelection">清空</el-button>
          </div>
        </el-col>
      </el-row>
    </el-card>

    <el-card shadow="never" class="list-card">
      <template #header>
        <div class="list-header">
          <span>导入文件管理</span>
          <div class="list-header-actions">
            <el-tag v-if="autoRefreshing" type="info" size="small">自动刷新中</el-tag>
            <el-button size="small" type="primary" @click="goToDataList">
              去数据列表查看
            </el-button>
            <el-button size="small" @click="loadList">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
          </div>
        </div>
      </template>
      <el-form :inline="true" class="filter-form">
        <el-form-item label="关键字">
          <el-input
            v-model="filters.keyword"
            clearable
            placeholder="文件名/显示名称"
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item label="扩展名">
          <el-select v-model="filters.fileExt" clearable placeholder="全部" style="width: 140px;">
            <el-option label="zip" value="zip" />
            <el-option label="tar" value="tar" />
            <el-option label="tar.gz" value="tar.gz" />
            <el-option label="tgz" value="tgz" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" clearable placeholder="全部" style="width: 140px;">
            <el-option label="uploaded" value="uploaded" />
            <el-option label="deleted" value="deleted" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">查询</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>

      <el-table :data="rows" size="small" v-loading="loading">
        <el-table-column prop="import_id" label="Import ID" min-width="200" show-overflow-tooltip />
        <el-table-column prop="original_filename" label="原始文件名" min-width="200" show-overflow-tooltip />
        <el-table-column prop="display_name" label="显示名称" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.display_name">{{ row.display_name }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.description">{{ row.description }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="标签" min-width="160">
          <template #default="{ row }">
            <template v-if="(row.tags || []).length">
              <el-tag
                v-for="tag in row.tags"
                :key="tag"
                size="small"
                class="tag-item"
                effect="plain"
              >{{ tag }}</el-tag>
            </template>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_by" label="创建人" min-width="110" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.created_by">{{ row.created_by }}</span>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="file_ext" label="扩展名" width="90" />
        <el-table-column prop="size_bytes" label="大小" width="110">
          <template #default="{ row }">{{ formatFileSize(row.size_bytes) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="存档状态" width="110">
          <template #default="{ row }">
            <el-tag :type="row.status === 'uploaded' ? 'success' : 'info'" size="small">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="摄入状态" width="120">
          <template #default="{ row }">
            <el-tag :type="ingestTagType(row.ingest_status)" size="small">
              {{ ingestStatusLabel(row.ingest_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="摄入统计" width="280">
          <template #default="{ row }">
            <div v-if="['success', 'partial_success', 'failed'].includes(row.ingest_status)" class="ingest-stats">
              <div>
                用例 {{ row.ingested_case_count || 0 }}
                <span class="delta-tag delta-new" v-if="row.ingested_new_case_count">+{{ row.ingested_new_case_count }}</span>
                <span class="delta-tag delta-update" v-if="row.ingested_updated_case_count">↻{{ row.ingested_updated_case_count }}</span>
                / 轮次 {{ row.ingested_run_count || 0 }}
                <span class="delta-tag delta-new" v-if="row.ingested_new_run_count">+{{ row.ingested_new_run_count }}</span>
                <span class="delta-tag delta-update" v-if="row.ingested_updated_run_count">↻{{ row.ingested_updated_run_count }}</span>
              </div>
              <div class="entry-line">条目 {{ row.ingested_entry_count || 0 }}</div>
              <div class="training-stats">
                训练：完整 {{ row.training_complete_count || 0 }} · 不完整 {{ row.training_incomplete_count || 0 }} ·
                重复 {{ row.training_duplicate_count || 0 }} · 失败 {{ row.training_failed_count || 0 }} ·
                解析失败 {{ row.training_parse_failed_count || 0 }}
              </div>
              <div v-if="row.ingest_status === 'failed' && row.ingest_error" class="ingest-error" :title="row.ingest_error">
                {{ truncate(row.ingest_error, 28) }}
              </div>
            </div>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="上传时间" min-width="160">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="320" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="handlePreview(row)">浏览</el-button>
            <el-button link type="primary" @click="handleDownload(row)">下载</el-button>
            <el-button
              link
              type="success"
              :disabled="row.ingest_status === 'ingesting'"
              @click="handleReingest(row)"
            >
              {{ row.ingest_status === 'success' || row.ingest_status === 'partial_success' ? '重新摄入' : '摄入' }}
            </el-button>
            <el-button link type="warning" @click="openEditDialog(row)">编辑</el-button>
            <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-wrap">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.pageSize"
          :total="pagination.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @current-change="loadList"
          @size-change="handlePageSizeChange"
        />
      </div>
    </el-card>

    <el-drawer v-model="previewDrawerVisible" title="文件浏览" size="42%">
      <div v-loading="previewLoading">
        <el-descriptions v-if="previewData" :column="1" border size="small">
          <el-descriptions-item label="Import ID">{{ previewData.import_id }}</el-descriptions-item>
          <el-descriptions-item label="文件名">{{ previewData.original_filename }}</el-descriptions-item>
          <el-descriptions-item label="扩展名">{{ previewData.file_ext }}</el-descriptions-item>
          <el-descriptions-item label="大小">{{ formatFileSize(previewData.size_bytes) }}</el-descriptions-item>
          <el-descriptions-item label="MIME">{{ previewData.mime_type || '-' }}</el-descriptions-item>
          <el-descriptions-item label="存档状态">{{ previewData.status }}</el-descriptions-item>
          <el-descriptions-item label="摄入状态">
            <el-tag :type="ingestTagType(previewData.ingest_status)" size="small">
              {{ ingestStatusLabel(previewData.ingest_status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="previewData.ingested_at" label="摄入完成时间">
            {{ formatDateTime(previewData.ingested_at) }}
          </el-descriptions-item>
          <el-descriptions-item v-if="previewData.ingest_status === 'failed' && previewData.ingest_error" label="摄入错误">
            <span class="ingest-error">{{ previewData.ingest_error }}</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="previewData.ingest_status === 'success' || previewData.ingest_status === 'partial_success'" label="生成用例">
            <el-tag v-for="cid in previewData.ingested_case_ids || []" :key="cid" size="small" class="tag-item">{{ cid }}</el-tag>
            <span v-if="!(previewData.ingested_case_ids || []).length">-</span>
          </el-descriptions-item>
          <el-descriptions-item v-if="previewData.ingest_status === 'success' || previewData.ingest_status === 'partial_success'" label="生成轮次">
            {{ previewData.ingested_run_count || 0 }}
          </el-descriptions-item>
          <el-descriptions-item v-if="previewData.ingest_status === 'success' || previewData.ingest_status === 'partial_success'" label="生成日志条目">
            {{ previewData.ingested_entry_count || 0 }}
          </el-descriptions-item>
          <el-descriptions-item label="训练数据统计">
            <div class="training-stats preview-training-stats">
              <span>完整 {{ previewData.training_complete_count || 0 }}</span>
              <span>不完整 {{ previewData.training_incomplete_count || 0 }}</span>
              <span>重复 {{ previewData.training_duplicate_count || 0 }}</span>
              <span>失败 {{ previewData.training_failed_count || 0 }}</span>
              <span>解析失败 {{ previewData.training_parse_failed_count || 0 }}</span>
            </div>
          </el-descriptions-item>
          <el-descriptions-item label="标签">
            <el-tag v-for="tag in previewData.tags || []" :key="tag" size="small" class="tag-item">{{ tag }}</el-tag>
            <span v-if="!(previewData.tags || []).length">-</span>
          </el-descriptions-item>
          <el-descriptions-item label="描述">{{ previewData.description || '-' }}</el-descriptions-item>
          <el-descriptions-item label="文件存在">{{ previewData.file_exists ? '是' : '否' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDateTime(previewData.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatDateTime(previewData.updated_at) }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-drawer>

    <el-dialog v-model="editDialogVisible" title="编辑元数据" width="560px">
      <el-form :model="editForm" label-width="90px">
        <el-form-item label="显示名称">
          <el-input v-model="editForm.displayName" clearable />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="editForm.description" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="editForm.tagsText" placeholder="逗号分隔，例如 sdk,arm,v1" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingEdit" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { UploadFilled, Refresh } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import {
  deleteDataImport,
  downloadDataImportUrl,
  ingestDataImport,
  listDataImports,
  previewDataImport,
  updateDataImport,
  uploadDataImport,
} from '@/api/dataImport'

const MAX_FILE_SIZE = 512 * 1024 * 1024
const ALLOWED_EXTENSIONS = ['zip', 'tar', 'tar.gz', 'tgz']

const router = useRouter()

const uploadRef = ref(null)
const uploadFileList = ref([])
const selectedFile = ref(null)
const uploading = ref(false)

const loading = ref(false)
const rows = ref([])
const pagination = reactive({
  page: 1,
  pageSize: 20,
  total: 0,
})
const filters = reactive({
  keyword: '',
  fileExt: '',
  status: '',
})

const previewDrawerVisible = ref(false)
const previewLoading = ref(false)
const previewData = ref(null)

const editDialogVisible = ref(false)
const savingEdit = ref(false)
const editForm = reactive({
  importId: '',
  displayName: '',
  description: '',
  tagsText: '',
})

const uploadForm = reactive({
  displayName: '',
  description: '',
  tagsText: '',
  createdBy: '',
})

function detectArchiveExtension(name = '') {
  const filename = String(name).toLowerCase()
  if (filename.endsWith('.tar.gz')) return 'tar.gz'
  if (filename.endsWith('.tgz')) return 'tgz'
  if (filename.endsWith('.zip')) return 'zip'
  if (filename.endsWith('.tar')) return 'tar'
  return ''
}

function formatDateTime(value) {
  if (!value) return '-'
  // 后端返回的 ISO 字符串大多无时区后缀（naive UTC datetime）；浏览器会按本地时区解析，
  // 导致显示晚 8 小时。这里统一：无 Z / 无 ±HH:MM 后缀的视为 UTC，补 'Z' 再交给 Date 解析。
  const s = String(value)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  const iso = hasTz ? s : s + 'Z'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function formatFileSize(bytes) {
  if (typeof bytes !== 'number') return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function validateFile(file) {
  const ext = detectArchiveExtension(file.name)
  if (!ALLOWED_EXTENSIONS.includes(ext)) {
    ElMessage.error('仅支持 zip / tar / tar.gz / tgz')
    return false
  }
  if (file.size > MAX_FILE_SIZE) {
    ElMessage.error('文件大小不能超过 512MB')
    return false
  }
  return true
}

function beforeUpload(rawFile) {
  return validateFile(rawFile) ? false : false
}

function handleFileExceed() {
  ElMessage.warning('一次仅可选择 1 个文件')
}

function handleFileChange(file) {
  const raw = file.raw || file
  if (!raw || !validateFile(raw)) {
    selectedFile.value = null
    uploadFileList.value = []
    uploadRef.value?.clearFiles()
    return
  }
  selectedFile.value = raw
  uploadFileList.value = [file]
}

function handleFileRemove() {
  selectedFile.value = null
}

function goToDataList() {
  // 路由名兜底：项目里入口名是 LogAnalysisMain，但用户看到的是「数据列表」
  router.push({ name: 'LogAnalysisMain' }).catch(() => {
    router.push('/log-analysis')
  })
}

function clearUploadSelection() {
  selectedFile.value = null
  uploadFileList.value = []
  uploadRef.value?.clearFiles()
  uploadForm.displayName = ''
  uploadForm.description = ''
  uploadForm.tagsText = ''
  uploadForm.createdBy = ''
}

async function handleUpload() {
  if (!selectedFile.value) {
    ElMessage.warning('请先选择上传文件')
    return
  }
  if (!validateFile(selectedFile.value)) return

  const formData = new FormData()
  formData.append('file', selectedFile.value)
  if (uploadForm.displayName.trim()) formData.append('display_name', uploadForm.displayName.trim())
  if (uploadForm.description.trim()) formData.append('description', uploadForm.description.trim())
  if (uploadForm.tagsText.trim()) formData.append('tags', uploadForm.tagsText.trim())
  if (uploadForm.createdBy.trim()) formData.append('created_by', uploadForm.createdBy.trim())

  uploading.value = true
  try {
    const result = await uploadDataImport(formData)
    if (result?.ingest_status === 'pending' || result?.ingest_status === 'ingesting') {
      ElMessage.success('上传成功，正在自动解压并摄入到数据列表...')
    } else if (result?.ingest_status === 'success') {
      ElMessage.success('上传 + 摄入完成，已写入数据列表')
    } else if (result?.ingest_status === 'partial_success') {
      ElMessage.warning('上传完成，部分训练数据摄入失败')
    } else {
      ElMessage.success('上传成功')
    }
    // 显著提示：摄入完成后这条记录会出现在「数据分析→数据列表」
    ElNotification({
      title: '已开始自动摄入',
      message: '解压并写入"数据分析→数据列表"通常需要几秒。完成后会再弹一条「摄入完成」提示，告诉你新增了多少 Run、覆盖了多少 Run。',
      type: 'info',
      duration: 6000,
    })
    clearUploadSelection()
    pagination.page = 1
    await loadList()
    // 启动后台轮询，等待摄入完成
    startAutoRefresh()
  } finally {
    uploading.value = false
  }
}

// ── 摄入状态显示工具 ──
function ingestStatusLabel(status) {
  return {
    pending: '排队中',
    ingesting: '摄入中…',
    success: '已摄入',
    partial_success: '部分成功',
    failed: '摄入失败',
    no_test_dirs: '无 Test 目录',
    skipped: '未摄入',
  }[status] || (status || '-')
}

function ingestTagType(status) {
  return {
    pending: 'info',
    ingesting: 'warning',
    success: 'success',
    partial_success: 'warning',
    failed: 'danger',
    no_test_dirs: 'info',
    skipped: 'info',
  }[status] || 'info'
}

function truncate(s, n) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}

// ── 自动刷新（仅在仍有 pending/ingesting 行时持续轮询） ──
const autoRefreshing = ref(false)
let autoRefreshTimer = null
// 上一轮状态快照：import_id → ingest_status，用来发现「刚刚摄入完成」的行
let prevIngestStatus = new Map()

function shouldKeepRefreshing() {
  return rows.value.some(r => r.ingest_status === 'pending' || r.ingest_status === 'ingesting')
}

function announceJustFinished() {
  // 找出本轮 success/failed、上一轮还在 pending/ingesting 的行
  for (const row of rows.value) {
    const prev = prevIngestStatus.get(row.import_id)
    if (prev !== 'pending' && prev !== 'ingesting') continue
    if (row.ingest_status === 'success' || row.ingest_status === 'partial_success') {
      const newR = row.ingested_new_run_count || 0
      const updR = row.ingested_updated_run_count || 0
      const newC = row.ingested_new_case_count || 0
      const updC = row.ingested_updated_case_count || 0
      let msg
      if (newR === 0 && updR > 0) {
        msg = `${row.original_filename}：所有 ${updR} 个 Run 已是已有用例，仅刷新内容（Run 总数不变）`
      } else if (newR > 0 && updR === 0) {
        msg = `${row.original_filename}：新增 ${newC} 用例 / ${newR} Run`
      } else {
        msg = `${row.original_filename}：新增 ${newC} 用例 ${newR} Run，覆盖 ${updC} 用例 ${updR} Run`
      }
      ElNotification({
        title: row.ingest_status === 'partial_success' ? '摄入部分成功' : '摄入完成',
        message: msg,
        type: row.ingest_status === 'partial_success' ? 'warning' : (newR > 0 ? 'success' : 'warning'),
        duration: 8000,
      })
    } else if (row.ingest_status === 'failed') {
      ElNotification({
        title: '摄入失败',
        message: `${row.original_filename}：${row.ingest_error || '未知错误'}`,
        type: 'error',
        duration: 0,
      })
    }
  }
  prevIngestStatus = new Map(rows.value.map(r => [r.import_id, r.ingest_status]))
}

function startAutoRefresh() {
  if (autoRefreshTimer) return
  autoRefreshing.value = true
  // 启动时先记录一次基线
  prevIngestStatus = new Map(rows.value.map(r => [r.import_id, r.ingest_status]))
  autoRefreshTimer = setInterval(async () => {
    await loadList()
    announceJustFinished()
    if (!shouldKeepRefreshing()) {
      stopAutoRefresh()
    }
  }, 3000)
}

function stopAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer)
    autoRefreshTimer = null
  }
  autoRefreshing.value = false
}

async function handleReingest(row) {
  try {
    if (row.ingest_status === 'success' || row.ingest_status === 'partial_success') {
      await ElMessageBox.confirm(
        '该数据集已成功摄入，重新摄入会覆盖已有 case/run 记录（按 case_id 主键 upsert）。继续？',
        '重新摄入',
        { type: 'warning' },
      )
    }
    await ingestDataImport(row.import_id)
    ElMessage.success('已发起摄入任务，状态将自动刷新')
    await loadList()
    startAutoRefresh()
  } catch (e) {
    if (e === 'cancel' || e?.message === 'cancel') return
    ElMessage.error(`摄入触发失败: ${e?.message || e}`)
  }
}

function buildQueryParams() {
  return {
    page: pagination.page,
    page_size: pagination.pageSize,
    keyword: filters.keyword.trim() || undefined,
    file_ext: filters.fileExt || undefined,
    status: filters.status || undefined,
  }
}

async function loadList() {
  loading.value = true
  try {
    const result = await listDataImports(buildQueryParams())
    rows.value = result.items || []
    pagination.total = result.total || 0
    // 列表中若仍有进行中的摄入任务，启动自动刷新
    if (shouldKeepRefreshing()) {
      startAutoRefresh()
    }
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  pagination.page = 1
  loadList()
}

function handleReset() {
  filters.keyword = ''
  filters.fileExt = ''
  filters.status = ''
  pagination.page = 1
  pagination.pageSize = 20
  loadList()
}

function handlePageSizeChange() {
  pagination.page = 1
  loadList()
}

async function handlePreview(row) {
  previewDrawerVisible.value = true
  previewLoading.value = true
  previewData.value = null
  try {
    previewData.value = await previewDataImport(row.import_id)
  } finally {
    previewLoading.value = false
  }
}

function handleDownload(row) {
  window.open(downloadDataImportUrl(row.import_id), '_blank')
}

function openEditDialog(row) {
  editForm.importId = row.import_id
  editForm.displayName = row.display_name || ''
  editForm.description = row.description || ''
  editForm.tagsText = (row.tags || []).join(', ')
  editDialogVisible.value = true
}

async function submitEdit() {
  const tags = editForm.tagsText
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)

  savingEdit.value = true
  try {
    await updateDataImport(editForm.importId, {
      display_name: editForm.displayName,
      description: editForm.description,
      tags,
    })
    ElMessage.success('元数据更新成功')
    editDialogVisible.value = false
    await loadList()
  } finally {
    savingEdit.value = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm(
    `确认删除文件 "${row.original_filename}" 吗？`,
    '删除确认',
    { type: 'warning' },
  )
  await deleteDataImport(row.import_id)
  ElMessage.success('删除成功')
  if (rows.value.length === 1 && pagination.page > 1) {
    pagination.page -= 1
  }
  await loadList()
}

onMounted(() => {
  loadList()
})

onBeforeUnmount(() => {
  stopAutoRefresh()
})
</script>

<style scoped>
.data-import-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.upload-text {
  margin-top: 8px;
  color: #475569;
}

.upload-tip {
  margin-top: 4px;
  color: #94a3b8;
  font-size: 12px;
}

.action-row {
  display: flex;
  gap: 10px;
}

.filter-form {
  margin-bottom: 12px;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}

.tag-item {
  margin-right: 6px;
  margin-bottom: 2px;
}

.muted {
  color: #94a3b8;
}

.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.list-header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ingest-stats {
  font-size: 12px;
  color: #475569;
  line-height: 1.5;
}

.entry-line {
  color: #64748b;
}

.training-stats {
  color: #64748b;
  line-height: 1.55;
}

.preview-training-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}

.delta-tag {
  display: inline-block;
  font-size: 11px;
  padding: 0 5px;
  border-radius: 8px;
  margin: 0 2px;
  line-height: 16px;
}

.delta-new {
  background: #ecfdf5;
  color: #059669;
}

.delta-update {
  background: #eff6ff;
  color: #2563eb;
}

.ingest-error {
  font-size: 12px;
  color: #dc2626;
}
</style>
