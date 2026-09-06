<template>
  <div class="log-upload-page">
    <el-card shadow="never" class="input-card">
      <template #header><span>日志上传</span></template>
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
        <el-button type="primary" :loading="uploading" @click="handleUpload" size="large">
          <el-icon><Upload /></el-icon> 上传并解析
        </el-button>
        <el-button size="large" :disabled="uploading" @click="clearAll">清空</el-button>
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

    <!-- 已上传日志列表 -->
    <el-card v-if="uploadedLogs.length" shadow="never" class="result-card">
      <template #header>
        <div class="card-header">
          <span>已上传日志（{{ total }} 条）</span>
        </div>
      </template>
      
      <!-- 筛选条件 -->
      <div class="filter-section">
        <el-form :inline="true" :model="filterForm" class="filter-form">
          <el-form-item label="日志格式" >
            <el-select v-model="filterForm.log_format"  placeholder="选择日志格式" clearable>
              <el-option label="全部" value="" />
              <el-option label="plain" value="plain" />
              <el-option label="structured" value="structured" />
              <el-option label="syslog" value="syslog" />
            </el-select>
          </el-form-item>
          <el-form-item label="上传时间">
            <el-date-picker
              v-model="filterForm.date_range"
              type="daterange"
              range-separator="至"
              start-placeholder="开始日期"
              end-placeholder="结束日期"
              value-format="YYYY-MM-DD"
              clearable
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" @click="handleQuery">查询</el-button>
            <el-button @click="handleReset">重置</el-button>
            <el-button 
              type="success" 
              :disabled="selectedLogIds.length === 0" 
              @click="handleBatchAnalyze"
            >
              批量解析
            </el-button>
          </el-form-item>
        </el-form>
      </div>
      
      <!-- 日志列表 -->
      <el-table 
        :data="uploadedLogs" 
        size="small" 
        style="margin-top: 8px;"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="50" :selectable="checkSelectable" />
        <el-table-column prop="id" label="ID" />
        <el-table-column prop="filename" label="文件名" />
        <el-table-column prop="log_format" label="日志格式" width="100" />
        <el-table-column prop="line_count" label="行数" width="100" />
        <el-table-column prop="analyzed" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.analyzed ? 'success' : 'info'" size="small">
              {{ row.analyzed ? '已解析' : '未解析' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="上传时间" width="180">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button 
              v-if="row.analyzed" 
              type="primary" 
              size="small" 
              @click="viewAnalysis(row.id)"
            >
              查看
            </el-button>
            <el-button 
              v-else 
              type="success" 
              size="small" 
              @click="analyzeExistingLogItem(row.id)"
            >
              解析
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      
      <!-- 分页 -->
      <div class="pagination-section">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </div>
    </el-card>

    <!-- 查看解析结果弹窗 -->
    <el-dialog
      v-model="analysisDialogVisible"
      title="日志解析结果"
      width="90%"
      :close-on-click-modal="false"
    >
      <div class="analysis-container">
        <div class="analysis-section">
          <h3>解析结果</h3>
          <div class="analysis-result">
            <pre>{{ JSON.stringify(analysisData?.result || {}, null, 2) }}</pre>
          </div>
        </div>
        <div class="analysis-section">
          <h3>原始日志</h3>
          <div class="raw-log">
            <pre>{{ analysisData?.raw_log || '' }}</pre>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled, Upload } from '@element-plus/icons-vue'
import { uploadLog as uploadLogApi, uploadLogFile, uploadLogApi as uploadLogApiImport, listUploadedLogs, analyzeLog, analyzeExistingLog, getAnalysisResult, batchAnalyzeLogs } from '@/api/logAnalysis'

// 状态管理
const inputMode = ref('text')
const logText = ref('')
const fileList = ref([])
const uploading = ref(false)
const uploadResult = ref(null)
const uploadedLogs = ref([])
const uploadRef = ref(null)
const analysisDialogVisible = ref(false)
const analysisData = ref(null)

// 筛选表单
const filterForm = ref({
  log_format: '',
  date_range: []
})

// 分页
const pagination = ref({
  page: 1,
  page_size: 10
})

const total = ref(0)
const selectedLogIds = ref([])

// 接口导入表单
const apiImportForm = ref({
  url: '',
  method: 'GET',
  params: '{}',
  headers: '{}'
})

// 工具函数
function formatDate(dt) {
  if (!dt) return '-'
  // 无时区后缀的 ISO 字符串视为 UTC，避免被当本地时间解析晚 8 小时
  const s = String(dt)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  const iso = hasTz ? s : s + 'Z'
  return new Date(iso).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-')
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

// 上传日志（含自动解析）
async function handleUpload() {
  uploadResult.value = null
  uploading.value = true
  try {
    if (inputMode.value === 'text') {
      if (!logText.value.trim()) return ElMessage.warning('请输入日志内容')
      // Step 1: 上传到 MongoDB
      const upRes = await uploadLogApi({
        log_text: logText.value,
        filename: 'manual_input.log'
      })
      // Step 2: 自动解析
      try {
        await analyzeExistingLog({ run_id: upRes.run_id })
      } catch (e) {
        console.warn('自动解析失败：', e)
      }
      uploadResult.value = {
        success: true,
        message: '日志上传并解析完成',
        files: [{ name: 'manual_input.log', size: new Blob([logText.value]).size, status: 'success' }]
      }
    } else if (inputMode.value === 'file') {
      if (!fileList.value.length) return ElMessage.warning('请选择文件')
      const fd = new FormData()
      for (const file of fileList.value) {
        fd.append('files', file.raw)
      }
      // Step 1: 上传到 MongoDB
      const upRes = await uploadLogFile(fd)
      // Step 2: 自动解析每个文件
      const results = upRes.results || []
      let analyzedCount = 0
      for (const item of results) {
        if (!item.run_id) continue
        try {
          await analyzeExistingLog({ run_id: item.run_id })
          analyzedCount += 1
        } catch (e) {
          console.warn(`run ${item.run_id} 解析失败：`, e)
        }
      }
      uploadResult.value = {
        success: true,
        message: `已上传 ${fileList.value.length} 个文件，自动解析成功 ${analyzedCount} 个`,
        files: fileList.value.map(file => ({ name: file.name, size: file.size, status: 'success' }))
      }
    } else if (inputMode.value === 'api') {
      if (!apiImportForm.value.url.trim()) return ElMessage.warning('请输入接口URL')
      try {
        JSON.parse(apiImportForm.value.params)
        JSON.parse(apiImportForm.value.headers)
      } catch (e) {
        return ElMessage.warning('请求参数或请求头格式错误，请输入有效的JSON格式')
      }
      // Step 1: 上传到 MongoDB
      const upRes = await uploadLogApiImport({
        url: apiImportForm.value.url,
        method: apiImportForm.value.method,
        params: JSON.parse(apiImportForm.value.params),
        headers: JSON.parse(apiImportForm.value.headers)
      })
      // Step 2: 自动解析
      try {
        await analyzeExistingLog({ run_id: upRes.run_id })
      } catch (e) {
        console.warn('自动解析失败：', e)
      }
      uploadResult.value = {
        success: true,
        message: '接口导入并自动解析完成',
        files: [{ name: upRes.filename || 'api_import.log', size: upRes.file_size, status: 'success' }]
      }
    }
    ElMessage.success('上传并解析完成')
    await loadUploadedLogs()
  } catch (error) {
    ElMessage.error(`上传或解析失败: ${error.message}`)
  } finally {
    uploading.value = false
  }
}

// 仍保留独立的「日志解析」入口供已上传日志单独触发（被表格内的「解析」按钮使用）

// 清空
function clearAll() {
  logText.value = ''
  fileList.value = []
  uploadResult.value = null
  uploadRef.value?.clearFiles()
}

// 加载已上传日志
async function loadUploadedLogs() {
  try {
    const params = {
      page: pagination.value.page,
      page_size: pagination.value.page_size
    }
    
    // 添加筛选条件
    if (filterForm.value.log_format) {
      params.log_format = filterForm.value.log_format
    }
    
    if (filterForm.value.date_range && filterForm.value.date_range.length === 2) {
      params.start_date = filterForm.value.date_range[0]
      params.end_date = filterForm.value.date_range[1]
    }
    
    const result = await listUploadedLogs(params)
    uploadedLogs.value = result.items
    total.value = result.total
  } catch (error) {
    ElMessage.error(`加载日志列表失败: ${error.message}`)
  }
}

// 查询
function handleQuery() {
  pagination.value.page = 1
  loadUploadedLogs()
}

// 重置
function handleReset() {
  filterForm.value = {
    log_format: '',
    date_range: []
  }
  pagination.value.page = 1
  loadUploadedLogs()
}

// 分页大小变化
function handleSizeChange(size) {
  pagination.value.page_size = size
  pagination.value.page = 1
  loadUploadedLogs()
}

// 当前页变化
function handleCurrentChange(page) {
  pagination.value.page = page
  loadUploadedLogs()
}

// 选择变化
function handleSelectionChange(selection) {
  selectedLogIds.value = selection.map(row => row.id)
}

// 检查是否可选择
function checkSelectable(row) {
  return !row.analyzed
}

// 批量解析
async function handleBatchAnalyze() {
  if (selectedLogIds.value.length === 0) {
    ElMessage.warning('请选择要解析的日志')
    return
  }
  
  uploading.value = true
  try {
    const result = await batchAnalyzeLogs({ run_ids: selectedLogIds.value })
    ElMessage.success(result.message || `批量解析完成：成功 ${result.success_count} 条，失败 ${result.fail_count} 条`)
    // 重新加载日志列表
    await loadUploadedLogs()
    selectedLogIds.value = []
  } catch (error) {
    ElMessage.error(`批量解析失败: ${error.message}`)
  } finally {
    uploading.value = false
  }
}

// 解析已上传的日志
async function analyzeExistingLogItem(runId) {
  uploading.value = true
  try {
    const result = await analyzeExistingLog({ run_id: runId })
    ElMessage.success('日志解析成功')
    // 重新加载日志列表
    await loadUploadedLogs()
  } catch (error) {
    ElMessage.error(`解析失败: ${error.message}`)
  } finally {
    uploading.value = false
  }
}

// 查看解析结果
async function viewAnalysis(runId) {
  try {
    const result = await getAnalysisResult(runId)
    analysisData.value = result
    analysisDialogVisible.value = true
  } catch (error) {
    ElMessage.error(`获取解析结果失败: ${error.message}`)
  }
}

// 初始化
onMounted(() => {
  loadUploadedLogs()
})
</script>

<style scoped>
.log-upload-page { display: flex; flex-direction: column; gap: 0; }
.input-card, .result-card { margin-bottom: 20px; }
.submit-row { margin-top: 16px; }
.upload-text { font-size: 14px; color: #64748b; margin-top: 8px; }
.upload-tip  { font-size: 12px; color: #94a3b8; margin-top: 4px; }
.info-section { margin-top: 16px; }
.info-title { font-size: 13px; font-weight: 600; color: #475569; margin-bottom: 6px; }
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.filter-section {
  margin-bottom: 16px;
  padding: 16px;
  background: #f8f9fa;
  border-radius: 4px;
}
.filter-form {
  margin: 0;
}
.pagination-section {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
.analysis-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.analysis-section h3 {
  margin: 0 0 10px 0;
  font-size: 16px;
  font-weight: 600;
  color: #333;
}
.analysis-result, .raw-log {
  max-height: 40vh;
  overflow: auto;
  background: #f5f7fa;
  border-radius: 4px;
  padding: 16px;
}
.analysis-result pre, .raw-log pre {
  margin: 0;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>