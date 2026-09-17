import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  createAnnotation,
  deleteAnnotation,
  exportAnnotations,
  getAnnotationStats,
  getAnnotationWorkbench,
  getPendingAnnotations,
  getWindowAnnotation,
  listAnnotations,
  updateAnnotation
} from '@annotation/api/annotations'
import { deleteSliceWindow } from '@annotation/api/sliceWindows'
import type {
  Annotation,
  AnnotationExportMode,
  AnnotationExportResponse,
  AnnotationExportScope,
  AnnotationListItem,
  AnnotationPayload,
  AnnotationSortField,
  AnnotationSortOrder,
  AnnotationStatsResponse,
  AnnotationWorkbenchItem,
  PendingAnnotationFilters,
  PendingAnnotationItem
} from '@annotation/types/annotation'

export interface AnnotationFilters {
  package_id?: number
  task_id?: number
  label?: string
  anomaly_type?: string
  start_ts?: number
  end_ts?: number
}

function isNotFoundError(error: unknown): boolean {
  const candidate = error as { response?: { status?: number } }
  return candidate.response?.status === 404
}

const EMPTY_STATS: AnnotationStatsResponse = {
  total_annotations: 0,
  normal_count: 0,
  abnormal_count: 0
}

export const useAnnotationStore = defineStore('annotation', () => {
  const currentAnnotation = ref<Annotation | null>(null)
  const loadingCurrent = ref(false)
  const saving = ref(false)
  const deleting = ref(false)

  const stats = ref<AnnotationStatsResponse>({ ...EMPTY_STATS })
  const pendingItems = ref<PendingAnnotationItem[]>([])
  const pendingTotal = ref(0)
  const pendingFilters = ref<PendingAnnotationFilters>({})
  const workbenchItems = ref<AnnotationWorkbenchItem[]>([])
  const workbenchTotal = ref(0)

  const items = ref<AnnotationListItem[]>([])
  const loadingList = ref(false)
  const page = ref(1)
  const pageSize = ref(20)
  const total = ref(0)
  const filters = ref<AnnotationFilters>({})
  const sortBy = ref<AnnotationSortField>('updated_at')
  const sortOrder = ref<AnnotationSortOrder>('desc')

  const exportLoading = ref(false)
  const lastExport = ref<AnnotationExportResponse | null>(null)

  async function fetchForWindow(windowId: number | string) {
    loadingCurrent.value = true
    try {
      const data = await getWindowAnnotation(windowId)
      currentAnnotation.value = data
      return data
    } catch (error) {
      if (isNotFoundError(error)) {
        currentAnnotation.value = null
        return null
      }
      throw error
    } finally {
      loadingCurrent.value = false
    }
  }

  async function createForWindow(windowId: number | string, payload: AnnotationPayload) {
    saving.value = true
    try {
      const data = await createAnnotation(windowId, payload)
      currentAnnotation.value = data
      return data
    } finally {
      saving.value = false
    }
  }

  async function updateCurrent(annotationId: number | string, payload: AnnotationPayload) {
    saving.value = true
    try {
      const data = await updateAnnotation(annotationId, payload)
      currentAnnotation.value = data
      return data
    } finally {
      saving.value = false
    }
  }

  async function deleteCurrent(annotationId: number | string) {
    deleting.value = true
    try {
      await deleteAnnotation(annotationId)
      currentAnnotation.value = null
    } finally {
      deleting.value = false
    }
  }

  async function fetchList(overrides?: Partial<AnnotationFilters> & { page?: number; page_size?: number; sort_by?: AnnotationSortField; sort_order?: AnnotationSortOrder }) {
    loadingList.value = true
    try {
      if (overrides?.sort_by) {
        sortBy.value = overrides.sort_by
      }
      if (overrides?.sort_order) {
        sortOrder.value = overrides.sort_order
      }
      const query = {
        ...filters.value,
        ...overrides,
        page: overrides?.page ?? page.value,
        page_size: overrides?.page_size ?? pageSize.value,
        sort_by: overrides?.sort_by ?? sortBy.value,
        sort_order: overrides?.sort_order ?? sortOrder.value
      }
      const data = await listAnnotations(query)
      items.value = data.items
      total.value = data.total
      page.value = data.page
      pageSize.value = data.page_size
      return data
    } finally {
      loadingList.value = false
    }
  }

  async function fetchStats(overrides?: Partial<AnnotationFilters>) {
    const data = await getAnnotationStats({
      ...filters.value,
      ...overrides
    })
    stats.value = data
    return data
  }

  async function fetchPending(overrides?: PendingAnnotationFilters & { page?: number; page_size?: number }) {
    const merged: PendingAnnotationFilters & { page?: number; page_size?: number } = {
      package_id: overrides?.package_id ?? filters.value.package_id ?? pendingFilters.value.package_id,
      task_id: overrides?.task_id ?? filters.value.task_id ?? pendingFilters.value.task_id,
      start_ts: overrides?.start_ts ?? filters.value.start_ts ?? pendingFilters.value.start_ts,
      end_ts: overrides?.end_ts ?? filters.value.end_ts ?? pendingFilters.value.end_ts,
      min_line_count: overrides?.min_line_count ?? pendingFilters.value.min_line_count,
      max_line_count: overrides?.max_line_count ?? pendingFilters.value.max_line_count,
      keyword: overrides?.keyword ?? pendingFilters.value.keyword,
      sort_by: overrides?.sort_by ?? pendingFilters.value.sort_by,
      sort_order: overrides?.sort_order ?? pendingFilters.value.sort_order,
      page: overrides?.page ?? 1,
      page_size: overrides?.page_size ?? 20
    }
    const data = await getPendingAnnotations(merged)
    pendingItems.value = data.items
    pendingTotal.value = data.total
    return data
  }

  function setPendingFilters(next: PendingAnnotationFilters) {
    pendingFilters.value = { ...next }
  }

  async function discardPendingWindow(windowId: number | string) {
    await deleteSliceWindow(windowId)
  }

  async function fetchWorkbench(overrides?: Partial<AnnotationFilters> & { page?: number; page_size?: number }) {
    const data = await getAnnotationWorkbench({
      ...filters.value,
      ...overrides,
      page: overrides?.page ?? 1,
      page_size: overrides?.page_size ?? 20
    })
    workbenchItems.value = data.items
    workbenchTotal.value = data.total
    return data
  }

  function setFilters(next: AnnotationFilters) {
    filters.value = { ...next }
    page.value = 1
  }

  async function refreshWorkbenchData() {
    await fetchStats()
    await fetchPending()
    await fetchWorkbench()
    await fetchList({ page: 1 })
  }

  async function exportWithFilters(
    format: 'csv' | 'json',
    scope: AnnotationExportScope = 'current_filter',
    mode: AnnotationExportMode = 'light'
  ) {
    exportLoading.value = true
    try {
      const data = await exportAnnotations({
        format,
        scope,
        mode,
        ...filters.value
      })
      lastExport.value = data
      return data
    } finally {
      exportLoading.value = false
    }
  }

  return {
    currentAnnotation,
    loadingCurrent,
    saving,
    deleting,
    stats,
    pendingItems,
    pendingTotal,
    pendingFilters,
    workbenchItems,
    workbenchTotal,
    items,
    loadingList,
    page,
    pageSize,
    total,
    sortBy,
    sortOrder,
    filters,
    exportLoading,
    lastExport,
    fetchForWindow,
    createForWindow,
    updateCurrent,
    deleteCurrent,
    fetchList,
    fetchStats,
    fetchPending,
    setPendingFilters,
    discardPendingWindow,
    fetchWorkbench,
    setFilters,
    refreshWorkbenchData,
    exportWithFilters
  }
})
