import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { createPackage, createPackageFromDataImport, createPackageFromFolder, deletePackage, getImportTask, getPackage, listPackages, updatePackage } from '@annotation/api/packages'
import type { DatasetPackage, ImportTaskStatusResponse } from '@annotation/types/package'

const DEFAULT_PAGE = 1
const DEFAULT_PAGE_SIZE = 10
const IMPORT_TASK_STORAGE_KEY = 'log-annotation-platform.import-task-map'

function loadImportTaskMap(): Record<number, number> {
  if (typeof window === 'undefined') {
    return {}
  }

  try {
    const raw = window.sessionStorage.getItem(IMPORT_TASK_STORAGE_KEY)
    if (!raw) {
      return {}
    }

    const parsed = JSON.parse(raw) as Record<string, number>
    return Object.fromEntries(
      Object.entries(parsed)
        .map(([packageId, taskId]) => [Number(packageId), Number(taskId)])
        .filter(([packageId, taskId]) => Number.isFinite(packageId) && Number.isFinite(taskId))
    )
  } catch {
    return {}
  }
}

function persistImportTaskMap(value: Record<number, number>) {
  if (typeof window === 'undefined') {
    return
  }

  window.sessionStorage.setItem(IMPORT_TASK_STORAGE_KEY, JSON.stringify(value))
}

export const usePackageStore = defineStore('package', () => {
  const packages = ref<DatasetPackage[]>([])
  const currentPackage = ref<DatasetPackage | null>(null)
  const importTaskIds = ref<Record<number, number>>(loadImportTaskMap())
  const importTaskStatuses = ref<Record<number, ImportTaskStatusResponse>>({})

  const loading = ref(false)
  const detailLoading = ref(false)
  const uploadLoading = ref(false)
  const deleting = ref(false)
  const updatingDescription = ref(false)
  const importTaskLoading = ref(false)

  const query = ref('')
  const page = ref(DEFAULT_PAGE)
  const pageSize = ref(DEFAULT_PAGE_SIZE)
  const total = ref(0)

  const hasData = computed(() => packages.value.length > 0)

  async function fetchPackages() {
    loading.value = true
    try {
      const data = await listPackages({
        query: query.value || undefined,
        page: page.value,
        page_size: pageSize.value
      })

      packages.value = data.items
      total.value = data.total
      page.value = data.page
      pageSize.value = data.page_size
    } finally {
      loading.value = false
    }
  }

  async function fetchPackage(id: number | string) {
    detailLoading.value = true
    try {
      currentPackage.value = await getPackage(id)
      if (currentPackage.value.import_task_id) {
        rememberImportTask(currentPackage.value.id, currentPackage.value.import_task_id)
      }
      if (['imported', 'failed'].includes(currentPackage.value.import_status)) {
        clearImportTask(currentPackage.value.id)
      }
      return currentPackage.value
    } finally {
      detailLoading.value = false
    }
  }

  async function uploadPackage(formData: FormData) {
    uploadLoading.value = true
    try {
      const created = await createPackage(formData)
      if (created.import_task_id) {
        rememberImportTask(created.id, created.import_task_id)
      }
      await fetchPackages()
      return created
    } finally {
      uploadLoading.value = false
    }
  }

  async function uploadPackageFolder(formData: FormData) {
    uploadLoading.value = true
    try {
      const created = await createPackageFromFolder(formData)
      if (created.import_task_id) {
        rememberImportTask(created.id, created.import_task_id)
      }
      await fetchPackages()
      return created
    } finally {
      uploadLoading.value = false
    }
  }

  async function importPackageFromDataImport(payload: {
    import_id: string | number
    filename?: string
    name?: string
    description?: string | null
  }) {
    uploadLoading.value = true
    try {
      const created = await createPackageFromDataImport(payload)
      if (created.import_task_id) {
        rememberImportTask(created.id, created.import_task_id)
      }
      await fetchPackages()
      return created
    } finally {
      uploadLoading.value = false
    }
  }

  async function removePackage(id: number | string, packageNameConfirm: string) {
    deleting.value = true
    try {
      await deletePackage(id, {
        package_name_confirm: packageNameConfirm
      })
      clearImportTask(Number(id))
      if (currentPackage.value?.id === Number(id)) {
        currentPackage.value = null
      }
      await fetchPackages()
    } finally {
      deleting.value = false
    }
  }

  async function saveDescription(id: number | string, description: string) {
    updatingDescription.value = true
    try {
      const updated = await updatePackage(id, description)
      currentPackage.value = updated
      const index = packages.value.findIndex((item) => item.id === updated.id)
      if (index >= 0) {
        packages.value[index] = updated
      }
      return updated
    } finally {
      updatingDescription.value = false
    }
  }

  function rememberImportTask(packageId: number, taskId: number) {
    importTaskIds.value = {
      ...importTaskIds.value,
      [packageId]: taskId
    }
    persistImportTaskMap(importTaskIds.value)
  }

  function clearImportTask(packageId: number) {
    const nextTaskIds = { ...importTaskIds.value }
    const nextStatuses = { ...importTaskStatuses.value }
    delete nextTaskIds[packageId]
    delete nextStatuses[packageId]
    importTaskIds.value = nextTaskIds
    importTaskStatuses.value = nextStatuses
    persistImportTaskMap(importTaskIds.value)
  }

  function getKnownImportTaskId(packageId: number) {
    return importTaskIds.value[packageId]
  }

  function getImportTaskStatusForPackage(packageId: number) {
    return importTaskStatuses.value[packageId] ?? null
  }

  async function refreshImportProgress(packageId: number, taskId?: number) {
    const resolvedTaskId = taskId ?? getKnownImportTaskId(packageId)
    if (!resolvedTaskId) {
      return null
    }

    importTaskLoading.value = true
    try {
      const task = await getImportTask(resolvedTaskId)
      importTaskStatuses.value = {
        ...importTaskStatuses.value,
        [packageId]: task
      }
      const pkg = await fetchPackage(packageId)
      if (task.status === 'completed' || task.status === 'failed' || ['imported', 'failed'].includes(pkg.import_status)) {
        clearImportTask(packageId)
      }
      return task
    } finally {
      importTaskLoading.value = false
    }
  }

  function setQuery(value: string) {
    query.value = value
    page.value = DEFAULT_PAGE
  }

  function setPage(value: number) {
    page.value = value
  }

  function setPageSize(value: number) {
    pageSize.value = value
    page.value = DEFAULT_PAGE
  }

  return {
    packages,
    currentPackage,
    loading,
    detailLoading,
    uploadLoading,
    deleting,
    updatingDescription,
    importTaskLoading,
    query,
    page,
    pageSize,
    total,
    hasData,
    importTaskIds,
    importTaskStatuses,
    fetchPackages,
    fetchPackage,
    uploadPackage,
    uploadPackageFolder,
    importPackageFromDataImport,
    removePackage,
    saveDescription,
    rememberImportTask,
    clearImportTask,
    getKnownImportTaskId,
    getImportTaskStatusForPackage,
    refreshImportProgress,
    setQuery,
    setPage,
    setPageSize
  }
})
