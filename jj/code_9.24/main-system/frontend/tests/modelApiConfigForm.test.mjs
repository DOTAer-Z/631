import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const modulePath = new URL('../src/views/ModelManagement/modelApiConfigForm.js', import.meta.url)
const source = await readFile(modulePath, 'utf8')
const {
  buildApiConfigPayload,
  configToForm,
  createEmptyApiConfigForm,
  providerLabel,
  validateApiConfigForm,
} = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`)

test('empty form uses bounded runtime defaults', () => {
  assert.deepEqual(createEmptyApiConfigForm(), {
    name: '',
    provider: 'openai-compatible',
    base_url: '',
    api_key: '',
    model: '',
    timeout_seconds: 60,
    max_output_tokens: 1024,
  })
})

test('configToForm never copies a secret from a response', () => {
  assert.deepEqual(
    configToForm({
      name: 'Cluster vLLM',
      provider: 'vllm',
      base_url: 'http://vllm:8000/v1/',
      model: 'qwen3.5-9b-sft',
      timeout_seconds: 90,
      max_output_tokens: 2048,
      api_key: 'must-not-be-copied',
    }),
    {
      name: 'Cluster vLLM',
      provider: 'vllm',
      base_url: 'http://vllm:8000/v1/',
      api_key: '',
      model: 'qwen3.5-9b-sft',
      timeout_seconds: 90,
      max_output_tokens: 2048,
    },
  )
})

test('edit payload omits an empty API key and normalizes the URL', () => {
  assert.deepEqual(
    buildApiConfigPayload({
      name: ' Cluster vLLM ',
      provider: 'vllm',
      base_url: 'http://vllm:8000/v1/',
      api_key: '',
      model: ' qwen3.5-9b-sft ',
      timeout_seconds: 60,
      max_output_tokens: 1024,
    }, true),
    {
      name: 'Cluster vLLM',
      provider: 'vllm',
      base_url: 'http://vllm:8000/v1',
      model: 'qwen3.5-9b-sft',
      timeout_seconds: 60,
      max_output_tokens: 1024,
    },
  )
})

test('create payload includes a supplied API key', () => {
  const payload = buildApiConfigPayload({
    ...createEmptyApiConfigForm(),
    name: 'DashScope',
    provider: 'dashscope',
    base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    api_key: 'secret-value',
    model: 'qwen-plus',
  }, false)

  assert.equal(payload.api_key, 'secret-value')
})

test('validation reports exact field failures', () => {
  assert.deepEqual(validateApiConfigForm(createEmptyApiConfigForm(), false), {
    name: '请输入配置名称',
    base_url: '请输入 Base URL',
    model: '请输入模型名称',
    api_key: '新增配置时请输入 API Key',
  })
})

test('validation allows an empty API key while editing', () => {
  assert.deepEqual(validateApiConfigForm({
    ...createEmptyApiConfigForm(),
    name: 'Local vLLM',
    base_url: 'http://vllm:8000/v1',
    model: 'qwen',
  }, true), {})
})

test('provider labels are stable for table display', () => {
  assert.equal(providerLabel('openai-compatible'), 'OpenAI Compatible')
  assert.equal(providerLabel('dashscope'), 'DashScope')
  assert.equal(providerLabel('vllm'), 'vLLM')
  assert.equal(providerLabel('unknown'), 'unknown')
})
