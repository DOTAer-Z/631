import http from './http'

import type {
  SliceTaskCreatePayload,
  SliceTaskDetail,
  SliceTaskListResponse
} from '@annotation/types/sliceTask'

export async function listSliceTasks(packageId: number | string) {
  const { data } = await http.get<SliceTaskListResponse>(`/packages/${packageId}/slice-tasks`)
  return data
}

export async function createSliceTask(packageId: number | string, payload: SliceTaskCreatePayload) {
  const { data } = await http.post<SliceTaskDetail>(`/packages/${packageId}/slice-tasks`, payload)
  return data
}

export async function getSliceTask(taskId: number | string) {
  const { data } = await http.get<SliceTaskDetail>(`/slice-tasks/${taskId}`)
  return data
}

export async function deleteSliceTask(taskId: number | string) {
  await http.delete(`/slice-tasks/${taskId}`)
}
