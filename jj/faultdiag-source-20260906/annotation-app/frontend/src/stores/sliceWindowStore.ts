import { defineStore } from 'pinia'
import { ref } from 'vue'

import { getSliceWindowFull, getSliceWindowLogs, listSliceTaskWindows } from '@annotation/api/sliceWindows'
import type {
  SliceWindowFullResponse,
  SliceWindowListItem,
  SliceWindowLogItem,
  SliceWindowNavigation,
  SliceWindowSummary,
  SliceWindowTreeNode
} from '@annotation/types/sliceWindow'

export const useSliceWindowStore = defineStore('slice-window', () => {
  const windows = ref<SliceWindowListItem[]>([])
  const selectedWindow = ref<SliceWindowSummary | null>(null)
  const tree = ref<SliceWindowTreeNode[]>([])
  const navigation = ref<SliceWindowNavigation | null>(null)
  const currentAnnotations = ref<SliceWindowFullResponse['annotations']>([])

  const selectedSourceFileId = ref<number | null>(null)
  const keyword = ref('')
  const logItems = ref<SliceWindowLogItem[]>([])
  const nextCursor = ref<string | null>(null)
  const hasMore = ref(false)

  const loadingWindows = ref(false)
  const loadingWindow = ref(false)
  const loadingLogs = ref(false)

  async function fetchWindows(taskId: number | string) {
    loadingWindows.value = true
    try {
      const data = await listSliceTaskWindows(taskId, {
        page: 1,
        page_size: 200,
        sort_by: 'window_start_ts',
        sort_order: 'asc'
      })
      windows.value = data.items
      return data
    } finally {
      loadingWindows.value = false
    }
  }

  async function selectWindow(windowId: number | string) {
    loadingWindow.value = true
    try {
      const data = await getSliceWindowFull(windowId)
      selectedWindow.value = data.window
      tree.value = data.tree.root
      navigation.value = data.navigation
      currentAnnotations.value = data.annotations
      selectedSourceFileId.value = null
      logItems.value = []
      nextCursor.value = null
      hasMore.value = false
      keyword.value = ''
      return data
    } finally {
      loadingWindow.value = false
    }
  }

  // Refresh only the annotation list for the currently selected window, without
  // resetting the browse state (file tree / selected file / logs). Used after
  // inline add/edit/delete of annotations in the browser view.
  async function reloadAnnotations(windowId?: number | string) {
    const targetId = windowId ?? selectedWindow.value?.id
    if (targetId == null) {
      return
    }
    const data = await getSliceWindowFull(targetId)
    currentAnnotations.value = data.annotations
  }

  async function loadLogs(sourceFileId: number, append = false) {
    if (!selectedWindow.value) {
      return null
    }

    loadingLogs.value = true
    try {
      const data = await getSliceWindowLogs(selectedWindow.value.id, {
        source_file_id: sourceFileId,
        cursor: append ? nextCursor.value : null,
        limit: 200,
        keyword: keyword.value.trim() || undefined
      })

      selectedSourceFileId.value = sourceFileId
      nextCursor.value = data.next_cursor
      hasMore.value = data.has_more
      logItems.value = append ? [...logItems.value, ...data.items] : data.items
      return data
    } finally {
      loadingLogs.value = false
    }
  }

  async function loadMore() {
    if (!selectedSourceFileId.value || !hasMore.value) {
      return null
    }
    return loadLogs(selectedSourceFileId.value, true)
  }

  function setKeyword(value: string) {
    keyword.value = value
  }

  return {
    windows,
    selectedWindow,
    tree,
    navigation,
    currentAnnotations,
    selectedSourceFileId,
    keyword,
    logItems,
    nextCursor,
    hasMore,
    loadingWindows,
    loadingWindow,
    loadingLogs,
    fetchWindows,
    selectWindow,
    reloadAnnotations,
    loadLogs,
    loadMore,
    setKeyword
  }
})
