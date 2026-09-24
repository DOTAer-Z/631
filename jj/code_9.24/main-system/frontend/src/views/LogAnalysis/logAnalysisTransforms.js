export function buildRunListParams({ page, pageSize, runId, caseId, testName, faultStatus, dataCategory }) {
  const params = {
    page,
    page_size: pageSize,
  }

  // 数据源类型：结构化 / 非结构化 / 半结构化。后端按 run_id 前缀归类过滤。
  if (dataCategory) params.data_category = dataCategory
  if (runId) params.run_id = runId
  if (caseId) params.case_id = caseId
  if (testName) params.test_name = testName
  if (faultStatus) params.fault_status = faultStatus

  return params
}

export function formatFaultStatus(value) {
  if (value === 'fault') return '故障'
  if (value === 'normal') return '正常'
  return '未知'
}

export function getFaultStatusTagType(value) {
  if (value === 'fault') return 'danger'
  if (value === 'normal') return 'success'
  return 'info'
}

export function formatWindowPreview(text) {
  if (!text) return ''
  return text.length > 240 ? `${text.slice(0, 240)}...` : text
}
