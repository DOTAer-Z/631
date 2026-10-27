import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { createSliceTask, deleteSliceTask, getSliceTask, listSliceTasks } from '@annotation/api/sliceTasks'
import type { SliceTaskCreatePayload, SliceTaskDetail, SliceTaskSummary } from '@annotation/types/sliceTask'

export const useSliceTaskStore = defineStore('slice-task', () => {
  const tasks = ref<SliceTaskSummary[]>([])
  const currentTask = ref<SliceTaskDetail | null>(null)

  const loading = ref(false)
  const creating = ref(false)
  const deleting = ref(false)
  const detailLoading = ref(false)

  const total = ref(0)

  const hasData = computed(() => tasks.value.length > 0)

  async function fetchTasks(packageId: number | string) {
    loading.value = true
    try {
      const data = await listSliceTasks(packageId)
      tasks.value = data.items
      total.value = data.total
      return data
    } finally {
      loading.value = false
    }
  }

  async function createTask(packageId: number | string, payload: SliceTaskCreatePayload) {
    creating.value = true
    try {
      const created = await createSliceTask(packageId, payload)
      await fetchTasks(packageId)
      return created
    } finally {
      creating.value = false
    }
  }

  async function fetchTaskDetail(taskId: number | string) {
    detailLoading.value = true
    try {
      currentTask.value = await getSliceTask(taskId)
      return currentTask.value
    } finally {
      detailLoading.value = false
    }
  }

  async function deleteTask(taskId: number | string, packageId: number | string) {
    deleting.value = true
    try {
      await deleteSliceTask(taskId)
      if (currentTask.value?.id === Number(taskId)) {
        currentTask.value = null
      }
      await fetchTasks(packageId)
    } finally {
      deleting.value = false
    }
  }

  return {
    tasks,
    currentTask,
    loading,
    creating,
    deleting,
    detailLoading,
    total,
    hasData,
    fetchTasks,
    createTask,
    fetchTaskDetail,
    deleteTask
  }
})
