<template>
  <div class="data-processing-page">
    <!-- 步骤条 -->
    <el-card shadow="never" class="steps-card">
      <el-steps :active="currentStep" finish-status="success" align-center>
        <el-step title="输入原始数据" description="粘贴文本或上传文件" />
        <el-step title="LLM 预处理" description="清洗文本，提取关键信息" />
        <el-step title="确认信息" description="选择故障类型，编辑摘要" />
        <el-step title="完成" description="保存到知识库" />
      </el-steps>
    </el-card>

    <!-- Step 0：输入原始数据 -->
    <el-card v-if="currentStep === 0" shadow="never" class="step-card">
      <template #header><span>Step 1：输入原始数据</span></template>
      <el-tabs v-model="inputMode">
        <el-tab-pane label="粘贴文本" name="text">
          <el-input
            v-model="rawText"
            type="textarea"
            :rows="10"
            placeholder="在此粘贴原始日志内容..."
          />
        </el-tab-pane>
        <el-tab-pane label="上传文件" name="file">
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".log,.txt,.csv,.json"
            :on-change="onFileChange"
            :on-remove="() => { selectedFile = null; rawText = '' }"
            drag
          >
            <el-icon size="48" color="#94a3b8"><UploadFilled /></el-icon>
            <div class="upload-text">拖拽或点击上传日志文件</div>
            <div class="upload-tip">.log .txt .csv .json</div>
          </el-upload>
        </el-tab-pane>
      </el-tabs>
      <div class="btn-row">
        <el-button type="primary" :loading="processing" @click="doPreprocess" size="large">
          <el-icon><MagicStick /></el-icon> LLM 预处理
        </el-button>
      </div>
    </el-card>

    <!-- Step 1：预处理结果 -->
    <el-card v-if="currentStep === 1" shadow="never" class="step-card">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>Step 2：预处理结果</span>
          <el-button size="small" @click="currentStep = 0">返回修改</el-button>
        </div>
      </template>
      <el-row :gutter="20">
        <el-col :span="12">
          <div class="section-title">原始文本</div>
          <el-scrollbar max-height="200px">
            <pre class="text-preview">{{ rawText }}</pre>
          </el-scrollbar>
        </el-col>
        <el-col :span="12">
          <div class="section-title">清洗后文本</div>
          <el-scrollbar max-height="200px">
            <pre class="text-preview cleaned">{{ preprocessResult.cleaned_text }}</pre>
          </el-scrollbar>
        </el-col>
      </el-row>
      <el-divider />
      <el-row :gutter="20">
        <el-col :span="12">
          <div class="section-title">提取关键词</div>
          <el-tag
            v-for="kw in preprocessResult.extracted_keywords"
            :key="kw"
            type="primary"
            style="margin: 3px;"
          >{{ kw }}</el-tag>
          <span v-if="!preprocessResult.extracted_keywords?.length" class="empty-hint">暂无</span>
        </el-col>
        <el-col :span="12">
          <div class="section-title">建议故障类型</div>
          <el-tag v-if="preprocessResult.suggested_fault_type" type="warning">
            {{ preprocessResult.suggested_fault_type }}
          </el-tag>
          <span v-else class="empty-hint">未识别</span>
        </el-col>
      </el-row>
      <div class="btn-row">
        <el-button type="primary" @click="currentStep = 2" size="large">
          下一步：确认信息
        </el-button>
      </div>
    </el-card>

    <!-- Step 2：确认信息 -->
    <el-card v-if="currentStep === 2" shadow="never" class="step-card">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>Step 3：确认信息</span>
          <el-button size="small" @click="currentStep = 1">返回预览</el-button>
        </div>
      </template>
      <el-form label-width="120px" style="max-width: 600px;">
        <el-form-item label="故障类型" required>
          <el-select v-model="selectedTypeId" placeholder="选择故障类型" style="width: 100%;">
            <el-option v-for="ft in faultTypes" :key="ft.id" :label="ft.name" :value="ft.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="摘要">
          <el-input v-model="editableSummary" type="textarea" :rows="3" placeholder="LLM 生成的摘要，可编辑..." />
        </el-form-item>
        <el-form-item label="文件名">
          <el-input v-model="editableFilename" placeholder="文件名" />
        </el-form-item>
        <el-form-item label="立即向量化">
          <el-switch v-model="autoVectorize" active-text="是（加入知识库）" inactive-text="否（仅保存）" />
        </el-form-item>
      </el-form>
      <div class="btn-row">
        <el-button type="primary" :loading="saving" @click="doSave" size="large">
          <el-icon><Check /></el-icon> 保存到知识库
        </el-button>
      </div>
    </el-card>

    <!-- Step 3：完成 -->
    <el-card v-if="currentStep === 3" shadow="never" class="step-card">
      <template #header><span>完成</span></template>
      <el-result icon="success" title="保存成功！">
        <template #sub-title>
          <el-descriptions :column="2" border size="small" style="max-width: 500px; margin: 0 auto;">
            <el-descriptions-item label="日志 ID">{{ saveResult?.id }}</el-descriptions-item>
            <el-descriptions-item label="文件名">{{ saveResult?.filename }}</el-descriptions-item>
            <el-descriptions-item label="故障类型">{{ saveResult?.fault_type_name }}</el-descriptions-item>
            <el-descriptions-item label="入库状态">
              <el-tag :type="saveResult?.is_indexed ? 'success' : 'info'" size="small">
                {{ saveResult?.is_indexed ? '已向量化入库' : '已保存（待入库）' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="摘要" :span="2">{{ saveResult?.summary || '-' }}</el-descriptions-item>
          </el-descriptions>
        </template>
        <template #extra>
          <el-button type="primary" @click="reset">处理下一条</el-button>
        </template>
      </el-result>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled, MagicStick, Check } from '@element-plus/icons-vue'
import { listFaultTypes } from '@/api/knowledgeBase'
import { preprocessText, preprocessFile, saveProcessedLog } from '@/api/dataProcessing'

const currentStep = ref(0)
const inputMode   = ref('text')
const rawText     = ref('')
const selectedFile = ref(null)
const processing  = ref(false)

const preprocessResult = ref(null)

const faultTypes       = ref([])
const selectedTypeId   = ref(null)
const editableSummary  = ref('')
const editableFilename = ref('手动输入')
const autoVectorize    = ref(true)
const saving           = ref(false)

const saveResult = ref(null)

function onFileChange(file) {
  selectedFile.value = file
  editableFilename.value = file.name
  const reader = new FileReader()
  reader.onload = (e) => (rawText.value = e.target.result)
  reader.readAsText(file.raw)
}

async function doPreprocess() {
  if (inputMode.value === 'text' && !rawText.value.trim()) {
    return ElMessage.warning('请输入日志内容')
  }
  if (inputMode.value === 'file' && !selectedFile.value) {
    return ElMessage.warning('请选择文件')
  }
  processing.value = true
  try {
    let result
    if (inputMode.value === 'text') {
      result = await preprocessText({ raw_text: rawText.value })
    } else {
      const fd = new FormData()
      fd.append('file', selectedFile.value.raw)
      result = await preprocessFile(fd)
    }
    preprocessResult.value = result
    editableSummary.value = result.summary || ''
    // 自动匹配建议故障类型
    if (result.suggested_fault_type) {
      const match = faultTypes.value.find(t => t.name === result.suggested_fault_type)
      if (match) selectedTypeId.value = match.id
    }
    currentStep.value = 1
  } finally {
    processing.value = false
  }
}

async function doSave() {
  if (!selectedTypeId.value) return ElMessage.warning('请选择故障类型')
  saving.value = true
  try {
    const cleanedText = preprocessResult.value?.cleaned_text || rawText.value
    saveResult.value = await saveProcessedLog({
      raw_text: rawText.value,
      cleaned_text: cleanedText,
      fault_type_id: selectedTypeId.value,
      summary: editableSummary.value,
      filename: editableFilename.value || '手动输入',
      auto_vectorize: autoVectorize.value,
    })
    currentStep.value = 3
  } finally {
    saving.value = false
  }
}

function reset() {
  currentStep.value = 0
  rawText.value = ''
  selectedFile.value = null
  preprocessResult.value = null
  selectedTypeId.value = null
  editableSummary.value = ''
  editableFilename.value = '手动输入'
  autoVectorize.value = true
  saveResult.value = null
}

onMounted(() => listFaultTypes().then(r => (faultTypes.value = r)))
</script>

<style scoped>
.data-processing-page { display: flex; flex-direction: column; gap: 20px; }
.steps-card {}
.step-card {}
.btn-row { margin-top: 20px; }
.upload-text { font-size: 14px; color: #64748b; margin-top: 8px; }
.upload-tip  { font-size: 12px; color: #94a3b8; margin-top: 4px; }
.section-title { font-size: 13px; font-weight: 600; color: #475569; margin-bottom: 8px; }
.text-preview { font-family: monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0; background: #f8fafc; padding: 8px; border-radius: 4px; }
.cleaned { background: #f0fdf4; }
.empty-hint { color: #94a3b8; font-size: 13px; }
</style>
