import http from './http'

import type {
  SliceWindowFullResponse,
  SliceWindowSummary,
  SliceWindowListResponse,
  SliceWindowLogsResponse,
  SliceWindowTreeResponse
} from '@annotation/types/sliceWindow'

export async function listSliceTaskWindows(
  taskId: number | string,
  params?: {
    page?: number
    page_size?: number
    sort_by?: 'window_start_ts' | 'created_at'
    sort_order?: 'asc' | 'desc'
    start_ts?: number
    end_ts?: number
  }
) {
  const { data } = await http.get<SliceWindowListResponse>(`/slice-tasks/${taskId}/windows`, { params })
  return data
}

export async function getSliceWindow(windowId: number | string) {
  const { data } = await http.get<SliceWindowSummary>(`/slice-windows/${windowId}`)
  return data
}

export async function getSliceWindowTree(windowId: number | string) {
  const { data } = await http.get<SliceWindowTreeResponse>(`/slice-windows/${windowId}/tree`)
  return data
}

export async function getSliceWindowFull(windowId: number | string) {
  const { data } = await http.get<SliceWindowFullResponse>(`/slice-windows/${windowId}/full`)
  return data
}

export async function getSliceWindowLogs(
  windowId: number | string,
  params: { source_file_id: number; cursor?: string | null; limit?: number; keyword?: string }
) {
  const { data } = await http.get<SliceWindowLogsResponse>(`/slice-windows/${windowId}/logs`, { params })
  return data
}

export async function deleteSliceWindow(windowId: number | string) {
  await http.delete(`/slice-windows/${windowId}`)
}

export async function subdivideSliceWindow(windowId: number | string, windowSeconds: number) {
  const { data } = await http.post<SliceWindowSummary[]>(`/slice-windows/${windowId}/subdivide`, {
    window_seconds: windowSeconds
  })
  return data
}

export interface SliceWindowPushResult {
  run_id: string
  message: string
  [key: string]: unknown
}

/** 把一个标注窗口（含标注）推送到主系统，落成一条 run + log_entries，供主系统文件选择/诊断使用。 */
export async function pushSliceWindow(windowId: number | string) {
  const { data } = await http.post<SliceWindowPushResult>(`/slice-windows/${windowId}/push`)
  return data
}
