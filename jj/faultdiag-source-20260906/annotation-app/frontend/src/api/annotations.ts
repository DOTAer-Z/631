import http, { resolveApiUrl } from './http'

import type {
  Annotation,
  AnnotationExportResponse,
  AnnotationListResponse,
  AnnotationPayload,
  AnnotationStatsResponse,
  AnnotationWorkbenchResponse,
  PendingAnnotationListResponse
} from '@annotation/types/annotation'

export async function createAnnotation(windowId: number | string, payload: AnnotationPayload) {
  const { data } = await http.post<Annotation>(`/slice-windows/${windowId}/annotation`, payload)
  return data
}

export async function getWindowAnnotation(windowId: number | string) {
  const { data } = await http.get<Annotation>(`/slice-windows/${windowId}/annotation`)
  return data
}

export async function updateAnnotation(annotationId: number | string, payload: AnnotationPayload) {
  const { data } = await http.patch<Annotation>(`/annotations/${annotationId}`, payload)
  return data
}

export async function deleteAnnotation(annotationId: number | string) {
  await http.delete(`/annotations/${annotationId}`)
}

export async function listAnnotations(params: {
  package_id?: number
  task_id?: number
  label?: string
  anomaly_type?: string
  start_ts?: number
  end_ts?: number
  page?: number
  page_size?: number
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}) {
  const { data } = await http.get<AnnotationListResponse>('/annotations', { params })
  return data
}

export async function exportAnnotations(params: {
  format: 'csv' | 'json'
  scope: 'current_filter' | 'all'
  mode?: 'light' | 'full'
  package_id?: number
  task_id?: number
  label?: string
  anomaly_type?: string
  start_ts?: number
  end_ts?: number
}) {
  const { data } = await http.get<AnnotationExportResponse>('/annotations/export', { params })
  return data
}

export function triggerAnnotationExportDownload(downloadUrl: string, fileName: string) {
  // 集成方案 A：后端返回的 download_url 可能是 "/api/v1/annotations/..."（新版绝对路径）
  // 或 "annotations/..."（相对路径）。直接用 resolveApiUrl 会丢掉 axios baseURL 的路径段
  // (/annotate-api/v1)，把请求打到主系统后端 → 404。这里统一规整成「相对 API 路径」后用
  // baseURL 重新拼接，保证独立部署 (/api/v1) 与集成部署 (/annotate-api/v1) 都路由到标注后端。
  let finalUrl: string
  if (/^https?:\/\//i.test(downloadUrl)) {
    finalUrl = downloadUrl
  } else {
    const relativePath = downloadUrl.replace(/^\/+/, '').replace(/^api\/v\d+\//, '')
    const apiBase = (http.defaults.baseURL ?? '/api/v1').replace(/\/+$/, '')
    finalUrl = resolveApiUrl(`${apiBase}/${relativePath}`)
  }

  const anchor = document.createElement('a')
  anchor.href = finalUrl
  anchor.download = fileName
  anchor.rel = 'noopener'
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
}

export async function getAnnotationStats(params: {
  package_id?: number
  task_id?: number
  label?: string
  anomaly_type?: string
  start_ts?: number
  end_ts?: number
}) {
  const { data } = await http.get<AnnotationStatsResponse>('/annotations/stats', { params })
  return data
}

export async function getPendingAnnotations(params: {
  package_id?: number
  task_id?: number
  start_ts?: number
  end_ts?: number
  min_line_count?: number
  max_line_count?: number
  keyword?: string
  sort_by?: 'window_start_ts' | 'line_count'
  sort_order?: 'asc' | 'desc'
  page?: number
  page_size?: number
}) {
  const { data } = await http.get<PendingAnnotationListResponse>('/annotations/pending', { params })
  return data
}

export async function getAnnotationWorkbench(params: {
  package_id?: number
  task_id?: number
  label?: string
  anomaly_type?: string
  start_ts?: number
  end_ts?: number
  page?: number
  page_size?: number
}) {
  const { data } = await http.get<AnnotationWorkbenchResponse>('/annotations/workbench', { params })
  return data
}
