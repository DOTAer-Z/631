export type AnnotationLabel = 'normal' | 'abnormal'
export type AnnotationExportScope = 'current_filter' | 'all'
export type AnnotationExportMode = 'light' | 'full'

export interface AnnotationPayload {
  label: AnnotationLabel
  anomaly_type?: string | null
  note?: string | null
  source_log_file_id?: number | null
}

export interface Annotation {
  id: number
  slice_window_id: number
  source_log_file_id?: number | null
  binding_label?: string | null
  label: AnnotationLabel
  anomaly_type: string | null
  note: string | null
  created_at: string
  updated_at: string
}

export type AnnotationSortField = 'id' | 'label' | 'anomaly_type' | 'created_at' | 'updated_at' | 'window_start_ts'
export type AnnotationSortOrder = 'asc' | 'desc'

export interface AnnotationListItem {
  id: number
  package_id: number
  task_id: number
  window_id: number
  window_start_ts: number
  window_end_ts: number
  label: AnnotationLabel
  anomaly_type: string | null
  note: string | null
  created_at: string
  updated_at: string
}

export interface AnnotationListResponse {
  items: AnnotationListItem[]
  total: number
  page: number
  page_size: number
}

export interface AnnotationStatsResponse {
  total_annotations: number
  normal_count: number
  abnormal_count: number
}

export interface PendingAnnotationItem {
  package_id: number
  package_name?: string | null
  task_id: number
  task_name?: string | null
  window_id: number
  window_start_ts: number
  window_end_ts: number
  line_count: number
  file_count: number
  cpu_count: number
  module_count: number
}

export type PendingSortField = 'window_start_ts' | 'line_count'
export type PendingSortOrder = 'asc' | 'desc'

export interface PendingAnnotationFilters {
  package_id?: number
  task_id?: number
  start_ts?: number
  end_ts?: number
  min_line_count?: number
  max_line_count?: number
  keyword?: string
  sort_by?: PendingSortField
  sort_order?: PendingSortOrder
}

export interface PendingAnnotationListResponse {
  items: PendingAnnotationItem[]
  total: number
  page: number
  page_size: number
}

export interface AnnotationWorkbenchItem {
  annotation_id: number
  package_id: number
  task_id: number
  window_id: number
  window_start_ts: number
  window_end_ts: number
  label: AnnotationLabel
  anomaly_type: string | null
  note: string | null
  updated_at: string
}

export interface AnnotationWorkbenchResponse {
  items: AnnotationWorkbenchItem[]
  total: number
  page: number
  page_size: number
}

export interface AnnotationExportResponse {
  format: 'csv' | 'json'
  scope: AnnotationExportScope
  mode: AnnotationExportMode
  file_path: string
  file_name: string
  download_url: string
  item_count: number
}
