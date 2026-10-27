import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const modulePath = new URL('../src/views/ModelManagement/trainingForm.js', import.meta.url)
const source = await readFile(modulePath, 'utf8')
const {
  buildSplitPreviewPayload,
  buildTaskPayload,
  canCancel,
  canEvaluate,
  canRetry,
  createTrainingForm,
  statusLabel,
  statusType,
  validLossMetrics,
  validateSplitRatios,
  validateTaskForm,
} = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`)

function validForm(overrides = {}) {
  return {
    ...createTrainingForm(),
    name: ' CPT smoke ',
    test_version_ids: [12, '34'],
    ...overrides,
  }
}

test('CPT payload normalizes Element Plus values to the exact request contract', () => {
  assert.deepEqual(buildTaskPayload(validForm({
    train_ratio: '0.8',
    validation_ratio: '0.1',
    test_ratio: '0.1',
    seed: '631',
    overrides: { learning_rate: '0.0002', num_train_epochs: '2' },
  })), {
    name: 'CPT smoke',
    task_type: 'cpt',
    test_version_ids: [12, 34],
    preset: 'quick',
    overrides: { learning_rate: 0.0002, num_train_epochs: 2 },
    train_ratio: 0.8,
    validation_ratio: 0.1,
    test_ratio: 0.1,
    seed: 631,
  })
})

test('base SFT omits the optional CPT Adapter field even when stale form state exists', () => {
  const payload = buildTaskPayload(validForm({
    task_type: 'sft',
    sft_source: 'base',
    cpt_adapter_artifact_id: 'stale-adapter-id',
  }))

  assert.equal(Object.hasOwn(payload, 'cpt_adapter_artifact_id'), false)
})

test('CPT-sourced SFT includes only the selected Adapter artifact ID', () => {
  const payload = buildTaskPayload(validForm({
    task_type: 'sft',
    sft_source: 'cpt',
    cpt_adapter_artifact_id: ' adapter-631 ',
  }))

  assert.equal(payload.cpt_adapter_artifact_id, 'adapter-631')
  assert.equal(Object.values(payload).includes('/training/adapters/adapter-631'), false)
})

test('split preview uses normalized integer IDs, ratios, and seed only', () => {
  assert.deepEqual(buildSplitPreviewPayload(validForm()), {
    test_version_ids: [12, 34],
    train_ratio: 0.8,
    validation_ratio: 0.1,
    test_ratio: 0.1,
    seed: 631,
  })
})

test('ratio validation rejects non-finite, negative, and imprecise totals', () => {
  assert.equal(validateSplitRatios(validForm()), '')
  assert.notEqual(validateSplitRatios(validForm({ train_ratio: 'Infinity' })), '')
  assert.notEqual(validateSplitRatios(validForm({ validation_ratio: -0.1 })), '')
  assert.notEqual(validateSplitRatios(validForm({ test_ratio: 0.100000002 })), '')
  assert.equal(validateSplitRatios(validForm({ test_ratio: 0.1000000001 })), '')
})

test('validation rejects duplicate or non-integer version IDs before a request', () => {
  assert.ok(validateTaskForm(validForm({ test_version_ids: [12, '12'] })).test_version_ids)
  assert.ok(validateTaskForm(validForm({ test_version_ids: [12, '12.5'] })).test_version_ids)
  assert.throws(
    () => buildTaskPayload(validForm({ test_version_ids: [12, '12'] })),
    /test version/i,
  )
})

const rejectedNumericValues = [
  true,
  false,
  null,
  undefined,
  '',
  '   ',
  Infinity,
  -Infinity,
  NaN,
  '12px',
  '0x0',
]

test('version IDs reject invalid source values and unsafe integers before payload creation', () => {
  for (const value of rejectedNumericValues) {
    const form = validForm({ test_version_ids: [value] })
    assert.ok(validateTaskForm(form).test_version_ids, `accepted version ID ${String(value)}`)
    assert.throws(() => buildSplitPreviewPayload(form), /test version/i)
    assert.throws(() => buildTaskPayload(form), /test version/i)
  }

  for (const value of [0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1, '9007199254740993']) {
    const form = validForm({ test_version_ids: [value] })
    assert.ok(validateTaskForm(form).test_version_ids, `accepted version ID ${String(value)}`)
    assert.throws(() => buildTaskPayload(form), /test version/i)
  }
})

test('version IDs preserve the largest safe integer exactly', () => {
  const numericPayload = buildTaskPayload(validForm({
    test_version_ids: [Number.MAX_SAFE_INTEGER],
  }))
  const stringPayload = buildSplitPreviewPayload(validForm({
    test_version_ids: [String(Number.MAX_SAFE_INTEGER)],
  }))

  assert.deepEqual(numericPayload.test_version_ids, [Number.MAX_SAFE_INTEGER])
  assert.deepEqual(stringPayload.test_version_ids, [Number.MAX_SAFE_INTEGER])
})

test('ratios reject invalid source values before payload creation', () => {
  for (const value of rejectedNumericValues) {
    const coerced = Number(value)
    const form = validForm({
      train_ratio: value,
      validation_ratio: Number.isFinite(coerced) && coerced >= 0 && coerced <= 1 ? 1 - coerced : 0.1,
      test_ratio: Number.isFinite(coerced) && coerced >= 0 && coerced <= 1 ? 0 : 0.1,
    })
    assert.notEqual(validateSplitRatios(form), '', `accepted ratio ${String(value)}`)
    assert.throws(() => buildSplitPreviewPayload(form), /ratio|\u6bd4\u4f8b/i)
    assert.throws(() => buildTaskPayload(form), /ratio|\u6bd4\u4f8b/i)
  }
})

test('seed rejects invalid source values and unsafe integers before payload creation', () => {
  for (const value of [...rejectedNumericValues, 1.5, Number.MAX_SAFE_INTEGER + 1, '9007199254740993']) {
    const form = validForm({ seed: value })
    assert.ok(validateTaskForm(form).seed, `accepted seed ${String(value)}`)
    assert.throws(() => buildSplitPreviewPayload(form), /seed|\u79cd\u5b50/i)
    assert.throws(() => buildTaskPayload(form), /seed|\u79cd\u5b50/i)
  }
})

test('overrides reject invalid source values before payload creation', () => {
  for (const value of rejectedNumericValues) {
    const form = validForm({ overrides: { lora_alpha: value } })
    assert.ok(validateTaskForm(form).overrides, `accepted override ${String(value)}`)
    assert.throws(() => buildTaskPayload(form), /override|\u53c2\u6570/i)
  }
})

test('validation enforces task type, preset, seed, and SFT source constraints', () => {
  assert.ok(validateTaskForm(validForm({ task_type: 'legacy' })).task_type)
  assert.ok(validateTaskForm(validForm({ preset: 'custom' })).preset)
  assert.ok(validateTaskForm(validForm({ seed: '631.5' })).seed)
  assert.ok(validateTaskForm(validForm({ name: 'x'.repeat(256) })).name)
  assert.ok(validateTaskForm(validForm({
    task_type: 'sft',
    sft_source: 'cpt',
    cpt_adapter_artifact_id: '',
  })).cpt_adapter_artifact_id)
  assert.ok(validateTaskForm(validForm({
    task_type: 'cpt',
    cpt_adapter_artifact_id: 'adapter-631',
  })).cpt_adapter_artifact_id)
  assert.ok(validateTaskForm(validForm({ overrides: { unsupported_parameter: 1 } })).overrides)
  assert.ok(validateTaskForm(validForm({ overrides: { unsupported_parameter: '' } })).overrides)
  assert.ok(validateTaskForm(validForm({ overrides: { logging_steps: '1.5' } })).overrides)
  assert.ok(validateTaskForm(validForm({ overrides: { lora_alpha: '64.5' } })).overrides)
  assert.ok(validateTaskForm(validForm({ overrides: { learning_rate: 'Infinity' } })).overrides)
  assert.ok(validateTaskForm(validForm({
    task_type: 'sft',
    sft_source: 'cpt',
    cpt_adapter_artifact_id: 'x'.repeat(37),
  })).cpt_adapter_artifact_id)
})

test('status labels, Element Plus types, and action guards share the backend state machine', () => {
  assert.equal(statusLabel('preparing_data'), '数据准备中')
  assert.equal(statusLabel('succeeded'), '成功')
  assert.equal(statusLabel('future_state'), 'future_state')
  assert.equal(statusType('succeeded'), 'success')
  assert.equal(statusType('failed'), 'danger')
  assert.equal(statusType('future_state'), 'info')

  for (const state of ['queued', 'preparing_data', 'training', 'evaluating']) {
    assert.equal(canCancel({ state }), true)
  }
  for (const state of ['cancelling', 'cancelled', 'succeeded', 'failed', 'interrupted']) {
    assert.equal(canCancel({ state }), false)
  }
  for (const state of ['failed', 'cancelled', 'interrupted']) {
    assert.equal(canRetry({ state }), true)
  }
  assert.equal(canRetry({ state: 'succeeded' }), false)
  assert.equal(canEvaluate({ state: 'succeeded', task_type: 'sft', job_kind: 'training' }), true)
  assert.equal(canEvaluate({ state: 'succeeded', task_type: 'cpt', job_kind: 'training' }), false)
  assert.equal(canEvaluate({ state: 'succeeded', task_type: 'sft', job_kind: 'evaluation' }), false)
  assert.equal(canEvaluate({ state: 'failed', task_type: 'sft', job_kind: 'training' }), false)
})

test('loss chart metrics keep only finite numeric loss values including zero', () => {
  const zero = { step: 2, loss: 0 }
  const positive = { step: 3, loss: 1.25 }

  assert.deepEqual(validLossMetrics([
    { step: 1, loss: null },
    zero,
    positive,
    { step: 4, loss: Infinity },
    { step: 5, loss: NaN },
    { step: 6, loss: '0.5' },
    { step: 7, loss: true },
    null,
  ]), [zero, positive])

  for (const value of [undefined, null, {}, 'invalid']) {
    assert.deepEqual(validLossMetrics(value), [])
  }
})
