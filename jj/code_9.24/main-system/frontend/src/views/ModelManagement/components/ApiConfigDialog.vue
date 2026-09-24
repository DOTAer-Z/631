<template>
  <el-dialog
    :model-value="modelValue"
    :title="isEditing ? '编辑 API 配置' : '新增 API 配置'"
    width="560px"
    destroy-on-close
    :before-close="beforeClose"
    @closed="resetForm"
  >
    <el-alert
      v-if="isEditing && config?.api_key_configured"
      title="API Key 已配置；留空表示保持原值。"
      type="info"
      :closable="false"
      show-icon
      class="key-alert"
    />
    <el-form label-width="130px" @submit.prevent="submit">
      <el-form-item label="配置名称" :error="errors.name">
        <el-input v-model="form.name" maxlength="80" placeholder="例如：Cluster vLLM" />
      </el-form-item>
      <el-form-item label="服务类型">
        <el-select v-model="form.provider" style="width: 100%;">
          <el-option
            v-for="option in PROVIDER_OPTIONS"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="Base URL" :error="errors.base_url">
        <el-input v-model="form.base_url" placeholder="http://vllm:8000/v1" />
      </el-form-item>
      <el-form-item label="API Key" :error="errors.api_key">
        <el-input
          v-model="form.api_key"
          type="password"
          autocomplete="new-password"
          :placeholder="isEditing ? '留空表示不修改' : '请输入 API Key'"
        />
      </el-form-item>
      <el-form-item label="模型名称" :error="errors.model">
        <el-input v-model="form.model" placeholder="qwen3.5-9b-sft" />
      </el-form-item>
      <el-form-item label="超时时间（秒）" :error="errors.timeout_seconds">
        <el-input-number v-model="form.timeout_seconds" :min="1" :max="600" :step="5" />
      </el-form-item>
      <el-form-item label="最大输出 Token" :error="errors.max_output_tokens">
        <el-input-number v-model="form.max_output_tokens" :min="1" :max="32768" :step="128" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import {
  PROVIDER_OPTIONS,
  buildApiConfigPayload,
  configToForm,
  createEmptyApiConfigForm,
  validateApiConfigForm,
} from '../modelApiConfigForm'

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  config: { type: Object, default: null },
  submitting: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'submit'])
const form = reactive(createEmptyApiConfigForm())
const errors = reactive({})
const isEditing = computed(() => Boolean(props.config?.id))

function replaceForm(value) {
  Object.assign(form, value)
  Object.keys(errors).forEach((key) => delete errors[key])
}

function resetForm() {
  replaceForm(createEmptyApiConfigForm())
}

watch(
  () => [props.modelValue, props.config],
  ([visible]) => {
    if (visible) replaceForm(props.config ? configToForm(props.config) : createEmptyApiConfigForm())
    else resetForm()
  },
  { immediate: true },
)

function close() {
  if (props.submitting) return
  emit('update:modelValue', false)
}

function beforeClose(done) {
  if (props.submitting) return
  emit('update:modelValue', false)
  done()
}

function submit() {
  const nextErrors = validateApiConfigForm(form, isEditing.value)
  Object.keys(errors).forEach((key) => delete errors[key])
  Object.assign(errors, nextErrors)
  if (Object.keys(nextErrors).length) return
  emit('submit', buildApiConfigPayload(form, isEditing.value))
}
</script>

<style scoped>
.key-alert {
  margin-bottom: 16px;
}
</style>
