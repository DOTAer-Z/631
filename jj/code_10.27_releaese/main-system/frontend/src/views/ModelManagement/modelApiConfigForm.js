export const PROVIDER_OPTIONS = [
  { label: 'OpenAI Compatible', value: 'openai-compatible' },
  { label: 'DashScope', value: 'dashscope' },
  { label: 'vLLM', value: 'vllm' },
]

export function createEmptyApiConfigForm() {
  return {
    name: '',
    provider: 'openai-compatible',
    base_url: '',
    api_key: '',
    model: '',
    timeout_seconds: 60,
    max_output_tokens: 1024,
  }
}

export function configToForm(config) {
  return {
    name: config?.name || '',
    provider: config?.provider || 'openai-compatible',
    base_url: config?.base_url || '',
    api_key: '',
    model: config?.model || '',
    timeout_seconds: config?.timeout_seconds || 60,
    max_output_tokens: config?.max_output_tokens || 1024,
  }
}

export function buildApiConfigPayload(form, isEditing) {
  const payload = {
    name: form.name.trim(),
    provider: form.provider,
    base_url: form.base_url.trim().replace(/\/+$/, ''),
    model: form.model.trim(),
    timeout_seconds: Number(form.timeout_seconds),
    max_output_tokens: Number(form.max_output_tokens),
  }
  if (!isEditing || form.api_key.trim()) {
    payload.api_key = form.api_key.trim()
  }
  return payload
}

export function validateApiConfigForm(form, isEditing) {
  const errors = {}
  if (!form.name.trim()) errors.name = '请输入配置名称'
  if (!form.base_url.trim()) errors.base_url = '请输入 Base URL'
  if (!form.model.trim()) errors.model = '请输入模型名称'
  if (!isEditing && !form.api_key.trim()) errors.api_key = '新增配置时请输入 API Key'
  if (!Number.isInteger(Number(form.timeout_seconds)) || Number(form.timeout_seconds) < 1 || Number(form.timeout_seconds) > 600) {
    errors.timeout_seconds = '超时时间必须是 1 到 600 秒的整数'
  }
  if (!Number.isInteger(Number(form.max_output_tokens)) || Number(form.max_output_tokens) < 1 || Number(form.max_output_tokens) > 32768) {
    errors.max_output_tokens = '最大输出 Token 必须是 1 到 32768 的整数'
  }
  return errors
}

export function providerLabel(provider) {
  return PROVIDER_OPTIONS.find((item) => item.value === provider)?.label || provider
}
