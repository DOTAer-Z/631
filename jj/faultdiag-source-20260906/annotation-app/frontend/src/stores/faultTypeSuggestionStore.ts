import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import {
  acceptFaultTypeSuggestion,
  deleteFaultTypeSuggestion,
  listFaultTypeSuggestions,
  rejectFaultTypeSuggestion
} from '@annotation/api/faultTypeSuggestions'
import type {
  FaultTypeSuggestion,
  FaultTypeSuggestionAcceptPayload,
  FaultTypeSuggestionStatus
} from '@annotation/types/faultTypeSuggestion'

export const useFaultTypeSuggestionStore = defineStore('faultTypeSuggestion', () => {
  const items = ref<FaultTypeSuggestion[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const filterStatus = ref<FaultTypeSuggestionStatus>('pending')
  const loading = ref(false)
  const acting = ref(false)
  const pendingTotal = ref(0)

  const pendingCount = computed(() => pendingTotal.value)

  async function fetchAll(overrides?: { status?: FaultTypeSuggestionStatus; page?: number; page_size?: number }) {
    loading.value = true
    try {
      const targetStatus = overrides?.status ?? filterStatus.value
      const data = await listFaultTypeSuggestions({
        status: targetStatus,
        page: overrides?.page ?? page.value,
        page_size: overrides?.page_size ?? pageSize.value
      })
      items.value = data.items
      total.value = data.total
      page.value = data.page
      pageSize.value = data.page_size
      filterStatus.value = targetStatus
      return data
    } finally {
      loading.value = false
    }
  }

  async function refreshPendingCount() {
    const data = await listFaultTypeSuggestions({ status: 'pending', page: 1, page_size: 1 })
    pendingTotal.value = data.total
    return data.total
  }

  async function accept(id: number | string, payload: FaultTypeSuggestionAcceptPayload = {}) {
    acting.value = true
    try {
      return await acceptFaultTypeSuggestion(id, payload)
    } finally {
      acting.value = false
    }
  }

  async function reject(id: number | string) {
    acting.value = true
    try {
      return await rejectFaultTypeSuggestion(id)
    } finally {
      acting.value = false
    }
  }

  async function remove(id: number | string) {
    acting.value = true
    try {
      await deleteFaultTypeSuggestion(id)
    } finally {
      acting.value = false
    }
  }

  function setFilterStatus(next: FaultTypeSuggestionStatus) {
    filterStatus.value = next
  }

  return {
    items,
    total,
    page,
    pageSize,
    filterStatus,
    loading,
    acting,
    pendingTotal,
    pendingCount,
    fetchAll,
    refreshPendingCount,
    accept,
    reject,
    remove,
    setFilterStatus
  }
})
