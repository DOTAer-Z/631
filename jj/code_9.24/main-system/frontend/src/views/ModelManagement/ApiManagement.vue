<template>
  <div class="api-management-page">
    <div class="page-toolbar">
      <div>
        <h2>大模型 API</h2>
        <p>管理日志分析、故障诊断和预测预警使用的运行时模型接口。</p>
      </div>
      <div class="toolbar-actions">
        <el-button
          :icon="Refresh"
          :loading="loading"
          :disabled="conflictingWork"
          @click="loadConfigs"
        >
          刷新
        </el-button>
        <el-button
          type="primary"
          :icon="Plus"
          :disabled="loading || conflictingWork"
          @click="openCreate"
        >
          新增配置
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="loadError"
      :title="loadError"
      type="error"
      show-icon
      :closable="false"
    />
    <el-alert
      v-if="operationError"
      :title="operationError"
      type="error"
      show-icon
      closable
      @close="operationError = ''"
    />

    <section class="active-section" v-loading="loading">
      <div class="section-header">
        <h3>当前生效配置</h3>
      </div>
      <el-descriptions v-if="activeConfig" :column="3" border>
        <el-descriptions-item label="名称">{{ activeConfig.name }}</el-descriptions-item>
        <el-descriptions-item label="服务类型">
          {{ providerLabel(activeConfig.provider) }}
        </el-descriptions-item>
        <el-descriptions-item label="模型">{{ activeConfig.model }}</el-descriptions-item>
        <el-descriptions-item label="Base URL" :span="2">
          {{ activeConfig.base_url }}
        </el-descriptions-item>
        <el-descriptions-item label="API Key">
          <el-tag :type="activeConfig.api_key_configured ? 'success' : 'danger'" size="small">
            {{ activeConfig.api_key_configured ? '已配置' : '未配置' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="超时">
          {{ activeConfig.timeout_seconds }} 秒
        </el-descriptions-item>
        <el-descriptions-item label="最大输出">
          {{ activeConfig.max_output_tokens }} Token
        </el-descriptions-item>
        <el-descriptions-item label="操作">
          <el-button
            size="small"
            :icon="VideoPlay"
            :loading="isRowOperation('test', activeConfig.id)"
            :disabled="rowOperationActive"
            @click="testConfig(activeConfig)"
          >
            测试连接
          </el-button>
        </el-descriptions-item>
      </el-descriptions>
      <el-empty
        v-else-if="!loading && !loadError"
        description="尚未启用大模型 API 配置"
        :image-size="72"
      >
        <el-button
          type="primary"
          :disabled="loading || conflictingWork"
          @click="openCreate"
        >
          新增配置
        </el-button>
      </el-empty>
    </section>

    <section class="list-section">
      <div class="section-header">
        <h3>API 配置列表</h3>
      </div>
      <div class="table-wrap">
        <el-table :data="configs" size="small" v-loading="loading" row-key="id">
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                {{ row.is_active ? '使用中' : '未启用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="名称" min-width="140" show-overflow-tooltip />
          <el-table-column label="服务类型" width="150">
            <template #default="{ row }">{{ providerLabel(row.provider) }}</template>
          </el-table-column>
          <el-table-column prop="base_url" label="Base URL" min-width="240" show-overflow-tooltip />
          <el-table-column prop="model" label="模型" min-width="170" show-overflow-tooltip />
          <el-table-column label="凭证" width="90">
            <template #default="{ row }">
              <el-tag
                :type="row.api_key_configured ? 'success' : 'danger'"
                effect="plain"
                size="small"
              >
                {{ row.api_key_configured ? '已配置' : '缺失' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="292" fixed="right">
            <template #default="{ row }">
              <el-button
                size="small"
                :icon="VideoPlay"
                :loading="isRowOperation('test', row.id)"
                :disabled="rowOperationActive"
                @click="testConfig(row)"
              >
                测试
              </el-button>
              <el-button
                size="small"
                :icon="Edit"
                :disabled="rowOperationActive"
                @click="openEdit(row)"
              >
                编辑
              </el-button>
              <el-button
                v-if="!row.is_active"
                size="small"
                type="primary"
                :icon="CircleCheck"
                :loading="isRowOperation('activate', row.id)"
                :disabled="rowOperationActive"
                @click="activateConfig(row)"
              >
                启用
              </el-button>
              <el-button
                v-if="!row.is_active"
                size="small"
                type="danger"
                :icon="Delete"
                :loading="isRowOperation('delete', row.id)"
                :disabled="rowOperationActive"
                aria-label="删除配置"
                title="删除配置"
                @click="removeConfig(row)"
              />
            </template>
          </el-table-column>
        </el-table>
      </div>
      <el-empty
        v-if="!loading && !loadError && !configs.length"
        description="暂无 API 配置"
        :image-size="72"
      />
    </section>

    <ApiConfigDialog
      v-model="dialogVisible"
      :config="editingConfig"
      :submitting="submitting"
      @submit="saveConfig"
    />

    <el-dialog v-model="testDialogVisible" title="连接测试结果" width="520px">
      <el-result
        v-if="testResult"
        :icon="testResult.success ? 'success' : 'error'"
        :title="testResult.success ? '连接成功' : '连接失败'"
        :sub-title="testResultSubtitle"
      >
        <template #extra>
          <el-text v-if="testResult.message">{{ testResult.message }}</el-text>
        </template>
      </el-result>
    </el-dialog>
  </div>
</template>

<script>
export function createExclusiveOperationState() {
  let activeToken = null
  let nextToken = 0

  return {
    start() {
      if (activeToken !== null) return null
      nextToken += 1
      activeToken = nextToken
      return activeToken
    },
    finish(token) {
      if (activeToken !== token) return false
      activeToken = null
      return true
    },
    isActive() {
      return activeToken !== null
    },
  }
}

export function createLatestRequestState() {
  let latestRequestId = 0

  return {
    start() {
      latestRequestId += 1
      return latestRequestId
    },
    isLatest(requestId) {
      return requestId === latestRequestId
    },
  }
}
</script>

<script setup>
import { computed, onMounted, ref, shallowRef } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, Delete, Edit, Plus, Refresh, VideoPlay } from '@element-plus/icons-vue'
import {
  activateModelApiConfig,
  createModelApiConfig,
  deleteModelApiConfig,
  listModelApiConfigs,
  testModelApiConfig,
  updateModelApiConfig,
} from '@/api/modelApiConfig'
import ApiConfigDialog from './components/ApiConfigDialog.vue'
import { providerLabel } from './modelApiConfigForm'

const configs = ref([])
const loading = ref(false)
const loadError = ref('')
const operationError = ref('')
const dialogVisible = ref(false)
const editingConfig = ref(null)
const submitting = ref(false)
const rowOperation = shallowRef(null)
const testDialogVisible = ref(false)
const testResult = ref(null)
const testedConfig = ref(null)
const rowOperations = createExclusiveOperationState()
const configRequests = createLatestRequestState()

const activeConfig = computed(() => configs.value.find((item) => item.is_active) || null)
const rowOperationActive = computed(() => Boolean(rowOperation.value))
const conflictingWork = computed(() => submitting.value || rowOperationActive.value)
const testResultSubtitle = computed(() => {
  if (!testResult.value) return ''
  if (!testResult.value.success) return testResult.value.error || '连接测试失败'
  const model = testResult.value.model || testedConfig.value?.model || ''
  const latency = testResult.value.latency_ms
  const latencyLabel = latency === null || latency === undefined ? '' : `${latency} ms`
  return [latencyLabel, model]
    .filter(Boolean)
    .join(' / ')
})

function errorDetail(error, fallback) {
  return error?.response?.data?.detail || fallback
}

function isCancelled(error) {
  return error === 'cancel' || error === 'close'
}

function startRowOperation(type, id) {
  const operation = rowOperations.start()
  if (operation === null) return null
  rowOperation.value = { operation, type, id }
  return operation
}

function finishRowOperation(operation) {
  if (rowOperations.finish(operation)) rowOperation.value = null
}

function isRowOperation(type, id) {
  return rowOperation.value?.type === type && rowOperation.value?.id === id
}

async function loadConfigs() {
  const requestId = configRequests.start()
  loading.value = true
  loadError.value = ''
  try {
    const response = await listModelApiConfigs()
    if (!configRequests.isLatest(requestId)) return
    configs.value = Array.isArray(response?.items) ? response.items : []
  } catch (error) {
    if (!configRequests.isLatest(requestId)) return
    configs.value = []
    loadError.value = errorDetail(error, 'API 配置加载失败，请检查后端接口。')
  } finally {
    if (configRequests.isLatest(requestId)) loading.value = false
  }
}

function openCreate() {
  if (loading.value || conflictingWork.value) return
  operationError.value = ''
  editingConfig.value = null
  dialogVisible.value = true
}

function openEdit(config) {
  operationError.value = ''
  editingConfig.value = config
  dialogVisible.value = true
}

async function saveConfig(payload) {
  submitting.value = true
  operationError.value = ''
  try {
    if (editingConfig.value) {
      await updateModelApiConfig(editingConfig.value.id, payload)
      ElMessage.success('API 配置已更新')
    } else {
      await createModelApiConfig(payload)
      ElMessage.success('API 配置已创建')
    }
    dialogVisible.value = false
    await loadConfigs()
  } catch (error) {
    operationError.value = errorDetail(error, 'API 配置保存失败，请稍后重试。')
  } finally {
    submitting.value = false
  }
}

async function activateConfig(config) {
  const operation = startRowOperation('activate', config.id)
  if (!operation) return
  operationError.value = ''
  try {
    try {
      await ElMessageBox.confirm(
        `启用“${config.name}”后，新的大模型请求将使用该配置。`,
        '切换生效配置',
        { type: 'warning', confirmButtonText: '启用', cancelButtonText: '取消' },
      )
    } catch (error) {
      if (!isCancelled(error)) operationError.value = '无法打开启用确认，请稍后重试。'
      return
    }
    await activateModelApiConfig(config.id)
    ElMessage.success(`已启用 ${config.name}`)
    await loadConfigs()
  } catch (error) {
    operationError.value = errorDetail(error, 'API 配置启用失败，请稍后重试。')
  } finally {
    finishRowOperation(operation)
  }
}

async function testConfig(config) {
  const operation = startRowOperation('test', config.id)
  if (!operation) return
  operationError.value = ''
  testedConfig.value = config
  testResult.value = null
  try {
    testResult.value = await testModelApiConfig(config.id)
  } catch (error) {
    testResult.value = {
      success: false,
      error: errorDetail(error, '连接测试失败'),
      message: null,
      latency_ms: 0,
      model: config.model,
    }
  } finally {
    finishRowOperation(operation)
    testDialogVisible.value = true
  }
}

async function removeConfig(config) {
  if (config.is_active) return
  const operation = startRowOperation('delete', config.id)
  if (!operation) return
  operationError.value = ''
  try {
    try {
      await ElMessageBox.confirm(
        `删除“${config.name}”后无法恢复。`,
        '删除 API 配置',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
      )
    } catch (error) {
      if (!isCancelled(error)) operationError.value = '无法打开删除确认，请稍后重试。'
      return
    }
    await deleteModelApiConfig(config.id)
    ElMessage.success('API 配置已删除')
    await loadConfigs()
  } catch (error) {
    operationError.value = errorDetail(error, 'API 配置删除失败，请稍后重试。')
  } finally {
    finishRowOperation(operation)
  }
}

onMounted(loadConfigs)
</script>

<style scoped>
.api-management-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.page-toolbar,
.toolbar-actions {
  display: flex;
  align-items: center;
}

.page-toolbar {
  justify-content: space-between;
  gap: 16px;
}

.toolbar-actions {
  gap: 8px;
  flex-shrink: 0;
}

.page-toolbar h2 {
  margin: 0;
  color: #1f2937;
  font-size: 22px;
  letter-spacing: 0;
}

.page-toolbar p {
  margin: 6px 0 0;
  color: #64748b;
  font-size: 14px;
}

.active-section,
.list-section {
  min-width: 0;
  padding-top: 4px;
}

.section-header {
  margin-bottom: 12px;
  border-bottom: 1px solid #e5e7eb;
}

.section-header h3 {
  margin: 0 0 10px;
  color: #374151;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0;
}

.table-wrap {
  width: 100%;
  overflow-x: auto;
}

.table-wrap :deep(.el-table) {
  min-width: 1040px;
}

@media (max-width: 760px) {
  .page-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .toolbar-actions {
    width: 100%;
  }

  .toolbar-actions .el-button {
    flex: 1;
  }

  :deep(.el-dialog) {
    width: calc(100% - 32px) !important;
  }

  :deep(.el-descriptions__body .el-descriptions__table) {
    min-width: 620px;
  }

  .active-section {
    overflow-x: auto;
  }
}

@media (max-width: 480px) {
  .toolbar-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .toolbar-actions .el-button {
    flex: none;
    width: 100%;
    margin-left: 0;
  }
}
</style>
