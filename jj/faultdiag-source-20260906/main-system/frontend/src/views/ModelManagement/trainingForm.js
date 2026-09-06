const TASK_TYPES = new Set(['cpt', 'sft'])
const PRESETS = new Set(['quick', 'formal'])
const SFT_SOURCES = new Set(['base', 'cpt'])
const CANCELLABLE_STATES = new Set(['queued', 'preparing_data', 'training', 'evaluating'])
const RETRYABLE_STATES = new Set(['failed', 'cancelled', 'interrupted'])
const INTEGER_OVERRIDE_NAMES = new Set([
  'max_seq_length',
  'lora_r',
  'lora_alpha',
  'per_device_train_batch_size',
  'gradient_accumulation_steps',
  'logging_steps',
  'eval_steps',
  'save_steps',
])
const NUMBER_OVERRIDE_NAMES = new Set([
  'learning_rate',
  'num_train_epochs',
  'lora_alpha',
  'lora_dropout',
  ...INTEGER_OVERRIDE_NAMES,
])
const DECIMAL_NUMBER_PATTERN = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/
const POSITIVE_INTEGER_PATTERN = /^\+?\d+$/

const STATUS_LABELS = {
  queued: '排队中',
  preparing_data: '数据准备中',
  training: '训练中',
  evaluating: '评估中',
  cancelling: '取消中',
  cancelled: '已取消',
  succeeded: '成功',
  failed: '失败',
  interrupted: '已中断',
}

const STATUS_TYPES = {
  queued: 'info',
  preparing_data: 'primary',
  training: 'primary',
  evaluating: 'warning',
  cancelling: 'warning',
  cancelled: 'info',
  succeeded: 'success',
  failed: 'danger',
  interrupted: 'warning',
}

export function createTrainingForm() {
  return {
    name: '',
    task_type: 'cpt',
    test_version_ids: [],
    preset: 'quick',
    overrides: {},
    train_ratio: 0.8,
    validation_ratio: 0.1,
    test_ratio: 0.1,
    seed: 631,
    sft_source: 'base',
    cpt_adapter_artifact_id: '',
  }
}

function normalizedDecimalNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : NaN
  if (typeof value !== 'string') return NaN
  const trimmed = value.trim()
  if (!trimmed || !DECIMAL_NUMBER_PATTERN.test(trimmed)) return NaN
  const normalized = Number(trimmed)
  return Number.isFinite(normalized) ? normalized : NaN
}

function normalizedVersionId(value) {
  if (typeof value === 'number') {
    return Number.isSafeInteger(value) && value > 0 ? value : NaN
  }
  if (typeof value !== 'string') return NaN
  const trimmed = value.trim()
  if (!POSITIVE_INTEGER_PATTERN.test(trimmed)) return NaN
  const normalized = Number(trimmed)
  return Number.isSafeInteger(normalized) && normalized > 0 ? normalized : NaN
}

function normalizedVersionIds(versionIds) {
  return Array.isArray(versionIds) ? versionIds.map(normalizedVersionId) : []
}

function normalizedRatios(form) {
  return [
    normalizedDecimalNumber(form.train_ratio),
    normalizedDecimalNumber(form.validation_ratio),
    normalizedDecimalNumber(form.test_ratio),
  ]
}

function normalizedOverrides(overrides) {
  return Object.fromEntries(
    Object.entries(overrides || {})
      .map(([name, value]) => [name, normalizedDecimalNumber(value)])
  )
}

function validateOverrides(overrides) {
  if (!overrides || typeof overrides !== 'object' || Array.isArray(overrides)) {
    return '高级参数格式无效'
  }
  if (Object.keys(overrides).some((name) => !NUMBER_OVERRIDE_NAMES.has(name))) {
    return '包含不支持的高级参数'
  }
  const values = normalizedOverrides(overrides)
  if (Object.entries(values).some(([name, value]) => (
    !Number.isFinite(value) || (INTEGER_OVERRIDE_NAMES.has(name) && !Number.isInteger(value))
  ))) {
    return '高级参数必须是有效数字'
  }
  if (values.max_seq_length !== undefined && (values.max_seq_length < 512 || values.max_seq_length > 8192)) {
    return '最大序列长度必须在 512 到 8192 之间'
  }
  if (values.lora_r !== undefined && (values.lora_r < 4 || values.lora_r > 256)) {
    return 'LoRA r 必须在 4 到 256 之间'
  }
  if (values.lora_dropout !== undefined && (values.lora_dropout < 0 || values.lora_dropout > 0.5)) {
    return 'LoRA dropout 必须在 0 到 0.5 之间'
  }
  if (values.per_device_train_batch_size !== undefined && (
    values.per_device_train_batch_size < 1 || values.per_device_train_batch_size > 8
  )) {
    return '单设备批大小必须在 1 到 8 之间'
  }
  for (const name of ['gradient_accumulation_steps', 'logging_steps', 'eval_steps', 'save_steps']) {
    if (values[name] !== undefined && values[name] <= 0) return `${name} 必须为正整数`
  }
  if (values.learning_rate !== undefined && (values.learning_rate <= 0 || values.learning_rate > 0.01)) {
    return '学习率必须在 0 到 0.01 之间'
  }
  if (values.num_train_epochs !== undefined && (
    values.num_train_epochs <= 0 || values.num_train_epochs > 100
  )) {
    return '训练轮数必须在 0 到 100 之间'
  }
  return ''
}

