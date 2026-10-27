import http from './http'

import type {
  FaultTypeSuggestion,
  FaultTypeSuggestionAcceptPayload,
  FaultTypeSuggestionAcceptResponse,
  FaultTypeSuggestionListResponse,
  FaultTypeSuggestionStatus
} from '@annotation/types/faultTypeSuggestion'

export async function listFaultTypeSuggestions(params: {
  status?: FaultTypeSuggestionStatus
  page?: number
  page_size?: number
}) {
  const { data } = await http.get<FaultTypeSuggestionListResponse>('/fault-type-suggestions', { params })
  return data
}

export async function getFaultTypeSuggestion(id: number | string) {
  const { data } = await http.get<FaultTypeSuggestion>(`/fault-type-suggestions/${id}`)
  return data
}

export async function acceptFaultTypeSuggestion(
  id: number | string,
  payload: FaultTypeSuggestionAcceptPayload = {}
) {
  const { data } = await http.post<FaultTypeSuggestionAcceptResponse>(
    `/fault-type-suggestions/${id}/accept`,
    payload
  )
  return data
}

export async function rejectFaultTypeSuggestion(id: number | string) {
  const { data } = await http.post<FaultTypeSuggestion>(`/fault-type-suggestions/${id}/reject`)
  return data
}

export async function deleteFaultTypeSuggestion(id: number | string) {
  await http.delete(`/fault-type-suggestions/${id}`)
}
