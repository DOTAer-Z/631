<template>
  <div class="log-analysis-page">
    <el-tabs v-model="activeTab" type="border-card">
      <!-- Tab 1：日志解析与入库 -->
      <el-tab-pane label="日志解析与入库" name="analyze">
        <el-card shadow="never" class="input-card">
          <template #header><span>输入日志</span></template>
          <el-tabs v-model="inputMode">
            <el-tab-pane label="粘贴文本" name="text">
              <el-input
                v-model="logText"
                type="textarea"
                :rows="8"
                placeholder="在此粘贴日志内容..."
              />
            </el-tab-pane>
            <el-tab-pane label="上传文件" name="file">
              <el-upload
                ref="uploadRef"
                :auto-upload="false"
                :limit="10"
                accept=".log,.txt,.csv,.json"
                :on-change="onFileChange"
                :on-remove="onFileRemove"
                multiple
                drag
              >
                <el-icon size="48" color="#94a3b8"><UploadFilled /></el-icon>
                <div class="upload-text">拖拽或点击上传日志文件（支持多选）</div>
                <div class="upload-tip">.log .txt .csv .json</div>
              </el-upload>
            </el-tab-pane>
            <el-tab-pane label="接口导入" name="api">
              <el-form :model="apiImportForm" label-width="120px">
                <el-form-item label="接口URL">
                  <el-input v-model="apiImportForm.url" placeholder="请输入接口URL" />
                </el-form-item>
                <el-form-item label="请求方法">
                  <el-select v-model="apiImportForm.method" placeholder="选择请求方法">
                    <el-option label="GET" value="GET" />
                    <el-option label="POST" value="POST" />
                  </el-select>
                </el-form-item>
                <el-form-item label="请求参数">
                  <el-input
                    v-model="apiImportForm.params"
                    type="textarea"
                    :rows="4"
                    placeholder="请输入JSON格式的请求参数"
                  />
                </el-form-item>
                <el-form-item label="请求头">
                  <el-input
                    v-model="apiImportForm.headers"
                    type="textarea"
                    :rows="3"
                    placeholder="请输入JSON格式的请求头"
                  />
                </el-form-item>
              </el-form>
            </el-tab-pane>
          </el-tabs>
          <div class="submit-row">
            <el-button type="primary" :loading="analyzing" @click="doAnalyze" size="large">
              <el-icon><Search /></el-icon> 解析日志
            </el-button>
            <el-button size="large" :disabled="analyzing" @click="clearAll">清空</el-button>
          </div>
        </el-card>

        <!-- 上传结果反馈 -->
        <el-card v-if="uploadResult" shadow="never" class="result-card">
          <template #header><span>上传结果</span></template>
          <el-alert 
            :type="uploadResult.success ? 'success' : 'error'" 
            :closable="false" 
            show-icon 
          >
            <template #title>
              {{ uploadResult.message }}
            </template>
          </el-alert>
          <div v-if="uploadResult.files && uploadResult.files.length" style="margin-top: 16px;">
            <div class="info-title">上传文件列表</div>
            <el-table :data="uploadResult.files" size="small" style="margin-top: 8px;">
              <el-table-column prop="name" label="文件名" />
              <el-table-column prop="size" label="大小" width="100">
                <template #default="{ row }">
                  {{ (row.size / 1024).toFixed(2) }} KB
                </template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'success' ? 'success' : 'danger'" size="small">
                    {{ row.status === 'success' ? '成功' : '失败' }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-card>

        <!-- 解析结果 -->
        <template v-if="analyzeResult">

          <!-- ① 分析结论卡片（最顶部，主视觉） -->
          <el-card shadow="never" class="verdict-card" :class="analyzeResult.has_fault ? 'verdict-fault' : 'verdict-normal'">
            <div class="verdict-header">
              <el-tag
                :type="analyzeResult.has_fault ? 'danger' : 'success'"
                size="large"
                effect="dark"
                class="verdict-tag"
              >
                {{ analyzeResult.has_fault ? '故障日志' : '正常日志' }}
              </el-tag>
              <span class="verdict-confidence">
                置信度 <strong>{{ Math.round(analyzeResult.confidence * 100) }}%</strong>
              </span>
              <span class="verdict-next-action">
                建议下一步：<strong>{{ nextActionLabel }}</strong>
              </span>
            </div>
            <div class="verdict-summary">{{ analyzeResult.summary }}</div>
          </el-card>

          <!-- ② 判断依据卡片（仅在有 evidence 时显示） -->
          <el-card
            v-if="analyzeResult.evidence && analyzeResult.evidence.length"
            shadow="never"
            class="evidence-card"
          >
            <template #header><span>判断依据</span></template>
            <div
              v-for="(ev, idx) in analyzeResult.evidence.slice(0, 5)"
              :key="idx"
              class="evidence-item"
            >
              <el-tag :type="evidenceTagType(ev.source)" size="small" class="evidence-source">
                {{ evidenceSourceLabel(ev.source) }}
              </el-tag>
              <span class="evidence-detail">{{ ev.detail }}</span>
            </div>
          </el-card>

          <!-- ③ 统计详情卡片（下移，弱化） -->
          <el-card shadow="never" class="result-card">
            <template #header><span>解析详情 — {{ analyzeResult.filename }}</span></template>

            <!-- 日志级别统计 -->
            <el-row :gutter="20">
              <el-col :span="10">
                <div ref="levelChartRef" style="height: 200px;" />
              </el-col>
              <el-col :span="14">
                <el-descriptions :column="2" border size="small">
                  <el-descriptions-item label="总行数">{{ analyzeResult.line_count }}</el-descriptions-item>
                  <el-descriptions-item label="ERROR">
                    <el-tag type="danger" size="small">{{ analyzeResult.log_level_stats.error_count }}</el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="WARNING">
                    <el-tag type="warning" size="small">{{ analyzeResult.log_level_stats.warning_count }}</el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="INFO">
                    <el-tag type="info" size="small">{{ analyzeResult.log_level_stats.info_count }}</el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="DEBUG">
                    <el-tag size="small">{{ analyzeResult.log_level_stats.debug_count }}</el-tag>
                  </el-descriptions-item>
                </el-descriptions>

                <!-- 时间戳：隐藏（数据保留，不展示） -->

                <!-- 错误码：有内容才展示 -->
                <div v-if="analyzeResult.extracted_error_codes.length" class="info-section">
                  <div class="info-title">提取到的错误码</div>
                  <el-tag
                    v-for="code in analyzeResult.extracted_error_codes"
                    :key="code"
                    type="danger"
                    size="small"
                    style="margin: 2px;"
                  >{{ code }}</el-tag>
                </div>
              </el-col>
            </el-row>

            <!-- 堆栈信息：有内容才展示 -->
            <div v-if="analyzeResult.extracted_stack_traces.length" class="info-section">
              <div class="info-title">堆栈信息</div>
              <el-collapse>
                <el-collapse-item
                  v-for="(trace, i) in analyzeResult.extracted_stack_traces"
                  :key="i"
                  :title="`堆栈 ${i + 1}`"
                >
                  <pre class="stack-trace">{{ trace }}</pre>
                </el-collapse-item>
              </el-collapse>
            </div>

            <!-- 原文预览：折叠式，默认截断 1200 字符 -->
            <div class="info-section">
              <div class="info-title">原文预览</div>
              <el-scrollbar max-height="200px">
                <pre class="raw-content">{{ displayedRaw }}</pre>
              </el-scrollbar>
              <el-button
                v-if="analyzeResult.raw_content.length > 1200"
                link
                size="small"
                class="raw-toggle"
                @click="rawExpanded = !rawExpanded"
              >
                {{ rawExpanded ? '收起' : `展开全部（共 ${analyzeResult.raw_content.length} 字符）` }}
              </el-button>
            </div>
          </el-card>

          <!-- 向量化入库 -->
          <el-card shadow="never" class="vectorize-card">
            <template #header><span>向量化入库（不触发诊断）</span></template>
            <el-form inline>
              <el-form-item label="关联故障类型（可选）">
                <el-select v-model="faultTypeIdForVectorize" placeholder="选择故障类型（不选则标记为未分类）" clearable style="width: 260px;">
                  <el-option v-for="ft in faultTypes" :key="ft.id" :label="ft.name" :value="ft.id" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button type="success" :loading="vectorizing" @click="doVectorize">
                  <el-icon><Plus /></el-icon> 加入知识库
                </el-button>
              </el-form-item>
            </el-form>
            <el-alert v-if="vectorizeResult" type="success" :closable="false" show-icon style="margin-top: 12px;">
              <template #title>
                向量化成功！日志 ID: {{ vectorizeResult.log_entry_id }}，
                状态: {{ vectorizeResult.is_indexed ? '已入库' : '待入库' }}
                <span v-if="vectorizeResult.fault_type_name">，类型: {{ vectorizeResult.fault_type_name }}</span>
              </template>
            </el-alert>
          </el-card>

          <!-- 多文件批量入库（仅文件模式下有多个文件时显示） -->
          <el-card v-if="inputMode === 'file' && fileList.length > 1" shadow="never" class="vectorize-card">
            <template #header><span>批量向量化（{{ fileList.length }} 个文件）</span></template>
            <el-form inline>
              <el-form-item label="统一关联故障类型（可选）">
                <el-select v-model="batchFaultTypeId" placeholder="选择故障类型" clearable style="width: 260px;">
                  <el-option v-for="ft in faultTypes" :key="ft.id" :label="ft.name" :value="ft.id" />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button type="success" :loading="batchVectorizing" @click="doBatchVectorize">
                  <el-icon><Plus /></el-icon> 批量加入知识库
                </el-button>
              </el-form-item>
            </el-form>
            <el-alert v-if="batchResult" type="success" :closable="false" show-icon style="margin-top: 12px;">
              <template #title>
                批量完成：成功 {{ batchResult.success_count }} 条，失败 {{ batchResult.fail_count }} 条
              </template>
            </el-alert>
          </el-card>
        </template>
      </el-tab-pane>

      <!-- Tab 2：未分类日志管理 -->
      <el-tab-pane label="未分类日志管理" name="unclassified">
        <el-card shadow="never">
          <template #header>
            <div style="display: flex; align-items: center; gap: 12px;">
              <span>未分类日志（共 {{ uncTotal }} 条）</span>
              <el-select v-model="classifyTypeId" placeholder="选择故障类型" clearable size="small" style="width: 200px;">
                <el-option v-for="ft in faultTypes" :key="ft.id" :label="ft.name" :value="ft.id" />
              </el-select>
              <el-button
                type="primary"
                size="small"
                :loading="classifying"
                :disabled="!classifyTypeId || !selectedLogIds.length"
                @click="doBatchClassify"
              >
                批量分类（{{ selectedLogIds.length }} 条）
              </el-button>
              <el-button size="small" @click="loadUnclassified">刷新</el-button>
            </div>
          </template>
          <el-table
            :data="unclassifiedLogs"
            @selection-change="onSelectionChange"
            size="small"
          >
            <el-table-column type="selection" width="50" />
            <el-table-column prop="id" label="ID" width="60" />
            <el-table-column prop="filename" label="文件名" />
            <el-table-column prop="summary" label="摘要" show-overflow-tooltip />
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.is_indexed ? 'success' : 'info'" size="small">
                  {{ row.is_indexed ? '已入库' : '未入库' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="创建时间" width="160">
              <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="uncTotal > 20"
            v-model:current-page="uncPage"
            :page-size="20"
            :total="uncTotal"
            layout="prev, pager, next"
            @current-change="loadUnclassified"
            style="margin-top: 12px; justify-content: flex-end;"
          />
        </el-card>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled, Search, Plus } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import { listFaultTypes } from '@/api/knowledgeBase'
import { analyzeText, analyzeFile, analyzeApi, vectorizeLog, batchVectorizeLogs, listUnclassified, classifyLogs } from '@/api/logAnalysis'

// ── 公共状态 ──────────────────────────────────────────────────────────────────
const faultTypes = ref([])
const activeTab  = ref('analyze')

// ── Tab 1 状态 ────────────────────────────────────────────────────────────────
const inputMode  = ref('text')
const logText    = ref('')
const fileList   = ref([])
const analyzing  = ref(false)
const analyzeResult = ref(null)
const levelChartRef = ref(null)
let   levelChart = null

// ── 接口导入表单 ──────────────────────────────────────────────────────────────
const apiImportForm = ref({
  url: '',
  method: 'GET',
  params: '{}',
  headers: '{}'
})

const uploadResult = ref(null)
const rawExpanded = ref(false)

function clearAll() {
  logText.value = ''
  fileList.value = []
  analyzeResult.value = null
  vectorizeResult.value = null
  batchResult.value = null
  uploadResult.value = null
  rawExpanded.value = false
  uploadRef.value?.clearFiles()
}

const faultTypeIdForVectorize = ref(null)
const vectorizing = ref(false)
const vectorizeResult = ref(null)

const batchFaultTypeId = ref(null)
const batchVectorizing = ref(false)
const batchResult = ref(null)

// ── Tab 2 状态 ────────────────────────────────────────────────────────────────
const unclassifiedLogs = ref([])
const uncTotal = ref(0)
const uncPage  = ref(1)
const selectedLogIds = ref([])
const classifyTypeId = ref(null)
const classifying = ref(false)

// ── 新增：结论卡片计算属性 ─────────────────────────────────────────────────────
const nextActionLabel = computed(() => {
  if (!analyzeResult.value) return ''
  return analyzeResult.value.next_action === 'fault_location' ? '故障定位' : '预测预警'
})

const displayedRaw = computed(() => {
  if (!analyzeResult.value) return ''
  const content = analyzeResult.value.raw_content
  if (rawExpanded.value || content.length <= 1200) return content
  return content.slice(0, 1200) + '...'
})

// ── 新增：evidence 展示辅助 ────────────────────────────────────────────────────
function evidenceTagType(source) {
  const map = { level_stats: 'danger', keyword: 'warning', stack_trace: 'danger', window: 'info' }
  return map[source] || 'info'
}

function evidenceSourceLabel(source) {
  const map = { level_stats: '级别统计', keyword: '关键词', stack_trace: '堆栈', window: '时间窗口' }
  return map[source] || source
}

// ── 工具函数 ──────────────────────────────────────────────────────────────────
function formatDate(dt) {
  if (!dt) return '-'
  return new Date(dt).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
}

function onFileChange(file, files) {
  // 过滤无效文件格式
  const validFiles = files.filter(f => {
    const ext = f.name.split('.').pop().toLowerCase()
    const isValid = ['.log', '.txt', '.csv', '.json'].includes('.' + ext)
    if (!isValid) {
      ElMessage.warning(`文件 ${f.name} 格式无效，仅支持 .log、.txt、.csv、.json 格式`)
    }
    return isValid
  })
  fileList.value = validFiles
}

function onFileRemove(file, files) {
  fileList.value = files
}

function initLevelChart(stats) {
  if (!levelChart) levelChart = echarts.init(levelChartRef.value)
  levelChart.setOption({
    title: { text: '日志级别分布', left: 'center', top: 0, textStyle: { fontSize: 13 } },
    xAxis: { type: 'value' },
    yAxis: { type: 'category', data: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] },
    grid: { left: 70, right: 20, top: 30, bottom: 20 },
    series: [{
      type: 'bar',
      data: [
        { value: stats.debug_count,   itemStyle: { color: '#94a3b8' } },
        { value: stats.info_count,    itemStyle: { color: '#60a5fa' } },
        { value: stats.warning_count, itemStyle: { color: '#fbbf24' } },
        { value: stats.error_count,   itemStyle: { color: '#f87171' } },
      ],
      label: { show: true, position: 'right' },
    }],
  })
}

async function doAnalyze() {
  analyzeResult.value = null
  vectorizeResult.value = null
  batchResult.value = null
  rawExpanded.value = false
  analyzing.value = true
  try {
    if (inputMode.value === 'text') {
      if (!logText.value.trim()) return ElMessage.warning('请输入日志内容')
      analyzeResult.value = await analyzeText({ log_text: logText.value })
    } else if (inputMode.value === 'file') {
      if (!fileList.value.length) return ElMessage.warning('请选择文件')
      const fd = new FormData()
      fd.append('file', fileList.value[0].raw)
      analyzeResult.value = await analyzeFile(fd)
    } else if (inputMode.value === 'api') {
      if (!apiImportForm.value.url.trim()) return ElMessage.warning('请输入接口URL')
      try {
        JSON.parse(apiImportForm.value.params)
        JSON.parse(apiImportForm.value.headers)
      } catch (e) {
        return ElMessage.warning('请求参数或请求头格式错误，请输入有效的JSON格式')
      }
      analyzeResult.value = await analyzeApi({
        url: apiImportForm.value.url,
        method: apiImportForm.value.method,
        params: JSON.parse(apiImportForm.value.params),
        headers: JSON.parse(apiImportForm.value.headers)
      })
    }
    await nextTick()
    if (levelChartRef.value) initLevelChart(analyzeResult.value.log_level_stats)
  } catch (error) {
    ElMessage.error(`解析失败: ${error.message}`)
  } finally {
    analyzing.value = false
  }
}

async function doVectorize() {
  const text = inputMode.value === 'text' ? logText.value : analyzeResult.value?.raw_content
  if (!text) return ElMessage.warning('没有可向量化的内容')
  vectorizing.value = true
  try {
    vectorizeResult.value = await vectorizeLog({
      log_text: text,
      fault_type_id: faultTypeIdForVectorize.value || null,
      filename: fileList.value[0]?.name || '手动输入',
    })
    ElMessage.success('向量化完成，已加入知识库')
  } finally {
    vectorizing.value = false
  }
}

async function doBatchVectorize() {
  if (!fileList.value.length) return
  batchVectorizing.value = true
  try {
    const items = await Promise.all(fileList.value.map(async (f) => {
      return new Promise((resolve) => {
        const reader = new FileReader()
        reader.onload = (e) => resolve({
          log_text: e.target.result,
          fault_type_id: batchFaultTypeId.value || null,
          filename: f.name,
        })
        reader.readAsText(f.raw)
      })
    }))
    batchResult.value = await batchVectorizeLogs({ items })
    ElMessage.success(`批量完成：成功 ${batchResult.value.success_count} 条`)
  } finally {
    batchVectorizing.value = false
  }
}

function onSelectionChange(rows) {
  selectedLogIds.value = rows.map(r => r.id)
}

async function loadUnclassified() {
  const res = await listUnclassified({ page: uncPage.value, page_size: 20 })
  unclassifiedLogs.value = res.items
  uncTotal.value = res.total
}

async function doBatchClassify() {
  if (!classifyTypeId.value || !selectedLogIds.value.length) return
  classifying.value = true
  try {
    const res = await classifyLogs({ log_ids: selectedLogIds.value, fault_type_id: classifyTypeId.value })
    ElMessage.success(`已分类 ${res.updated_count} 条日志`)
    selectedLogIds.value = []
    await loadUnclassified()
  } finally {
    classifying.value = false
  }
}

onMounted(() => {
  listFaultTypes().then(r => (faultTypes.value = r))
  loadUnclassified()
})
</script>

<style scoped>
.log-analysis-page { display: flex; flex-direction: column; gap: 0; }
.input-card, .result-card, .vectorize-card { margin-bottom: 20px; }
.submit-row { margin-top: 16px; }
.upload-text { font-size: 14px; color: #64748b; margin-top: 8px; }
.upload-tip  { font-size: 12px; color: #94a3b8; margin-top: 4px; }
.info-section { margin-top: 16px; }
.info-title { font-size: 13px; font-weight: 600; color: #475569; margin-bottom: 6px; }
.stack-trace { font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0; }
.raw-content { font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0; }

/* ── 分析结论卡片 ──────────────────────────────────────────────────────────── */
.verdict-card {
  margin-bottom: 16px;
  border-left: 4px solid #e2e8f0;
}
.verdict-fault {
  border-left-color: #f87171;
  background-color: #fff5f5;
}
.verdict-normal {
  border-left-color: #34d399;
  background-color: #f0fdf4;
}
.verdict-header {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.verdict-tag {
  font-size: 14px;
  padding: 0 14px;
  height: 30px;
  line-height: 30px;
}
.verdict-confidence {
  font-size: 14px;
  color: #475569;
}
.verdict-confidence strong {
  color: #1e293b;
  font-size: 16px;
}
.verdict-next-action {
  font-size: 13px;
  color: #64748b;
  background: #f1f5f9;
  padding: 3px 10px;
  border-radius: 4px;
}
.verdict-next-action strong {
  color: #334155;
}
.verdict-summary {
  font-size: 14px;
  color: #334155;
  line-height: 1.7;
  padding-top: 4px;
  border-top: 1px solid rgba(0,0,0,0.06);
}

/* ── 判断依据卡片 ──────────────────────────────────────────────────────────── */
.evidence-card {
  margin-bottom: 16px;
}
.evidence-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid #f1f5f9;
}
.evidence-item:last-child {
  border-bottom: none;
}
.evidence-source {
  flex-shrink: 0;
  min-width: 60px;
  text-align: center;
}
.evidence-detail {
  font-size: 13px;
  color: #475569;
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 600px;
}

/* ── 原文折叠按钮 ────────────────────────────────────────────────────────── */
.raw-toggle {
  margin-top: 6px;
  color: #6366f1;
  font-size: 12px;
}
</style>
