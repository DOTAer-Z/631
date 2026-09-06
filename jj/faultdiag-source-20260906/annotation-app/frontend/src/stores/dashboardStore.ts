import { defineStore } from 'pinia'
import { ref } from 'vue'

import { getDashboardSummary, getRecentAnnotations, getRecentPackages, getRecentSliceTasks } from '@annotation/api/dashboard'
import type { DashboardSummary, RecentAnnotationItem, RecentPackageItem, RecentSliceTaskItem } from '@annotation/types/dashboard'

const DEFAULT_SUMMARY: DashboardSummary = {
  total_packages: 0,
  total_slice_tasks: 0,
  total_windows: 0,
  annotated_windows: 0,
  pending_windows: 0,
  normal_count: 0,
  abnormal_count: 0,
  structured_packages: 0,
  semi_structured_packages: 0,
  unstructured_packages: 0,
  structured_windows: 0,
  semi_structured_windows: 0,
  unstructured_windows: 0
}

export const useDashboardStore = defineStore('dashboard', () => {
  const summary = ref<DashboardSummary>({ ...DEFAULT_SUMMARY })
  const recentPackages = ref<RecentPackageItem[]>([])
  const recentSliceTasks = ref<RecentSliceTaskItem[]>([])
  const recentAnnotations = ref<RecentAnnotationItem[]>([])

  const loading = ref(false)
  const refreshing = ref(false)

  async function fetchSummary() {
    summary.value = await getDashboardSummary()
    return summary.value
  }

  async function fetchRecentPackages(limit = 8) {
    const data = await getRecentPackages(limit)
    recentPackages.value = data.items
    return data
  }

  async function fetchRecentSliceTasks(limit = 8) {
    const data = await getRecentSliceTasks(limit)
    recentSliceTasks.value = data.items
    return data
  }

  async function fetchRecentAnnotations(limit = 8) {
    const data = await getRecentAnnotations(limit)
    recentAnnotations.value = data.items
    return data
  }

  async function refreshDashboard() {
    refreshing.value = true
    try {
      await fetchSummary()
      await fetchRecentPackages()
      await fetchRecentSliceTasks()
      await fetchRecentAnnotations()
    } finally {
      refreshing.value = false
    }
  }

  async function initialize() {
    loading.value = true
    try {
      await refreshDashboard()
    } finally {
      loading.value = false
    }
  }

  return {
    summary,
    recentPackages,
    recentSliceTasks,
    recentAnnotations,
    loading,
    refreshing,
    fetchSummary,
    fetchRecentPackages,
    fetchRecentSliceTasks,
    fetchRecentAnnotations,
    refreshDashboard,
    initialize
  }
})
