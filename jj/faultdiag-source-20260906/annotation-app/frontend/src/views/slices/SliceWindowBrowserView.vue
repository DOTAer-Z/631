<template>
  <div class="page-shell">
    <el-page-header @back="goBack">
      <template #content>
        <span>窗口浏览</span>
      </template>
    </el-page-header>

    <el-card shadow="never" class="console-card" v-loading="store.loadingWindow">
      <template #header>
        <div class="panel-header">
          <span>Window Summary</span>
          <span class="panel-header__meta">{{ currentWindowRange }}</span>
        </div>
      </template>

      <div class="summary-grid">
        <div class="summary-item">
          <strong>{{ store.selectedWindow?.line_count ?? 0 }}</strong>
          <span>日志行数</span>
        </div>
        <div class="summary-item">
          <strong>{{ store.selectedWindow?.file_count ?? 0 }}</strong>
          <span>文件数</span>
        </div>
        <div class="summary-item">
          <strong>{{ store.selectedWindow?.cpu_count ?? 0 }}</strong>
          <span>CPU 数</span>
        </div>
        <div class="summary-item">
          <strong>{{ store.selectedWindow?.module_count ?? 0 }}</strong>
          <span>模块数</span>
        </div>
      </div>

      <div class="navigation-row">
        <el-select
          v-model="selectedWindowId"
          class="window-switcher"
          placeholder="选择窗口"
          data-testid="window-selector"
          @update:modelValue="handleWindowChange"
        >
          <el-option
            v-for="item in store.windows"
            :key="item.window_id"
            :label="formatWindowOption(item.window_id, item.window_start_ts, item.window_end_ts, item.segment_title)"
            :value="item.window_id"
          />
        </el-select>
        <el-button :disabled="!store.navigation?.prev_window_id" @click="jumpWindow(store.navigation?.prev_window_id)">上一窗口</el-button>
        <el-button type="primary" @click="goAnnotation">进入标注工作台</el-button>
        <el-button data-testid="window-subdivide-button" :disabled="!selectedWindowId" @click="openSubdivide">细分窗口</el-button>
        <el-button :disabled="!store.navigation?.next_window_id" @click="jumpWindow(store.navigation?.next_window_id)">下一窗口</el-button>
      </div>
    </el-card>

    <el-dialog v-model="subdivideVisible" title="细分当前窗口" width="420px">
      <el-form label-width="120px">
        <el-form-item label="子窗口长度(秒)">
          <el-input-number v-model="subdivideSeconds" :min="1" :max="3600" data-testid="subdivide-seconds-input" />
        </el-form-item>
        <p class="subdivide-hint">将当前时间窗口按更小粒度细分为子窗口，原窗口与其标注保留。</p>
      </el-form>
      <template #footer>
        <el-button @click="subdivideVisible = false">取消</el-button>
        <el-button type="primary" :loading="subdividing" data-testid="subdivide-confirm" @click="confirmSubdivide">确定</el-button>
      </template>
    </el-dialog>

    <el-card shadow="never" class="console-card" data-testid="window-annotation-card">
      <template #header>
        <div class="panel-header">
          <span>标注结果</span>
          <span class="panel-header__meta">{{ store.currentAnnotations.length }} 条</span>
          <el-button
            type="primary"
            size="small"
            :disabled="!selectedWindowId"
            data-testid="annotation-add-button"
            @click="openAddAnnotation"
          >
            新增标注
          </el-button>
        </div>
      </template>

      <el-empty v-if="!store.currentAnnotations.length" description="当前窗口暂无标注" />
      <el-table v-else :data="store.currentAnnotations" data-testid="window-annotation-table">
        <el-table-column label="绑定对象" min-width="200">
          <template #default="{ row }">
            {{ row.source_log_file_id ? (row.binding_label || `文件 #${row.source_log_file_id}`) : '整窗口' }}
          </template>
        </el-table-column>
        <el-table-column label="标签" width="110">
          <template #default="{ row }">
            <el-tag :type="row.label === 'abnormal' ? 'danger' : 'success'">
              {{ row.label === 'abnormal' ? '异常' : '正常' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="anomaly_type" label="故障类型" min-width="140">
          <template #default="{ row }">{{ row.anomaly_type || '-' }}</template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="200">
          <template #default="{ row }">{{ row.note || '-' }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="180">
          <template #default="{ row }">{{ formatDateTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button size="small" data-testid="annotation-edit-button" @click="openEditAnnotation(row)">编辑</el-button>
            <el-button
              size="small"
              type="danger"
              data-testid="annotation-delete-button"
              @click="confirmDeleteAnnotation(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="annotationDialogVisible" :title="annotationDialogTitle" width="520px" :close-on-click-modal="false">
      <el-form label-width="100px">
        <el-form-item label="绑定对象">
          <el-select
            v-model="annotationForm.source_log_file_id"
            :disabled="annotationEditingId !== null"
            clearable
            placeholder="整窗口"
            data-testid="annotation-binding-select"
          >
            <el-option :label="'整窗口'" :value="null" />
            <el-option
              v-for="file in windowFiles"
              :key="file.source_file_id"
              :label="file.path"
              :value="file.source_file_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="标签">
          <el-select v-model="annotationForm.label" data-testid="annotation-label-select">
            <el-option label="normal" value="normal" />
            <el-option label="abnormal" value="abnormal" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="annotationForm.label === 'abnormal'" label="故障类型">
          <el-select
            v-model="annotationForm.anomaly_type"
            filterable
            :placeholder="faultTypeStore.items.length ? '选择已定义的故障类型' : '请先在故障类型管理中定义'"
            :disabled="!faultTypeStore.items.length"
            data-testid="annotation-anomaly-select"
          >
            <el-option v-for="item in faultTypeStore.items" :key="item.id" :label="item.name" :value="item.name" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="annotationForm.note" type="textarea" :rows="4" maxlength="20000" show-word-limit data-testid="annotation-note-input" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="annotationDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="annotationSaving" data-testid="annotation-save-button" @click="saveAnnotation">保存</el-button>
      </template>
    </el-dialog>

    <el-row :gutter="20">
      <el-col :span="8">
        <el-card shadow="never" class="console-card browser-panel">
          <template #header>
            <div class="panel-header">
              <span>Tree</span>
              <span class="panel-header__meta">cpu → module → file</span>
            </div>
          </template>

          <el-empty v-if="!store.tree.length" description="当前窗口暂无文件树" />
          <el-tree
            v-else
            :data="treeNodes"
            node-key="nodeKey"
            :props="{ label: 'name', children: 'children' }"
            @node-click="handleTreeNodeClick"
          />
        </el-card>
      </el-col>

      <el-col :span="16">
        <el-card shadow="never" class="console-card browser-panel" v-loading="store.loadingLogs">
          <template #header>
            <div class="panel-header">
              <span>Logs</span>
              <span class="panel-header__meta">搜索仅作用于当前窗口</span>
            </div>
          </template>

          <div class="logs-toolbar">
            <el-form inline @submit.prevent>
              <el-form-item label="关键字">
                <el-input
                  v-model="searchKeyword"
                  placeholder="仅搜索当前窗口"
                  data-testid="window-search-input"
                  @keyup.enter="handleSearch"
                />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :disabled="!store.selectedSourceFileId" data-testid="window-search-submit" @click="handleSearch">
                  搜索
                </el-button>
                <el-button :disabled="!store.selectedSourceFileId" @click="resetSearch">重置</el-button>
              </el-form-item>
            </el-form>
          </div>

          <div class="file-chip" v-if="activeSourceFileLabel">当前文件：{{ activeSourceFileLabel }}</div>

          <el-empty v-if="!store.selectedSourceFileId" description="请先从左侧树选择日志文件" />

          <template v-else>
            <el-table :data="store.logItems" empty-text="当前窗口内无匹配日志">
              <el-table-column prop="line_no" label="行号" width="90" />
              <el-table-column label="时间戳" width="180">
                <template #default="{ row }">{{ formatTimestamp(row.timestamp) }}</template>
              </el-table-column>
              <el-table-column prop="content" label="内容" min-width="420" />
            </el-table>

            <div class="load-more-row">
              <el-button
                v-if="store.hasMore"
                type="primary"
                plain
                :loading="store.loadingLogs"
                data-testid="window-load-more"
                @click="loadMore"
              >加载更多</el-button>
              <span v-else-if="store.logItems.length" class="load-more-row__done" data-testid="window-load-more-done">
                已显示全部 {{ store.logItems.length }} 行
              </span>
            </div>
          </template>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useSliceWindowStore } from '@annotation/stores/sliceWindowStore'
import { useFaultTypeStore } from '@annotation/stores/faultTypeStore'
import { subdivideSliceWindow } from '@annotation/api/sliceWindows'
import { createAnnotation, updateAnnotation, deleteAnnotation } from '@annotation/api/annotations'
import type { Annotation, AnnotationLabel } from '@annotation/types/annotation'
import type { SliceWindowTreeNode } from '@annotation/types/sliceWindow'

const route = useRoute()
const router = useRouter()
const store = useSliceWindowStore()
const faultTypeStore = useFaultTypeStore()

const taskId = computed(() => route.params.taskId as string)
const initialWindowId = computed(() => route.query.window_id as string | undefined)
// AI-suggested sub-window length passed from the annotation workbench (in seconds).
const suggestedSubdivideSeconds = computed(() => {
  const raw = route.query.suggest_seconds as string | undefined
  const value = raw != null ? Number(raw) : NaN
  return Number.isFinite(value) && value >= 1 && value <= 3600 ? Math.floor(value) : null
})
const searchKeyword = ref('')
const activeSourceFileLabel = ref('')
const selectedWindowId = ref<number | null>(null)
const subdivideVisible = ref(false)
const subdivideSeconds = ref(60)
const subdividing = ref(false)

// Inline annotation add/edit/delete on the browser page.
const annotationDialogVisible = ref(false)
const annotationEditingId = ref<number | null>(null)
const annotationSaving = ref(false)
const annotationForm = reactive<{
  label: AnnotationLabel
  anomaly_type: string | null
  note: string
  source_log_file_id: number | null
}>({ label: 'normal', anomaly_type: null, note: '', source_log_file_id: null })

const annotationDialogTitle = computed(() => (annotationEditingId.value === null ? '新增标注' : '编辑标注'))

// Files in the current window, for the binding selector (derived from the tree).
const windowFiles = computed(() => {
  const files: Array<{ source_file_id: number; path: string }> = []
  const walk = (nodes: SliceWindowTreeNode[]) => {
    for (const node of nodes) {
      if (node.type === 'file' && node.source_file_id != null) {
        files.push({ source_file_id: node.source_file_id, path: node.path || node.name })
      }
      walk(node.children || [])
    }
  }
  walk(store.tree)
  return files
})

const currentWindowRange = computed(() => {
  if (!store.selectedWindow) {
    return '请选择窗口'
  }
  // 非结构化语义段:展示段标题,而非把段序号当 epoch 渲染成 1970 时间。
  if (store.selectedWindow.segment_title) {
    return store.selectedWindow.segment_title
  }
  return `${formatTimestamp(store.selectedWindow.window_start_ts)} ~ ${formatTimestamp(store.selectedWindow.window_end_ts)}`
})

const treeNodes = computed(() => mapTree(store.tree))

function mapTree(nodes: SliceWindowTreeNode[], base = 'root'): Array<SliceWindowTreeNode & { nodeKey: string }> {
  return nodes.map((node, index) => {
    const nodeKey = `${base}-${index}-${node.name}`
    return {
      ...node,
      nodeKey,
      children: mapTree(node.children || [], nodeKey)
    }
  })
}

function formatTimestamp(value: number | null | undefined) {
  if (!value) {
    return '-'
  }
  return new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function formatWindowOption(windowId: number, start: number, end: number, segmentTitle?: string | null) {
  if (segmentTitle) {
    return `#${windowId} ${segmentTitle}`
  }
  return `#${windowId} ${formatTimestamp(start)} ~ ${formatTimestamp(end)}`
}

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return '-'
  }
  return new Date(value).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function goBack() {
  const from = route.query.from as string | undefined
  if (from === 'annotation') {
    void router.push({ name: 'annotation-workbench' })
    return
  }
  if (from === 'annotation-list') {
    void router.push({ name: 'annotation-list' })
    return
  }
  if (from === 'slicing') {
    void router.push({ name: 'slice-task-list', params: { id: route.params.id } })
    return
  }
  void router.push({ name: 'slicing' })
}

async function loadWindows() {
  try {
    const data = await store.fetchWindows(taskId.value)
    const targetId = initialWindowId.value ? Number(initialWindowId.value) : data.items[0]?.window_id
    if (targetId) {
      await jumpWindow(targetId)
    }
  } catch {
    ElMessage.error('加载窗口列表失败')
  }
}

async function jumpWindow(windowId: number | null | undefined) {
  if (!windowId) {
    return
  }

  try {
    await store.selectWindow(windowId)
    selectedWindowId.value = windowId
    activeSourceFileLabel.value = ''
    searchKeyword.value = ''
  } catch {
    ElMessage.error('切换窗口失败')
  }
}

async function handleWindowChange(value: string | number | null | undefined) {
  if (value == null || value === '') {
    return
  }
  await jumpWindow(Number(value))
}

async function handleTreeNodeClick(node: SliceWindowTreeNode) {
  if (node.type !== 'file' || !node.source_file_id) {
    return
  }

  try {
    store.setKeyword('')
    searchKeyword.value = ''
    activeSourceFileLabel.value = node.path || node.name
    await store.loadLogs(node.source_file_id, false)
  } catch {
    ElMessage.error('读取日志失败')
  }
}

async function handleSearch() {
  if (!store.selectedSourceFileId) {
    ElMessage.warning('请先选择日志文件')
    return
  }

  try {
    store.setKeyword(searchKeyword.value.trim())
    await store.loadLogs(store.selectedSourceFileId, false)
  } catch {
    ElMessage.error('窗口内搜索失败')
  }
}

async function resetSearch() {
  searchKeyword.value = ''
  await handleSearch()
}

async function loadMore() {
  try {
    await store.loadMore()
  } catch {
    ElMessage.error('加载更多日志失败')
  }
}

function goAnnotation() {
  void router.push({ name: 'annotation-workbench' })
}

function openAddAnnotation() {
  if (!selectedWindowId.value) {
    return
  }
  annotationEditingId.value = null
  annotationForm.label = 'normal'
  annotationForm.anomaly_type = null
  annotationForm.note = ''
  annotationForm.source_log_file_id = null
  annotationDialogVisible.value = true
}

function openEditAnnotation(row: Annotation) {
  annotationEditingId.value = row.id
  annotationForm.label = row.label
  annotationForm.anomaly_type = row.anomaly_type
  annotationForm.note = row.note || ''
  // Binding is fixed at creation; the selector is disabled while editing.
  annotationForm.source_log_file_id = row.source_log_file_id ?? null
  annotationDialogVisible.value = true
}

async function saveAnnotation() {
  if (annotationForm.label === 'abnormal' && !annotationForm.anomaly_type) {
    ElMessage.error('abnormal 标注必须选择故障类型')
    return
  }
  const payload = {
    label: annotationForm.label,
    anomaly_type: annotationForm.label === 'normal' ? null : annotationForm.anomaly_type,
    note: annotationForm.note.trim() || null,
    source_log_file_id: annotationForm.source_log_file_id
  }
  annotationSaving.value = true
  try {
    if (annotationEditingId.value === null) {
      if (!selectedWindowId.value) {
        return
      }
      await createAnnotation(selectedWindowId.value, payload)
    } else {
      await updateAnnotation(annotationEditingId.value, payload)
    }
    annotationDialogVisible.value = false
    await store.reloadAnnotations(selectedWindowId.value ?? undefined)
    ElMessage.success('标注已保存')
  } catch {
    ElMessage.error('保存标注失败')
  } finally {
    annotationSaving.value = false
  }
}

async function confirmDeleteAnnotation(row: Annotation) {
  try {
    await ElMessageBox.confirm('确定删除这条标注吗？', '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteAnnotation(row.id)
    await store.reloadAnnotations(selectedWindowId.value ?? undefined)
    ElMessage.success('标注已删除')
  } catch {
    ElMessage.error('删除标注失败')
  }
}

function openSubdivide() {
  subdivideVisible.value = true
}

async function confirmSubdivide() {
  if (!selectedWindowId.value) {
    return
  }
  subdividing.value = true
  try {
    const children = await subdivideSliceWindow(selectedWindowId.value, subdivideSeconds.value)
    ElMessage.success(`已细分为 ${children.length} 个子窗口`)
    subdivideVisible.value = false
    await store.fetchWindows(taskId.value)
    if (children[0]) {
      await jumpWindow(children[0].id)
    }
  } catch {
    ElMessage.error('细分窗口失败')
  } finally {
    subdividing.value = false
  }
}

onMounted(async () => {
  await loadWindows()
  void faultTypeStore.fetchAll().catch(() => undefined)
  // Arriving from the AI multi-error analysis "去细分" action: pre-fill the
  // suggested length and open the subdivide dialog on the targeted window.
  if (suggestedSubdivideSeconds.value != null && selectedWindowId.value != null) {
    subdivideSeconds.value = suggestedSubdivideSeconds.value
    subdivideVisible.value = true
  }
})
</script>

<style scoped>
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

.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 18px 20px;
  border-radius: 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
}

.summary-item strong {
  font-size: 28px;
  color: #0f172a;
}

.summary-item span {
  font-size: 13px;
  color: #64748b;
}

.navigation-row {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 18px;
}

.window-switcher {
  min-width: 320px;
}

.browser-panel {
  min-height: 540px;
}

.logs-toolbar {
  margin-bottom: 12px;
}

.file-chip {
  margin-bottom: 12px;
  color: #334155;
  font-size: 13px;
}

.load-more-row {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}

.load-more-row__done {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.subdivide-hint {
  margin: 8px 0 0;
  font-size: 12px;
  color: #94a3b8;
}

@media (max-width: 960px) {
  .summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 640px) {
  .summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
