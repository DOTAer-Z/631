<template>
  <div class="page-shell">
    <div class="page-toolbar">
      <div>
        <div class="page-toolbar__title">故障类型管理</div>
        <div class="page-toolbar__hint">维护可在标注与智能推荐中选择的故障类型字典</div>
      </div>

      <el-space>
        <el-button data-testid="back-to-workbench" @click="goBack">返回数据标注</el-button>
        <el-badge
          :value="suggestionStore.pendingTotal"
          :hidden="!suggestionStore.pendingTotal"
          :max="99"
          class="suggestion-entry-badge"
        >
          <el-button data-testid="fault-type-suggestion-entry" @click="goSuggestions">待审建议</el-button>
        </el-badge>
        <el-button type="primary" data-testid="fault-type-create-button" @click="openCreate">新增故障类型</el-button>
      </el-space>
    </div>

    <el-card shadow="never" class="console-card">
      <el-table :data="store.items" v-loading="store.loading" empty-text="暂无故障类型，请先新增">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" label="名称" width="200" />
        <el-table-column prop="description" label="描述" min-width="320" show-overflow-tooltip />
        <el-table-column label="updated_at" width="200">
          <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160">
          <template #default="{ row }">
            <el-space>
              <el-button size="small" data-testid="fault-type-edit-button" @click="openEdit(row)">编辑</el-button>
              <el-button size="small" type="danger" :loading="store.removing" data-testid="fault-type-delete-button" @click="confirmDelete(row)">
                删除
              </el-button>
            </el-space>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="520px" :close-on-click-modal="false">
      <el-form :model="form" label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="form.name" maxlength="128" data-testid="fault-type-name-input" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="5"
            maxlength="20000"
            show-word-limit
            data-testid="fault-type-description-input"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="store.saving" data-testid="fault-type-submit-button" @click="submit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useFaultTypeStore } from '@annotation/stores/faultTypeStore'
import { useFaultTypeSuggestionStore } from '@annotation/stores/faultTypeSuggestionStore'
import type { FaultType } from '@annotation/types/faultType'

const router = useRouter()
const store = useFaultTypeStore()
const suggestionStore = useFaultTypeSuggestionStore()

const dialogVisible = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({ name: '', description: '' })

const dialogTitle = ref('新增故障类型')

function formatDateTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function openCreate() {
  editingId.value = null
  form.name = ''
  form.description = ''
  dialogTitle.value = '新增故障类型'
  dialogVisible.value = true
}

function openEdit(row: FaultType) {
  editingId.value = row.id
  form.name = row.name
  form.description = row.description
  dialogTitle.value = '编辑故障类型'
  dialogVisible.value = true
}

async function submit() {
  const name = form.name.trim()
  const description = form.description.trim()
  if (!name) {
    ElMessage.error('名称不能为空')
    return
  }
  if (!description) {
    ElMessage.error('描述不能为空')
    return
  }
  try {
    if (editingId.value === null) {
      await store.create({ name, description })
      ElMessage.success('新增成功')
    } else {
      await store.update(editingId.value, { name, description })
      ElMessage.success('更新成功')
    }
    dialogVisible.value = false
  } catch (error) {
    const detail = (error as { response?: { data?: { message?: string } } }).response?.data?.message
    ElMessage.error(detail || '保存失败')
  }
}

async function confirmDelete(row: FaultType) {
  try {
    await ElMessageBox.confirm(`确认删除故障类型「${row.name}」?`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    const result = await store.remove(row.id)
    if (result.referenced_count > 0) {
      ElMessage.warning(`已删除，但有 ${result.referenced_count} 条历史标注引用此类型，原值已保留`)
    } else {
      ElMessage.success('删除成功')
    }
  } catch (error) {
    const detail = (error as { response?: { data?: { message?: string } } }).response?.data?.message
    ElMessage.error(detail || '删除失败')
  }
}

function goBack() {
  router.push({ name: 'annotation-workbench' })
}

function goSuggestions() {
  router.push({ name: 'fault-type-suggestion-list' })
}

onMounted(async () => {
  try {
    await store.fetchAll()
  } catch {
    ElMessage.error('加载故障类型失败')
  }
  // Surface the LLM suggestion backlog as a badge — non-blocking.
  void suggestionStore.refreshPendingCount().catch(() => undefined)
})

defineExpose({ openCreate, openEdit, submit, confirmDelete, goBack, goSuggestions })
</script>
