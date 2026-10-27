import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const viewPath = new URL('../src/views/LogAnalysis/LogParse.vue', import.meta.url)
const source = await readFile(viewPath, 'utf8')

test('LogParse uses asynchronous task APIs instead of synchronous parse requests', () => {
  assert.match(source, /createLogParseTask/)
  assert.match(source, /getLogParseTask/)
  assert.match(source, /cancelLogParseTask/)
  assert.doesNotMatch(source, /await\s+parseLog\s*\(/)
  assert.doesNotMatch(source, /await\s+batchParseLogs\s*\(/)
})

test('LogParse renders progress and a real cancelling state', () => {
  assert.match(source, /<el-progress/)
  assert.match(source, /取消解析/)
  assert.match(source, /正在取消/)
  assert.match(source, /activeTask\?\.stage\s*!==\s*['"]persisting['"]/)
})

test('LogParse uses serial polling, session restoration, and unmount cleanup', () => {
  assert.match(source, /setTimeout\s*\(/)
  assert.match(source, /sessionStorage\.setItem/)
  assert.match(source, /sessionStorage\.getItem/)
  assert.match(source, /onBeforeUnmount/)
  assert.match(source, /clearTimeout/)
})

test('LogParse keeps prior result access independent of the current task state', () => {
  assert.match(source, /parsedResults\.has\(String\(row\.id\)\)\s*\|\|\s*row\.has_analysis/)
  assert.match(source, /viewParsedResult/)
  assert.doesNotMatch(source, /parsedResults\.value\s*=\s*new Map\(\)/)
})
