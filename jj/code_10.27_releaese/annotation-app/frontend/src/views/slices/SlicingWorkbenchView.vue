<template>
  <div class="page-shell">
    <div class="page-toolbar">
      <div>
        <div class="page-toolbar__title">数据切片</div>
        <div class="page-toolbar__hint">基于已导入的 source_log_lines 创建数据库驱动切片任务并浏览窗口摘要</div>
      </div>
    </div>

    <el-row :gutter="20" class="workbench-row">
      <el-col :span="7">
        <el-card shadow="never" class="console-card panel-card">
          <template #header>
            <div class="panel-header">
              <span>数据包</span>
              <span class="panel-header__meta">{{ packageStore.packages.length }} 个</span>
            </div>
          </template>

          <div class="package-toolbar">
            <el-button text @click="refreshPackages">刷新</el-button>
          </div>

          <el-table :data="packageStore.packages" v-loading="packageStore.loading" empty-text="暂无数据包" @row-click="selectPackage">
            <el-table-column prop="name" label="名称" min-width="180" />
            <el-table-column label="种类" width="100">
              <template #default="{ row }">
                <el-tag :type="dataKindTag(row.data_kind)" effect="plain" size="small">
                  {{ dataKindLabel(row.data_kind) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="120">
              <template #default="{ row }">
                <el-tag :type="packageStatusTag(row.import_status)" effect="light">{{ row.import_status }}</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="17">
        <el-card shadow="never" class="console-card panel-card">
          <template #header>
            <div class="panel-header">
              <span>切片任务</span>
              <span class="panel-header__meta">{{ currentPackageTitle }}</span>
            </div>
          </template>

          <el-alert
            v-if="selectedPackage && selectedPackage.import_status !== 'imported'"
            type="warning"
            :closable="false"
            show-icon
            title="当前数据包导入未完成，禁止创建切片任务。"
            class="panel-alert"
          />

          <div class="slice-toolbar">
            <el-form inline @submit.prevent>
              <el-form-item label="任务名">
                <el-input v-model="createForm.name" placeholder="例如：5min-default" data-testid="task-name-input" />
              </el-form-item>
              <el-form-item v-if="!isUnstructured" label="窗口长度">
                <el-input-number
                  v-model="windowAmount"
                  :min="1"
                  :max="windowUnit === 'second' ? 3600 : 60"
                  :step="1"
                  data-testid="task-window-amount"
                  style="width: 140px"
                />
                <el-select v-model="windowUnit" data-testid="task-window-unit" style="width: 100px; margin-left: 8px">
                  <el-option label="秒" value="second" />
                  <el-option label="分钟" value="minute" />
                </el-select>
              </el-form-item>
              <el-form-item v-else label="切分方式">
                <el-tag type="info" effect="plain">非结构化数据：按语义段（## 标题）切分</el-tag>
              </el-form-item>
              <el-form-item>
                <el-button
                  type="primary"
                  :disabled="!canCreateTask"
                  :loading="sliceTaskStore.creating"
                  data-testid="task-create-submit"
                  @click="submitCreate"
                >
                  创建切片
                </el-button>
              </el-form-item>
            </el-form>
          </div>

          <el-table :data="sliceTaskStore.tasks" v-loading="sliceTaskStore.loading" empty-text="请选择数据包" @row-click="selectTask">
            <el-table-column prop="name" label="任务名" min-width="180" />
            <el-table-column label="窗口" width="120">
              <template #default="{ row }">{{ formatWindowDuration(row.window_seconds) }}</template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="120">
              <template #default="{ row }">
                <el-tag :type="taskStatusTag(row.status)" effect="light">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="文件 / 行 / 窗口" min-width="180">
              <template #default="{ row }">{{ row.total_files }} / {{ row.total_lines }} / {{ row.total_windows }}</template>
            </el-table-column>
            <el-table-column label="操作" width="240" fixed="right">
              <template #default="{ row }">
                <el-space>
                  <el-button link type="primary" :data-testid="`task-browse-button-${row.id}`" @click="goBrowse(row.id)">浏览窗口</el-button>
                  <el-button link type="primary" @click="recreateTask(row.name, row.window_seconds)">重新切片</el-button>
                  <el-button link type="danger" :data-testid="`task-delete-button-${row.id}`" @click="deleteTask(row.id)">删除</el-button>
                </el-space>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <el-card shadow="never" class="console-card panel-card">
          <template #header>
            <div class="panel-header">
              <span>窗口摘要</span>
              <span class="panel-header__meta">{{ selectedTaskTitle }}</span>
            </div>
          </template>

          <el-table :data="windowSummaries" empty-text="请选择切片任务">
            <el-table-column prop="id" label="窗口ID" width="100" />
            <el-table-column label="时间范围 / 语义段" min-width="240">
              <template #default="{ row }">
                <span v-if="row.segment_title">{{ row.segment_title }}</span>
                <span v-else>{{ formatWindowRange(row.window_start_ts, row.window_end_ts) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="文件 / 行 / CPU / 模块" min-width="240">
              <template #default="{ row }">{{ row.file_count }} / {{ row.line_count }} / {{ row.cpu_count }} / {{ row.module_count }}</template>
            </el-table-column>
            <el-table-column label="操作" width="120">
              <template #default="{ row }">
                <el-button link type="primary" @click="goWindow(row.id)">进入窗口</el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { usePackageStore } from '@annotation/stores/packageStore'
import { useSliceTaskStore } from '@annotation/stores/sliceTaskStore'
import type { SliceTaskSummary } from '@annotation/types/sliceTask'
import type { DataKind } from '@annotation/types/package'

const router = useRouter()
const packageStore = usePackageStore()
const sliceTaskStore = useSliceTaskStore()

const selectedPackageId = ref<number | null>(null)
const selectedTaskId = ref<number | null>(null)

const createForm = reactive({
  name: ''
})
const windowAmount = ref<number>(5)
const windowUnit = ref<'second' | 'minute'>('minute')

const MIN_WINDOW_SECONDS = 1
const MAX_WINDOW_SECONDS = 3600

function computeWindowSeconds(): number {
  const amount = Number(windowAmount.value)
  if (!Number.isFinite(amount) || amount <= 0) return 0
  return windowUnit.value === 'second' ? Math.round(amount) : Math.round(amount * 60)
}

function formatWindowDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '-'
  if (seconds % 60 === 0) return `${seconds / 60} 分`
  if (seconds < 60) return `${seconds} 秒`
  return `${Math.floor(seconds / 60)}分${seconds % 60}秒`
}

const selectedPackage = computed(() => packageStore.packages.find((item) => item.id === selectedPackageId.value) ?? null)
const isUnstructured = computed(() => selectedPackage.value?.data_kind === 'unstructured')
const currentPackageTitle = computed(() => selectedPackage.value?.name || '先在左侧选择一个数据包')
const selectedTask = computed(() => sliceTaskStore.currentTask ?? sliceTaskStore.tasks.find((item) => item.id === selectedTaskId.value) ?? null)
const selectedTaskTitle = computed(() => selectedTask.value?.name || '当前未选择切片任务')
const windowSummaries = computed(() => sliceTaskStore.currentTask?.windows ?? [])
const canCreateTask = computed(() => Boolean(selectedPackage.value && selectedPackage.value.import_status === 'imported'))

function packageStatusTag(status: string) {
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

function taskStatusTag(status: string) {
  if (status === 'success') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'running') return 'warning'
  return 'info'
}

function formatWindowRange(start: number, end: number) {
  return `${formatTimestamp(start)} ~ ${formatTimestamp(end)}`
}

function formatTimestamp(value: number) {
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

async function refreshPackages() {
  try {
    await packageStore.fetchPackages()
  } catch {
    ElMessage.error('获取数据包列表失败')
  }
}

async function refreshTasks() {
  if (!selectedPackageId.value) {
    return
  }

  try {
    await sliceTaskStore.fetchTasks(selectedPackageId.value)
    if (sliceTaskStore.tasks.length > 0 && !selectedTaskId.value) {
      await selectTask(sliceTaskStore.tasks[0])
    }
  } catch {
    ElMessage.error('获取切片任务失败')
  }
}

async function selectPackage(row: { id: number }) {
  selectedPackageId.value = row.id
  selectedTaskId.value = null
  await refreshTasks()
}

async function selectTask(row: Pick<SliceTaskSummary, 'id'>) {
  try {
    selectedTaskId.value = row.id
    await sliceTaskStore.fetchTaskDetail(row.id)
  } catch {
    ElMessage.error('获取切片任务详情失败')
  }
}

async function submitCreate() {
  if (!selectedPackageId.value) {
    ElMessage.warning('请先选择数据包')
    return
  }

  if (!createForm.name.trim()) {
    ElMessage.warning('请输入切片任务名')
    return
  }

  const windowSeconds = computeWindowSeconds()
  if (windowSeconds < MIN_WINDOW_SECONDS || windowSeconds > MAX_WINDOW_SECONDS) {
    ElMessage.warning(`窗口长度必须在 ${MIN_WINDOW_SECONDS} ~ ${MAX_WINDOW_SECONDS} 秒之间`)
    return
  }

  try {
    const created = await sliceTaskStore.createTask(selectedPackageId.value, {
      name: createForm.name.trim(),
      window_seconds: windowSeconds
    })
    await selectTask(created)
    createForm.name = ''
    windowAmount.value = 5
    windowUnit.value = 'minute'
    ElMessage.success('切片任务创建成功')
  } catch {
    ElMessage.error('切片任务创建失败')
  }
}

function recreateTask(name: string, windowSeconds: number) {
  createForm.name = `${name}-retry`
  if (windowSeconds % 60 === 0) {
    windowUnit.value = 'minute'
    windowAmount.value = windowSeconds / 60
  } else {
    windowUnit.value = 'second'
    windowAmount.value = windowSeconds
  }
}

async function deleteTask(taskId: number) {
  if (!selectedPackageId.value) {
    return
  }

  try {
    await sliceTaskStore.deleteTask(taskId, selectedPackageId.value)
    if (selectedTaskId.value === taskId) {
      selectedTaskId.value = sliceTaskStore.tasks[0]?.id ?? null
    }
    ElMessage.success('切片任务删除成功')
  } catch {
    ElMessage.error('切片任务删除失败')
  }
}

function goBrowse(taskId: number) {
  if (!selectedPackageId.value) {
    return
  }

  void router.push({
    name: 'slice-window-browser',
    params: { id: selectedPackageId.value, taskId },
    query: { from: 'slicing' }
  })
}

function goWindow(windowId: number) {
  if (!selectedPackageId.value || !selectedTaskId.value) {
    return
  }
  void router.push({
    name: 'slice-window-browser',
    params: { id: selectedPackageId.value, taskId: selectedTaskId.value },
    query: { window_id: windowId, from: 'slicing' }
  })
}

onMounted(() => {
  void refreshPackages()
})

defineExpose({
  windowAmount,
  windowUnit,
  selectPackage,
  selectTask,
  goWindow
})
</script>

<style scoped>
.workbench-row {
  width: 100%;
}

.panel-card {
  min-height: 360px;
}

.panel-card + .panel-card {
  margin-top: 20px;
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

.package-toolbar,
.slice-toolbar {
  margin-bottom: 16px;
}

.panel-alert {
  margin-bottom: 12px;
}
</style>
