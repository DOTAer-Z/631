<template>
  <div class="page-shell">
    <el-page-header @back="goBack">
      <template #content>
        <span>数据包详情</span>
      </template>
    </el-page-header>

    <el-card shadow="never" class="console-card" v-loading="store.detailLoading">
      <template #header>
        <div class="detail-header">
          <div>
            <div class="detail-header__title">{{ store.currentPackage?.name || '数据包详情' }}</div>
            <div class="detail-header__hint">展示导入统计、状态流转和切片前约束</div>
          </div>

          <el-space>
            <el-button
              type="primary"
              plain
              data-testid="go-slicing-button"
              :disabled="!canEnterSlicing"
              @click="goSlicing"
            >
              进入切片
            </el-button>
            <el-button v-if="canPollImport" type="warning" plain data-testid="detail-poll-import" @click="pollImport">
              刷新导入状态
            </el-button>
            <el-button type="primary" data-testid="open-edit-dialog" @click="openEditDialog">编辑描述</el-button>
            <el-button type="danger" data-testid="detail-delete-button" @click="openDeleteDialog">删除</el-button>
          </el-space>
        </div>
      </template>

      <el-empty v-if="!store.currentPackage" description="未找到数据包详情" />

      <template v-else>
        <el-alert
          v-if="store.currentPackage.import_status !== 'imported'"
          type="warning"
          :closable="false"
          show-icon
          title="导入未完成，当前禁止创建切片任务。"
          class="status-alert"
        />

        <el-alert
          v-if="store.currentPackage.import_status === 'failed' && store.currentPackage.import_error_message"
          type="error"
          :closable="false"
          show-icon
          :title="store.currentPackage.import_error_message"
          class="status-alert"
        />

        <div class="stats-grid">
          <el-card v-for="card in summaryCards" :key="card.label" shadow="never" class="console-card stat-card">
            <div class="stat-card__value">{{ card.value }}</div>
            <div class="stat-card__label">{{ card.label }}</div>
          </el-card>
        </div>

        <el-descriptions border :column="2" class="detail-descriptions">
          <el-descriptions-item label="导入状态">
            <el-tag :type="statusTag(store.currentPackage.import_status)" effect="light">
              {{ store.currentPackage.import_status }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="导入任务状态">{{ importTaskStatusLabel }}</el-descriptions-item>
          <el-descriptions-item label="压缩类型">{{ store.currentPackage.archive_type }}</el-descriptions-item>
          <el-descriptions-item label="文件大小">{{ formatSize(store.currentPackage.file_size) }}</el-descriptions-item>
          <el-descriptions-item label="时间范围">{{ timeRangeLabel }}</el-descriptions-item>
          <el-descriptions-item label="切片任务数">{{ store.currentPackage.slice_task_count }}</el-descriptions-item>
          <el-descriptions-item label="SHA256">{{ store.currentPackage.sha256 }}</el-descriptions-item>
          <el-descriptions-item label="存储路径">{{ store.currentPackage.stored_path }}</el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ store.currentPackage.description || '-' }}</el-descriptions-item>
          <el-descriptions-item label="导入错误原因" :span="2">
            {{ store.currentPackage.import_error_message || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatDateTime(store.currentPackage.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatDateTime(store.currentPackage.updated_at) }}</el-descriptions-item>
        </el-descriptions>
      </template>
    </el-card>

    <el-dialog v-model="editDialogVisible" title="编辑描述" width="520px">
      <el-input
        v-model="editDescription"
        type="textarea"
        :rows="4"
        maxlength="500"
        show-word-limit
        data-testid="description-input"
      />
      <template #footer>
        <el-button @click="editDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="store.updatingDescription"
          data-testid="description-submit"
          @click="submitDescription"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="deleteDialogVisible" title="删除数据包" width="520px">
      <div class="danger-delete">
        <p class="danger-delete__text">
          删除前必须输入包名 <strong>{{ store.currentPackage?.name }}</strong>，并同时清理导入、原始日志、切片和标注数据。
        </p>
        <el-input v-model="deleteConfirmName" data-testid="detail-delete-confirm-input" placeholder="输入完整包名" />
      </div>

      <template #footer>
        <el-button @click="closeDeleteDialog">取消</el-button>
        <el-button type="danger" :loading="store.deleting" data-testid="detail-delete-confirm-submit" @click="submitDelete">
          确认删除
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { usePackageStore } from '@annotation/stores/packageStore'

const route = useRoute()
const router = useRouter()
const store = usePackageStore()

const editDialogVisible = ref(false)
const editDescription = ref('')
const deleteDialogVisible = ref(false)
const deleteConfirmName = ref('')

const packageId = computed(() => Number(route.params.id))

const canEnterSlicing = computed(() => store.currentPackage?.import_status === 'imported')
const canPollImport = computed(() => {
  const pkg = store.currentPackage
  if (!pkg) return false
  return pkg.import_status !== 'imported' && Boolean(store.getKnownImportTaskId(pkg.id))
})

const importTaskStatusLabel = computed(() => {
  const pkg = store.currentPackage
  if (!pkg) return '-'
  return store.getImportTaskStatusForPackage(pkg.id)?.status || '-'
})

const timeRangeLabel = computed(() => {
  const pkg = store.currentPackage
  if (!pkg?.earliest_timestamp || !pkg?.latest_timestamp) {
    return '-'
  }
  return `${formatTimestamp(pkg.earliest_timestamp)} ~ ${formatTimestamp(pkg.latest_timestamp)}`
})

const summaryCards = computed(() => {
  const pkg = store.currentPackage
  if (!pkg) {
    return []
  }

  return [
    { label: '源文件数', value: pkg.source_file_count },
    { label: '日志行数', value: pkg.source_line_count },
    { label: 'CPU 数', value: pkg.cpu_count },
    { label: '模块数', value: pkg.module_count }
  ]
})

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function formatDateTime(value: string) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function formatTimestamp(value: number) {
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function statusTag(status: string) {
  if (status === 'imported') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'importing') return 'warning'
  return 'info'
}

function goBack() {
  void router.push({ name: 'packages' })
}

async function loadDetail() {
  try {
    await store.fetchPackage(packageId.value)
  } catch {
    ElMessage.error('获取数据包详情失败')
  }
}

function openEditDialog() {
  editDescription.value = store.currentPackage?.description || ''
  editDialogVisible.value = true
}

function goSlicing() {
  if (!canEnterSlicing.value) {
    ElMessage.warning('导入完成前禁止创建切片任务')
    return
  }

  void router.push({ name: 'slice-task-list', params: { id: packageId.value } })
}

async function pollImport() {
  if (!store.currentPackage) {
    return
  }

  try {
    const task = await store.refreshImportProgress(store.currentPackage.id)
    if (task?.status === 'completed') {
      ElMessage.success('导入已完成')
    } else if (task?.status === 'failed') {
      ElMessage.error(task.error_message || '导入失败')
    }
  } catch {
    ElMessage.error('刷新导入状态失败')
  }
}

async function submitDescription() {
  try {
    await store.saveDescription(packageId.value, editDescription.value.trim())
    editDialogVisible.value = false
    ElMessage.success('描述更新成功')
  } catch {
    ElMessage.error('描述更新失败')
  }
}

function openDeleteDialog() {
  deleteConfirmName.value = ''
  deleteDialogVisible.value = true
}

function closeDeleteDialog() {
  deleteDialogVisible.value = false
  deleteConfirmName.value = ''
}

async function submitDelete() {
  const pkg = store.currentPackage
  if (!pkg) {
    return
  }

  if (deleteConfirmName.value.trim() !== pkg.name) {
    ElMessage.error('包名确认不匹配，拒绝删除')
    return
  }

  try {
    await store.removePackage(packageId.value, deleteConfirmName.value.trim())
    ElMessage.success('删除成功')
    closeDeleteDialog()
    void router.push({ name: 'packages' })
  } catch {
    ElMessage.error('删除失败，请重试')
  }
}

onMounted(() => {
  void loadDetail()
})
</script>

<style scoped>
.detail-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.detail-header__title {
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
}

.detail-header__hint {
  margin-top: 4px;
  font-size: 13px;
  color: #64748b;
}

.status-alert {
  margin-bottom: 16px;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-bottom: 20px;
}

.stat-card :deep(.el-card__body) {
  padding: 20px;
}

.stat-card__value {
  font-size: 28px;
  font-weight: 700;
  color: #0f172a;
}

.stat-card__label {
  margin-top: 6px;
  font-size: 13px;
  color: #64748b;
}

.detail-descriptions {
  margin-top: 8px;
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
