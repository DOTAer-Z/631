import assert from 'node:assert/strict'
import test from 'node:test'
import { automaticGenerationSelection } from '../src/utils/eulerGeneration.mjs'

test('三类替代故障现在按实际类型直连能力检查，不生成 fallback 或模拟参数', () => {
  for (const code of ['F2301', 'F5203', 'F5402']) {
    const selected = automaticGenerationSelection(code, { [code]: [{ status: 'supported' }] }, {})
    assert.equal(selected.executed, code)
    assert.equal(selected.supported, true)
    assert.equal(selected.options, undefined)
  }
})

test('实际故障能力未知或不可用时不擅自提交', () => {
  assert.equal(automaticGenerationSelection('F5203', {}, {}).supported, false)
  assert.equal(automaticGenerationSelection('F5402', { F5402: [{ status: 'unsupported' }] }, {}).supported, false)
  assert.equal(automaticGenerationSelection('F2301', { F2301: [{ status: 'unknown' }] }, {}).supported, false)
})
