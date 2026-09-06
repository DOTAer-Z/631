import http from './http'

export interface HealthResponse {
  status: string
  version?: string
  database?: {
    connected: boolean
    error?: string | null
  }
  [key: string]: unknown
}

export async function getHealth() {
  const { data } = await http.get<HealthResponse>('/health')
  return data
}
