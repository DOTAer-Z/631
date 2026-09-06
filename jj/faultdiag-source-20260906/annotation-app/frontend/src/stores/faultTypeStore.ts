import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  createFaultType as createApi,
  deleteFaultType as deleteApi,
  listFaultTypes,
  updateFaultType as updateApi
} from '@annotation/api/faultTypes'
import type { FaultType, FaultTypePayload } from '@annotation/types/faultType'

export const useFaultTypeStore = defineStore('faultType', () => {
  const items = ref<FaultType[]>([])
  const loading = ref(false)
  const saving = ref(false)
  const removing = ref(false)

  const names = computed(() => items.value.map((item) => item.name))

  async function fetchAll() {
    loading.value = true
    try {
      const data = await listFaultTypes()
      items.value = data.items
      return data
    } finally {
      loading.value = false
    }
  }

  async function create(payload: FaultTypePayload) {
    saving.value = true
    try {
      const data = await createApi(payload)
      await fetchAll()
      return data
    } finally {
      saving.value = false
    }
  }

  async function update(id: number, payload: FaultTypePayload) {
    saving.value = true
    try {
      const data = await updateApi(id, payload)
      await fetchAll()
      return data
    } finally {
      saving.value = false
    }
  }

  async function remove(id: number) {
    removing.value = true
    try {
      const data = await deleteApi(id)
      await fetchAll()
      return data
    } finally {
      removing.value = false
    }
  }

  return {
    items,
    loading,
    saving,
    removing,
    names,
    fetchAll,
    create,
    update,
    remove
  }
})
