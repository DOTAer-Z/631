<template>
  <div class="page-shell">
    <div class="page-toolbar">
      <div>
        <div class="page-toolbar__title">故障类型建议（待审）</div>
        <div class="page-toolbar__hint">LLM 在两步推荐流程中识别出已定义字典之外的新故障类型，需要人工审核后再纳入</div>
      </div>
      <el-space>
        <el-button data-testid="suggestion-back-button" @click="goBack">返回故障类型管理</el-button>
      </el-space>
    </div>

    <el-card shadow="never" class="console-card">
      <el-form inline @submit.prevent class="suggestion-toolbar">
        <el-form-item label="状态">
          <el-select v-model="filterStatus" data-testid="suggestion-filter-status" style="width: 140px" @change="reload">
            <el-option label="待审" value="pending" />
            <el-option label="已采纳" value="accepted" />
            <el-option label="已拒绝" value="rejected" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button data-testid="suggestion-reload-button" @click="reload">刷新</el-button>
        </el-form-item>
      </el-form>

      <el-table :data="store.items" v-loading="store.loading" empty-text="暂无建议">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="suggested_name" label="建议名称" min-width="160" />
        <el-table-column prop="suggested_description" label="建议定义" min-width="240" show-overflow-tooltip />
        <el-table-column prop="reason" label="理由" min-width="240" show-overflow-tooltip>
          <template #default="{ row }">{{ row.reason || '-' }}</template>
        </el-table-column>
        <el-table-column label="来源窗口" width="110">
          <template #default="{ row }">
            <span>#{{ row.slice_window_id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="model" label="模型" width="160" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag>{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-space :size="6">
              <el-button
                size="small"
                type="primary"
                :disabled="row.status !== 'pending'"
                :loading="store.acting"
                data-testid="suggestion-accept-button"
                @click="openAccept(row)"
              >
                采纳
              </el-button>
              <el-button
                size="small"
                :disabled="row.status !== 'pending'"
                :loading="store.acting"
                data-testid="suggestion-reject-button"
                @click="rejectRow(row)"
              >
                拒绝
              </el-button>
              <el-button
                size="small"
                type="danger"
                plain
                :loading="store.acting"
                data-testid="suggestion-delete-button"
                @click="confirmDelete(row)"
              >
                删除
              </el-button>
            </el-space>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          background
          layout="total, prev, pager, next"
          :current-page="store.page"
          :page-size="store.pageSize"
          :total="store.total"
          @current-change="handlePageChange"
        />
      </div>
    </el-card>

    <el-dialog
      v-model="acceptDialogVisible"
      title="采纳建议为故障类型"
      width="540px"
      :close-on-click-modal="false"
    >
      <el-form :model="acceptForm" label-width="100px">
        <el-form-item label="名称">
          <el-input v-model="acceptForm.name" maxlength="128" data-testid="suggestion-accept-name" />
        </el-form-item>
        <el-form-item label="定义">
          <el-input
            v-model="acceptForm.description"
            type="textarea"
            :rows="5"
            maxlength="20000"
            show-word-limit
            data-testid="suggestion-accept-description"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="acceptDialogVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="store.acting"
          data-testid="suggestion-accept-submit"
          @click="submitAccept"
        >
          确认采纳
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useFaultTypeSuggestionStore } from '@annotation/stores/faultTypeSuggestionStore'
import type {
  FaultTypeSuggestion,
  FaultTypeSuggestionStatus
} from '@annotation/types/faultTypeSuggestion'

const router = useRouter()
const store = useFaultTypeSuggestionStore()

const filterStatus = ref<FaultTypeSuggestionStatus>('pending')
const acceptDialogVisible = ref(false)
const acceptingId = ref<number | null>(null)
const acceptForm = reactive<{ name: string; description: string }>({ name: '', description: '' })

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function goBack() {
  router.push({ name: 'fault-type-manage' })
}

async function reload() {
  try {
    store.setFilterStatus(filterStatus.value)
    await store.fetchAll({ status: filterStatus.value, page: 1 })
  } catch {
    ElMessage.error('加载建议失败')
  }
}

async function handlePageChange(value: number) {
  try {
    await store.fetchAll({ page: value })
  } catch {
    ElMessage.error('分页加载失败')
  }
}

function openAccept(row: FaultTypeSuggestion) {
  acceptingId.value = row.id
  acceptForm.name = row.suggested_name
  acceptForm.description = row.suggested_description
  acceptDialogVisible.value = true
}

async function submitAccept() {
  if (acceptingId.value === null) return
  const name = acceptForm.name.trim()
  const description = acceptForm.description.trim()
  if (!name || !description) {
    ElMessage.error('名称和定义都不能为空')
    return
  }
  try {
    await store.accept(acceptingId.value, { name, description })
    acceptDialogVisible.value = false
    ElMessage.success('已采纳并新增故障类型')
    await reload()
    await store.refreshPendingCount()
  } catch (error) {
    const detail = (error as { response?: { data?: { message?: string } } }).response?.data?.message
    ElMessage.error(detail || '采纳失败')
  }
}

async function rejectRow(row: FaultTypeSuggestion) {
  try {
    await ElMessageBox.confirm(`确认拒绝建议 #${row.id}?`, '拒绝确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await store.reject(row.id)
    ElMessage.success('已拒绝')
    await reload()
    await store.refreshPendingCount()
  } catch {
    ElMessage.error('拒绝失败')
  }
}

async function confirmDelete(row: FaultTypeSuggestion) {
  try {
    await ElMessageBox.confirm(`确认删除建议 #${row.id}?`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await store.remove(row.id)
    ElMessage.success('已删除')
    await reload()
    await store.refreshPendingCount()
  } catch {
    ElMessage.error('删除失败')
  }
}

defineExpose({ openAccept, submitAccept, rejectRow, confirmDelete, reload })

onMounted(() => {
  void reload()
})
</script>

<style scoped>
.suggestion-toolbar {
  margin-bottom: 16px;
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>