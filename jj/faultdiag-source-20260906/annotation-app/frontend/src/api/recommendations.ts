import http from './http'

import type {
  Recommendation,
  RecommendationBatchProgress,
  WindowMultiAnalysis
} from '@annotation/types/recommendation'

// LLM-backed endpoints can take far longer than the default 10s axios timeout;
// give the synchronous recommendation/analysis calls plenty of headroom.
const LLM_REQUEST_TIMEOUT_MS = 180000

export async function requestWindowRecommendation(windowId: number | string, forceRefresh = false) {
  const { data } = await http.post<Recommendation>(
    `/slice-windows/${windowId}/recommendation`,
    null,
    { params: { force_refresh: forceRefresh }, timeout: LLM_REQUEST_TIMEOUT_MS }
  )
  return data
}

export async function analyzeWindowMulti(windowId: number | string) {
  const { data } = await http.post<WindowMultiAnalysis>(
    `/slice-windows/${windowId}/recommendation/analyze`,
    null,
    { timeout: LLM_REQUEST_TIMEOUT_MS }
  )
  return data
}

export async function getWindowRecommendation(windowId: number | string) {
  const { data } = await http.get<Recommendation>(`/slice-windows/${windowId}/recommendation`)
  return data
}

// 读取已持久化的多错误分析（窗口重开时回显，无需重新点击分析）。无记录时后端返回 404。
export async function getStoredWindowAnalysis(windowId: number | string) {
  const { data } = await http.get<WindowMultiAnalysis>(
    `/slice-windows/${windowId}/recommendation/analysis`
  )
  return data
}

export async function startTaskRecommendationBatch(taskId: number | string) {
  const { data } = await http.post<RecommendationBatchProgress>(
    `/slice-tasks/${taskId}/recommendations/batch`
  )
  return data
}

export async function getTaskRecommendationBatchProgress(taskId: number | string) {
  const { data } = await http.get<RecommendationBatchProgress>(
    `/slice-tasks/${taskId}/recommendations/batch`
  )
  return data
}
