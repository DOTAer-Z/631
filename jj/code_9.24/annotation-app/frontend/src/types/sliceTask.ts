export type SliceTaskStatus = 'pending' | 'running' | 'success' | 'failed'

export interface SliceWindowSummary {
  id: number
  window_start_ts: number
  window_end_ts: number
  segment_title?: string | null
  line_count: number
  file_count: number
  cpu_count: number
  module_count: number
  created_at: string
}

export interface SliceTaskCreatePayload {
  name: string
  window_seconds?: number
}

export interface SliceTaskSummary {
  id: number
  package_id: number
  name: string
  window_seconds: number
  status: SliceTaskStatus
  total_files: number
  total_lines: number
  total_windows: number
  error_message: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  windows_count: number
}

export interface SliceTaskDetail extends SliceTaskSummary {
  windows: SliceWindowSummary[]
}

export interface SliceTaskListResponse {
  items: SliceTaskSummary[]
  total: number
}