export function validateSplitRatios(form) {
  const ratios = normalizedRatios(form)
  if (ratios.some((ratio) => !Number.isFinite(ratio) || ratio < 0)) {
    return '划分比例必须是非负有限数'
  }
  if (Math.abs(ratios.reduce((total, ratio) => total + ratio, 0) - 1) > 1e-9) {
    return '划分比例之和必须为 1'
  }
  return ''
}

export function validateTaskForm(form) {
  const errors = {}
  const name = typeof form.name === 'string' ? form.name.trim() : ''
  const versionIds = normalizedVersionIds(form.test_version_ids)
  const adapterId = typeof form.cpt_adapter_artifact_id === 'string'
    ? form.cpt_adapter_artifact_id.trim()
    : ''

  if (!name) errors.name = '请输入任务名称'
  else if (name.length > 255) errors.name = '任务名称不能超过 255 个字符'
  if (!TASK_TYPES.has(form.task_type)) errors.task_type = '请选择有效的训练类型'
  if (!PRESETS.has(form.preset)) errors.preset = '请选择有效的训练预设'
  if (!versionIds.length) {
    errors.test_version_ids = '请至少选择一个 Test'
  } else if (versionIds.some((id) => !Number.isSafeInteger(id) || id <= 0)
    || new Set(versionIds).size !== versionIds.length) {
    errors.test_version_ids = 'Test version IDs 必须是不重复的正安全整数'
  }
  const ratioError = validateSplitRatios(form)
  if (ratioError) errors.ratios = ratioError
  if (!Number.isSafeInteger(normalizedDecimalNumber(form.seed))) errors.seed = '随机种子必须是安全整数'
  const overridesError = validateOverrides(form.overrides)
  if (overridesError) errors.overrides = overridesError
  if (form.task_type === 'cpt' && adapterId) {
    errors.cpt_adapter_artifact_id = 'CPT 任务不能选择 CPT Adapter'
  }
  if (form.task_type === 'sft') {
    if (!SFT_SOURCES.has(form.sft_source)) errors.sft_source = '请选择有效的 SFT 起点'
    if (form.sft_source === 'cpt' && !adapterId) {
      errors.cpt_adapter_artifact_id = '请选择 CPT Adapter'
    } else if (form.sft_source === 'cpt' && adapterId.length > 36) {
      errors.cpt_adapter_artifact_id = 'CPT Adapter ID 不能超过 36 个字符'
    }
  }
  return errors
}

function assertValidTaskForm(form) {
  const errors = validateTaskForm(form)
  const firstError = Object.values(errors)[0]
  if (firstError) throw new TypeError(firstError)
}

export function buildSplitPreviewPayload(form) {
  assertValidTaskForm(form)
  const [trainRatio, validationRatio, testRatio] = normalizedRatios(form)
  return {
    test_version_ids: normalizedVersionIds(form.test_version_ids),
    train_ratio: trainRatio,
    validation_ratio: validationRatio,
    test_ratio: testRatio,
    seed: normalizedDecimalNumber(form.seed),
  }
}

export function buildTaskPayload(form) {
  assertValidTaskForm(form)
  const payload = {
    name: form.name.trim(),
    task_type: form.task_type,
    test_version_ids: normalizedVersionIds(form.test_version_ids),
    preset: form.preset,
    overrides: normalizedOverrides(form.overrides),
    train_ratio: normalizedDecimalNumber(form.train_ratio),
    validation_ratio: normalizedDecimalNumber(form.validation_ratio),
    test_ratio: normalizedDecimalNumber(form.test_ratio),
    seed: normalizedDecimalNumber(form.seed),
  }
  if (form.task_type === 'sft' && form.sft_source === 'cpt') {
    payload.cpt_adapter_artifact_id = form.cpt_adapter_artifact_id.trim()
  }
  return payload
}

export function statusLabel(state) {
  return STATUS_LABELS[state] || state
}

export function statusType(state) {
  return STATUS_TYPES[state] || 'info'
}

export function validLossMetrics(metrics) {
  if (!Array.isArray(metrics)) return []
  return metrics.filter(item => (
    item && typeof item.loss === 'number' && Number.isFinite(item.loss)
  ))
}

export function canCancel(task) {
  return CANCELLABLE_STATES.has(task?.state)
}

export function canRetry(task) {
  return RETRYABLE_STATES.has(task?.state)
}

export function canEvaluate(task) {
  return task?.state === 'succeeded' && task.task_type === 'sft' && task.job_kind === 'training'
}
