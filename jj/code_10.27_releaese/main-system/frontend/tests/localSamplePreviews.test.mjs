import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const previews = JSON.parse(readFileSync(new URL('../src/data/eulerSamplePreviews.json', import.meta.url), 'utf8'))
const expectedSamples = [
  { sample_id: 'sample-f4512-high-load', title: '持续高系统负载', replay_fault_type: 'F4512' },
  { sample_id: 'sample-f4620-memory', title: '可用内存枯竭', replay_fault_type: 'F4620' },
  { sample_id: 'sample-f5313-tcp', title: 'TCP 连接失败', replay_fault_type: 'F5313' },
]

test('本地预览只包含三个受控 NuttX 样例', () => {
  assert.deepEqual(Object.keys(previews), expectedSamples.map(sample => sample.sample_id))
  for (const sample of expectedSamples) {
    const preview = previews[sample.sample_id]
    assert.equal(preview.sample_id, sample.sample_id)
    assert.equal(preview.title, sample.title)
    assert.equal(preview.replay_fault_type, sample.replay_fault_type)
    assert.equal(preview.display_source, 'NuttX')
    assert.equal(preview.evidence_mode, 'fixture')
    assert.equal(preview.is_real_euler_sample, false)
    assert.ok(preview.ground_truth && typeof preview.ground_truth === 'object')
    assert.ok(preview.log_summary.length > 0)
    for (const log of preview.log_summary) {
      assert.equal(typeof log.path, 'string')
      assert.equal(typeof log.line_count, 'number')
      assert.ok(Array.isArray(log.preview))
    }
  }
})
