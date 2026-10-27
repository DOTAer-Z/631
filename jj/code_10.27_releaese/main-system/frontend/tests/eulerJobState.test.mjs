import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = await readFile(new URL('../src/utils/eulerJobState.mjs', import.meta.url), 'utf8')
const { isActiveJob, isTerminalJob, canCancelJob, hasJobArtifact, jobStatusLabel, requestedFaultType, executedFaultType } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)

test('new preparation and finalization states remain active', () => {
  for (const status of ['queued', 'preparing', 'running', 'finalizing', 'cancel_requested']) {
    assert.equal(isActiveJob(status), true)
    assert.equal(isTerminalJob(status), false)
    assert.notEqual(jobStatusLabel(status), status)
  }
  assert.equal(canCancelJob('preparing'), true)
  assert.equal(canCancelJob('finalizing'), false)
})

test('fallback jobs keep requested and executed fault types separately', () => {
  const job = { requested_fault_type: 'F5201', executed_fault_type: 'F5203', job: { fault_type: 'F5203' } }
  assert.equal(requestedFaultType(job), 'F5201')
  assert.equal(executedFaultType(job), 'F5203')
})

test('missed acceleration target allows an available artifact independently of speedup', () => {
  assert.equal(isTerminalJob('acceleration_target_not_met'), true)
  assert.equal(hasJobArtifact({ status: 'acceleration_target_not_met', artifact_path: 'job.tar.gz', target_met: false, measured_speedup: 1.2 }), true)
  assert.equal(hasJobArtifact({ status: 'succeeded', artifact_path: 'job.tar.gz' }), true)
  assert.equal(hasJobArtifact({ status: 'succeeded' }), false)
  assert.equal(hasJobArtifact({ status: 'succeeded', artifact_path: 'job.tar.gz', artifact_error: 'SHA256 mismatch' }), false)
  for (const status of ['failed', 'cancelled', 'partial_failed', 'interrupted', 'capability_failed']) {
    assert.equal(hasJobArtifact({ status, artifact_path: 'partial.tar.gz' }), false)
  }
})
