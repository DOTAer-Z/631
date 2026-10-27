export type FaultTypeSuggestionStatus = 'pending' | 'accepted' | 'rejected'

export interface FaultTypeSuggestion {
  id: number
  slice_window_id: number
  suggested_name: string
  suggested_description: string
  reason: string | null
  model: string | null
  status: FaultTypeSuggestionStatus
  accepted_fault_type_id: number | null
  created_at: string
  updated_at: string
}

export interface FaultTypeSuggestionListResponse {
  items: FaultTypeSuggestion[]
  total: number
  page: number
  page_size: number
}

export interface FaultTypeSuggestionAcceptPayload {
  name?: string | null
  description?: string | null
}

export interface FaultTypeSuggestionAcceptResponse {
  suggestion: FaultTypeSuggestion
  fault_type: {
    id: number
    name: string
    description: string
    created_at: string
    updated_at: string
  }
}
