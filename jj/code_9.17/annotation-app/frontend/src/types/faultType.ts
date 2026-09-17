export interface FaultType {
  id: number
  name: string
  description: string
  created_at: string
  updated_at: string
}

export interface FaultTypePayload {
  name: string
  description: string
}

export interface FaultTypeListResponse {
  items: FaultType[]
  total: number
}

export interface FaultTypeDeleteResponse {
  id: number
  name: string
  referenced_count: number
}
