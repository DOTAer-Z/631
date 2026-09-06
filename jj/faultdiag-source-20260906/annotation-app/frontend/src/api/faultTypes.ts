import http from './http'

import type {
  FaultType,
  FaultTypeDeleteResponse,
  FaultTypeListResponse,
  FaultTypePayload
} from '@annotation/types/faultType'

export async function listFaultTypes() {
  const { data } = await http.get<FaultTypeListResponse>('/fault-types')
  return data
}

export async function getFaultType(id: number | string) {
  const { data } = await http.get<FaultType>(`/fault-types/${id}`)
  return data
}

export async function createFaultType(payload: FaultTypePayload) {
  const { data } = await http.post<FaultType>('/fault-types', payload)
  return data
}

export async function updateFaultType(id: number | string, payload: FaultTypePayload) {
  const { data } = await http.patch<FaultType>(`/fault-types/${id}`, payload)
  return data
}

export async function deleteFaultType(id: number | string) {
  const { data } = await http.delete<FaultTypeDeleteResponse>(`/fault-types/${id}`)
  return data
}
