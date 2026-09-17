import type { Annotation } from './annotation'

export interface SliceWindowListItem {
  window_id: number
  window_start_ts: number
  window_end_ts: number
  segment_title?: string | null
  record_count: number
  created_at: string
}

export interface SliceWindowListResponse {
  items: SliceWindowListItem[]
  total: number
  page: number
  page_size: number
}

export interface SliceWindowSummary {
  id: number
  slice_task_id: number
  window_start_ts: number
  window_end_ts: number
  segment_title?: string | null
  line_count: number
  file_count: number
  cpu_count: number
  module_count: number
  created_at: string
}

export interface SliceWindowTreeNode {
  name: string
  type: 'cpu' | 'module' | 'file'
  path: string | null
  source_file_id: number | null
  children: SliceWindowTreeNode[]
}

export interface SliceWindowTreeResponse {
  window_id: number
  root: SliceWindowTreeNode[]
}

export interface SliceWindowNavigation {
  current_window_id: number
  prev_window_id: number | null
  next_window_id: number | null
}

export interface SliceWindowFullResponse {
  window: SliceWindowSummary
  tree: SliceWindowTreeResponse
  annotations: Annotation[]
  navigation: SliceWindowNavigation
}

export interface SliceWindowLogItem {
  id: number
  source_file_id: number
  line_no: number
  timestamp: number | null
  content: string
}

export interface SliceWindowLogsResponse {
  items: SliceWindowLogItem[]
  next_cursor: string | null
  has_more: boolean
}
