import test from 'node:test'
import assert from 'node:assert/strict'

import {
  clampTaskProgress,
  isTerminalParseTask,
  parseTaskStageLabel,
  shouldKeepPreviousResult,
} from '../src/views/LogAnalysis/logParseTaskState.js'


test('parse task terminal states exclude cancelling', () => {
  assert.equal(isTerminalParseTask('cancelled'), true)
  assert.equal(isTerminalParseTask('succeeded'), true)
  assert.equal(isTerminalParseTask('failed'), true)
  assert.equal(isTerminalParseTask('cancelling'), false)
  assert.equal(isTerminalParseTask('running'), false)
})

test('progress is stable for invalid and out-of-range values', () => {
  assert.equal(clampTaskProgress(-4), 0)
  assert.equal(clampTaskProgress(37.6), 38)
  assert.equal(clampTaskProgress(140), 100)
  assert.equal(clampTaskProgress(undefined), 0)
})

test('stage labels use concise processing text', () => {
  assert.equal(parseTaskStageLabel('queued'), '排队中')
  assert.equal(parseTaskStageLabel('llm'), '模型解析')
  assert.equal(parseTaskStageLabel('persisting'), '保存结果')
  assert.equal(parseTaskStageLabel('unknown'), '处理中')
})

test('failed or cancelled reparse preserves an existing successful result', () => {
  assert.equal(shouldKeepPreviousResult({ hasAnalysis: true, taskState: 'failed' }), true)
  assert.equal(shouldKeepPreviousResult({ hasAnalysis: true, taskState: 'cancelled' }), true)
  assert.equal(shouldKeepPreviousResult({ hasAnalysis: false, taskState: 'failed' }), false)
})
