<template>
  <el-dialog v-model="visible" title="上传 Adapter" width="min(620px, 94vw)" :close-on-click-modal="false" :before-close="beforeClose" @closed="resetDialog">
    <el-form label-width="104px">
      <el-form-item label="阶段" :error="errors.adapter_stage">
        <el-segmented v-model="form.adapter_stage" :options="stageOptions" :disabled="submitting" />
      </el-form-item>
      <el-form-item label="基座模型">
        <el-select v-model="form.base_model_id" disabled>
          <el-option label="Qwen/Qwen3.5-9B" value="qwen-qwen3.5-9b" />
        </el-select>
      </el-form-item>
      <el-form-item label="名称" :error="errors.name">
        <el-input v-model="form.name" maxlength="255" show-word-limit :disabled="submitting" />
      </el-form-item>
      <el-form-item label="描述" :error="errors.description">
        <el-input v-model="form.description" type="textarea" :rows="3" maxlength="1000" show-word-limit :disabled="submitting" />
      </el-form-item>
      <el-form-item label="压缩包" :error="errors.file">
        <el-upload
          drag
          action="#"
          accept=".zip,.tar,.tar.gz"
          :auto-upload="false"
          :limit="1"
          :file-list="fileList"
          :disabled="submitting"
          :on-change="handleFileChange"
          :on-remove="handleFileRemove"
          :on-exceed="handleFileExceed"
        >
          <el-icon><UploadFilled /></el-icon>
        </el-upload>
      </el-form-item>
      <el-progress v-if="submitting || progress > 0" :percentage="progress" :status="progress === 100 ? 'success' : undefined" />
    </el-form>

    <template #footer>
      <el-button v-if="submitting" type="danger" plain @click="cancelUpload">取消上传</el-button>
      <el-button v-else @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" :disabled="submitting" @click="submitUpload">上传</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { importTrainingAdapter } from '@/api/modelTraining'
import {
  createUploadGeneration,
  isUploadCancellation,
  uploadProgressPercent,
  validateAdapterArchive,
} from '../adapterUploadState'

const props = defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'imported'])
const visible = computed({
  get: () => props.modelValue,
  set: (value) => {
    if (!value) cancelUpload()
    emit('update:modelValue', value)
  },
})
const form = reactive(createForm())
const errors = reactive({})
const fileList = ref([])
const selectedFile = ref(null)
const submitting = ref(false)
const progress = ref(0)
const uploadGeneration = createUploadGeneration()
let activeController = null
const stageOptions = [
  { label: 'CPT', value: 'cpt' },
  { label: 'SFT', value: 'sft' },
]

watch(() => props.modelValue, open => {
  if (!open) cancelUpload()
})

function createForm() {
  return {
    adapter_stage: 'cpt',
    base_model_id: 'qwen-qwen3.5-9b',
    name: '',
    description: '',
  }
}
function applyErrors(values) {
  Object.keys(errors).forEach(key => delete errors[key])
  Object.assign(errors, values)
}
function validateForm() {
  const next = {}
  const name = form.name.trim()
  if (!name) next.name = '请输入 Adapter 名称'
  if (!['cpt', 'sft'].includes(form.adapter_stage)) next.adapter_stage = '请选择有效阶段'
  if (form.description.length > 1000) next.description = '描述不能超过 1000 个字符'
  const fileError = validateAdapterArchive(selectedFile.value)
  if (fileError) next.file = fileError
  applyErrors(next)
  return Object.keys(next).length === 0
}
function handleFileChange(uploadFile) {
  selectedFile.value = uploadFile.raw || null
  fileList.value = uploadFile ? [uploadFile] : []
  const fileError = validateAdapterArchive(selectedFile.value)
  applyErrors({ ...errors, file: fileError || undefined })
  if (!fileError) delete errors.file
}
function handleFileRemove() {
  selectedFile.value = null
  fileList.value = []
  delete errors.file
}
function handleFileExceed() {
  applyErrors({ ...errors, file: '只能选择一个压缩包' })
}
function uploadErrorMessage(error) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (typeof detail?.message === 'string') return detail.message
  return 'Adapter 上传失败'
}
async function submitUpload() {
  if (submitting.value) return
  if (!validateForm()) return

  const generation = uploadGeneration.start()
  const controller = new AbortController()
  activeController = controller
  submitting.value = true
  progress.value = 0
  const formData = new FormData()
  formData.append('file', selectedFile.value)
  formData.append('name', form.name.trim())
  formData.append('adapter_stage', form.adapter_stage)
  formData.append('base_model_id', form.base_model_id)
  if (form.description) formData.append('description', form.description)
  const onUploadProgress = event => {
    if (uploadGeneration.isCurrent(generation)) progress.value = uploadProgressPercent(event)
  }

  try {
    const result = await importTrainingAdapter(formData, { signal: controller.signal, onUploadProgress })
    if (!uploadGeneration.isCurrent(generation)) return
    progress.value = 100
    ElMessage.success(result.deduplicated ? 'Adapter 已存在' : 'Adapter 上传成功')
    emit('imported', result.adapter)
    visible.value = false
  } catch (error) {
    if (!uploadGeneration.isCurrent(generation) || isUploadCancellation(error)) return
    ElMessage.error(uploadErrorMessage(error))
  } finally {
    if (uploadGeneration.isCurrent(generation)) {
      submitting.value = false
      activeController = null
    }
  }
}
function cancelUpload() {
  activeController?.abort()
  activeController = null
  uploadGeneration.invalidate()
  submitting.value = false
  progress.value = 0
}
function beforeClose(done) {
  cancelUpload()
  done()
}
function resetDialog() {
  cancelUpload()
  Object.assign(form, createForm())
  applyErrors({})
  fileList.value = []
  selectedFile.value = null
}

onBeforeUnmount(cancelUpload)
</script>

<style scoped>
:deep(.el-select), :deep(.el-upload), :deep(.el-upload-dragger) { width: 100%; }
:deep(.el-upload-dragger) { padding: 24px 12px; }
</style>
