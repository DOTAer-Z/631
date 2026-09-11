import http from './http'

import type {
  DashboardSummary,
  RecentAnnotationListResponse,
  RecentPackageListResponse,
  RecentSliceTaskListResponse
} from '@annotation/types/dashboard'

export async function getDashboardSummary() {
  const { data } = await http.get<DashboardSummary>('/dashboard/summary')
  return data
}

export async function getRecentPackages(limit = 10) {
  const { data } = await http.get<RecentPackageListResponse>('/dashboard/recent-packages', { params: { limit } })
  return data
}

export async function getRecentSliceTasks(limit = 10) {
  const { data } = await http.get<RecentSliceTaskListResponse>('/dashboard/recent-slice-tasks', { params: { limit } })
  return data
}

export async function getRecentAnnotations(limit = 10) {
  const { data } = await http.get<RecentAnnotationListResponse>('/dashboard/recent-annotations', { params: { limit } })
  return data
}
