export type ImportStatus = 'uploaded' | 'importing' | 'imported' | 'failed'

// 数据种类：非结构化=设计/维护/网络数据(报告 txt/pdf/md，按 `## ` 分段)；
// 半结构化=日志数据(按时间窗口切片)；结构化=监控/追踪数据(保留桶，暂无导入管线)。
export type DataKind = 'structured' | 'semi_structured' | 'unstructured'

export interface DatasetPackage {
  id: number
  name: string
  archive_type: string
  stored_path: string
  file_size: number
  sha256: string
  description: string | null
  import_status: ImportStatus
  import_error_message: string | null
  data_kind?: DataKind
  source_file_count: number
  source_line_count: number
  cpu_count: number
  module_count: number
  earliest_timestamp: number | null
  latest_timestamp: number | null
  created_at: string
  updated_at: string
  slice_task_count: number
  has_abnormal?: boolean
  import_task_id?: number
}

export interface PackageListParams {
  query?: string
  page?: number
  page_size?: number
}

export interface PackageListResponse {
  items: DatasetPackage[]
  total: number
  page: number
  page_size: number
}

export interface UpdatePackagePayload {
  description: string | null
}

export interface DeletePackageParams {
  package_name_confirm: string
}

export interface ImportTaskStatusResponse {
  id: number
  package_id: number
  status: 'pending' | 'running' | 'completed' | 'failed'
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
  updated_at: string
}

// 主系统（DB1）的数据导入列表项
export interface DataImportItem {
  import_id: string
  original_filename: string
  file_ext: string
  size_bytes: number
  status: string
  display_name?: string | null
  description?: string | null
  tags: string[]
  created_by?: string | null
  created_at: string
  updated_at: string
  ingest_status?: string | null
  ingest_error?: string | null
  ingested_at?: string | null
  ingested_run_count?: number
  ingested_entry_count?: number
  ingested_case_count?: number
  ingested_new_run_count?: number
  ingested_updated_run_count?: number
  training_complete_count?: number
  training_incomplete_count?: number
  training_duplicate_count?: number
  training_failed_count?: number
  training_parse_failed_count?: number
}

export interface DataImportListResponse {
  items: DataImportItem[]
  total: number
  page: number
  page_size: number
}

// 「入知识库」触发后，主系统返回的单条数据导入（含实时 ingest 状态与计数）
export type DataImportDetail = DataImportItem
