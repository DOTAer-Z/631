export interface DashboardSummary {
  total_packages: number
  total_slice_tasks: number
  total_windows: number
  annotated_windows: number
  pending_windows: number
  normal_count: number
  abnormal_count: number
  // 按数据种类拆分（后端向后兼容返回，旧后端缺省视为 0）
  structured_packages?: number
  semi_structured_packages?: number
  unstructured_packages?: number
  structured_windows?: number
  semi_structured_windows?: number
  unstructured_windows?: number
}

export interface RecentPackageItem {
  id: number
  name: string
  import_status: string
  created_at: string
}

export interface RecentPackageListResponse {
  items: RecentPackageItem[]
}

export interface RecentSliceTaskItem {
  id: number
  package_id: number
  name: string
  status: string
  created_at: string
}

export interface RecentSliceTaskListResponse {
  items: RecentSliceTaskItem[]
}

export interface RecentAnnotationItem {
  id: number
  slice_window_id: number
  label: string
  anomaly_type: string | null
  updated_at: string
}

export interface RecentAnnotationListResponse {
  items: RecentAnnotationItem[]
}
