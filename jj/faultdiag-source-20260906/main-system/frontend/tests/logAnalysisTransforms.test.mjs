import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const transformModulePath = new URL('../src/views/LogAnalysis/logAnalysisTransforms.js', import.meta.url)
const logAnalysisViewPath = new URL('../src/views/LogAnalysis/LogAnalysis.vue', import.meta.url)
const logAnalysisDetailViewPath = new URL('../src/views/LogAnalysis/LogAnalysisDetail.vue', import.meta.url)
const transformModuleSource = await readFile(transformModulePath, 'utf8')
const logAnalysisViewSource = await readFile(logAnalysisViewPath, 'utf8')
const logAnalysisDetailViewSource = await readFile(logAnalysisDetailViewPath, 'utf8')
const {
  buildRunListParams,
  formatFaultStatus,
  formatWindowPreview,
} = await import(`data:text/javascript;charset=utf-8,${encodeURIComponent(transformModuleSource)}`)

test('buildRunListParams keeps page values and strips empty filters', () => {
  assert.deepEqual(
    buildRunListParams({
      page: 2,
      pageSize: 50,
      runId: '',
      caseId: 'nuttx_NuttX_Test_1030',
      testName: 'Test_1030',
      faultStatus: 'fault',
    }),
    {
      page: 2,
      page_size: 50,
      system_id: 'nuttx',
      case_id: 'nuttx_NuttX_Test_1030',
      test_name: 'Test_1030',
      fault_status: 'fault',
    },
  )
})

test('formatFaultStatus maps boolean-like values into Chinese labels', () => {
  assert.equal(formatFaultStatus('fault'), '故障')
  assert.equal(formatFaultStatus('normal'), '正常')
  assert.equal(formatFaultStatus(''), '未知')
  assert.equal(formatFaultStatus(undefined), '未知')
})

test('formatWindowPreview truncates long text', () => {
  const preview = formatWindowPreview('X'.repeat(260))

  assert.equal(preview.length, 243)
  assert.equal(preview, `${'X'.repeat(240)}...`)
})

test('LogAnalysis view keeps an in-page quick analysis entry point alongside the run browser', () => {
  assert.match(logAnalysisViewSource, /import\s+\{[^}]*analyzeLog[^}]*\}\s+from\s+['"]@\/api\/logAnalysis['"]/)
  assert.match(logAnalysisViewSource, /analysis-pane/)
  assert.match(logAnalysisViewSource, /quickAnalyze|handleQuickAnalyze/)
})

test('LogAnalysis view guards run-list and quick-analysis requests against stale responses', () => {
  assert.match(logAnalysisViewSource, /let\s+loadRunsRequestId\s*=\s*0/)
  assert.match(logAnalysisViewSource, /const\s+requestId\s*=\s*\+\+loadRunsRequestId/)
  assert.match(logAnalysisViewSource, /if\s*\(\s*requestId\s*!==\s*loadRunsRequestId\s*\)\s*return/)

  assert.match(logAnalysisViewSource, /let\s+quickAnalyzeRequestId\s*=\s*0/)
  assert.match(logAnalysisViewSource, /const\s+requestId\s*=\s*\+\+quickAnalyzeRequestId/)
  assert.match(logAnalysisViewSource, /if\s*\(\s*requestId\s*!==\s*quickAnalyzeRequestId\s*\)\s*return/)
})

test('LogAnalysisDetail watches route params and reloads state when runId changes', () => {
  assert.match(
    logAnalysisDetailViewSource,
    /watch\s*\(\s*\(\)\s*=>\s*route\.params\.runId[\s\S]*detail\.value\s*=\s*null[\s\S]*entryRows\.value\s*=\s*\[\][\s\S]*windowRows\.value\s*=\s*\[\][\s\S]*loadDetail\(\)[\s\S]*loadEntries\(\)[\s\S]*immediate:\s*true/s,
  )
})

test('LogAnalysisDetail guards detail, entries, and windows requests against stale responses', () => {
  assert.match(logAnalysisDetailViewSource, /let\s+detailRequestId\s*=\s*0/)
  assert.match(logAnalysisDetailViewSource, /let\s+entriesRequestId\s*=\s*0/)
  assert.match(logAnalysisDetailViewSource, /let\s+windowsRequestId\s*=\s*0/)

  assert.match(logAnalysisDetailViewSource, /const\s+requestId\s*=\s*\+\+detailRequestId/)
  assert.match(logAnalysisDetailViewSource, /const\s+requestId\s*=\s*\+\+entriesRequestId/)
  assert.match(logAnalysisDetailViewSource, /const\s+requestId\s*=\s*\+\+windowsRequestId/)

  assert.match(logAnalysisDetailViewSource, /if\s*\(\s*requestId\s*!==\s*detailRequestId\s*\)\s*return/)
  assert.match(logAnalysisDetailViewSource, /if\s*\(\s*requestId\s*!==\s*entriesRequestId\s*\)\s*return/)
  assert.match(logAnalysisDetailViewSource, /if\s*\(\s*requestId\s*!==\s*windowsRequestId\s*\)\s*return/)
})
