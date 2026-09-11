import request from './index'

export const analyzeText = (data) => request.post('/log-analysis/analyze', data)
export const analyzeFile = (formData) => request.post('/log-analysis/analyze/file', formData, {
  headers: { 'Content-Type': 'multipart/form-data' },
})
export const vectorizeLog = (data) => request.post('/log-analysis/vectorize', data)
export const batchVectorizeLogs = (data) => request.post('/log-analysis/vectorize/batch', data)
export const listUnclassified = (params) => request.get('/log-analysis/unclassified', { params })
export const classifyLogs = (data) => request.post('/log-analysis/classify', data)

// 新增：日志上传相关 API
export const uploadLog = (data) => request.post('/log-analysis/upload', data)
export const uploadLogFile = (formData) => request.post('/log-analysis/upload/file', formData, {
  headers: { 'Content-Type': 'multipart/form-data' },
})

// 新增：日志列表相关 API
export const listUploadedLogs = (params) => request.get('/log-analysis/uploaded', { params })
export const listDatasetRuns = (params) => request.get('/log-analysis/logs', { params })
export const getDatasetRunDetail = (runId) => request.get(`/log-analysis/logs/${runId}`)
export const listDatasetRunEntries = (runId, params) => request.get(`/log-analysis/logs/${runId}/entries`, { params })
export const listDatasetRunWindows = (runId, params) => request.get(`/log-analysis/logs/${runId}/windows`, { params })

// ── 「日志列表」CRUD ─────────────────────────────────────────────────────────
export const updateDatasetRun = (runId, data) =>
  request.patch(`/log-analysis/logs/${runId}`, data)
export const deleteDatasetRun = (runId, params) =>
  request.delete(`/log-analysis/logs/${runId}`, { params })
export const batchDeleteDatasetRuns = (data) =>
  request.post('/log-analysis/logs/batch-delete', data)

export const listLogs = listDatasetRuns
export const getLogDetails = getDatasetRunDetail

// 新增：日志解析相关 API
export const parseLog = (logId) => request.post(`/log-analysis/parse/${logId}`)
export const batchParseLogs = (data) => request.post('/log-analysis/parse/batch', data)

export const createLogParseTask = (data) =>
  request.post('/log-analysis/parse-tasks', data)
export const getLogParseTask = (taskId) =>
  request.get(`/log-analysis/parse-tasks/${taskId}`)
export const cancelLogParseTask = (taskId) =>
  request.post(`/log-analysis/parse-tasks/${taskId}/cancel`)

// 新增：日志分析相关 API
export const analyzeLog = (data) => {
  if (data instanceof FormData) {
    // 文件上传方式
    return request.post('/log-analysis/analyze/file', data, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  }
  // 文本输入方式 / 默认
  return request.post('/log-analysis/analyze', data)
}

// 新增：解析已上传日志相关 API
export const analyzeExistingLog = (data) => request.post('/log-analysis/analyze/existing', data)
export const getAnalysisResult = (runId) => request.get(`/log-analysis/analysis/result/${runId}`)
export const batchAnalyzeLogs = (data) => request.post('/log-analysis/batch-analyze', data)

// DB5：已分析文件列表（verdict = normal | abnormal | 空=全部）
// 供「故障诊断」取异常、「预测预警」取正常。
export const listAnalysisResults = (params) =>
  request.get('/log-analysis/analysis/results', { params })

// 统一级联删除「分析记录」整条 run 线（联通 日志分析/故障诊断/预测预警；DB1 原始数据受保护）
export const deleteAnalysisRun = (runId) =>
  request.delete(`/log-analysis/analysis/${runId}`)
