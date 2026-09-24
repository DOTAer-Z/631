export type RecommendationStatus = 'pending' | 'success' | 'failed'

export interface Recommendation {
  window_id: number
  status: RecommendationStatus
  recommended_label: 'normal' | 'abnormal' | null
  recommended_anomaly_type: string | null
  reason: string | null
  model: string | null
  error_message: string | null
  pending_suggestion_id: number | null
  created_at: string
  updated_at: string
}

export interface RecommendationBatchProgress {
  task_id: number
  total_windows: number
  success_count: number
  failed_count: number
  pending_count: number
  not_started_count: number
}

export interface WindowMultiAnalysisFileItem {
  source_log_file_id: number
  logical_path: string
  label: 'normal' | 'abnormal'
  anomaly_type: string | null
  reason: string | null
}

export interface WindowMultiAnalysis {
  window_id: number
  status: 'success' | 'failed'
  multiple_faults: boolean
  suggest_subdivide: boolean
  suggested_window_seconds: number | null
  files: WindowMultiAnalysisFileItem[]
  model: string | null
  error_message: string | null
}
