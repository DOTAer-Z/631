import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ElBadgeStub,
  ElButtonStub,
  ElCardStub,
  ElEmptyStub,
  ElFormItemStub,
  ElFormStub,
  ElInputStub,
  ElPaginationStub,
  ElSelectStub,
  ElSpaceStub,
  ElTableColumnStub,
  ElTableStub,
  ElTagStub
} from '@annotation/test-utils/elementStubs'
import FaultTypeSuggestionListView from '@annotation/views/faultTypes/FaultTypeSuggestionListView.vue'

const { pushMock, messageError, messageSuccess, confirmMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  messageError: vi.fn(),
  messageSuccess: vi.fn(),
  confirmMock: vi.fn(async () => undefined)
}))

const mockStore = {
  items: [
    {
      id: 11,
      slice_window_id: 5,
      suggested_name: '调度抖动失稳',
      suggested_description: '实时任务调度周期抖动超过阈值。',
      reason: '现有类型未覆盖。',
      model: 'mock-llm',
      status: 'pending' as const,
      accepted_fault_type_id: null,
      created_at: '2026-06-15T01:00:00Z',
      updated_at: '2026-06-15T01:00:00Z'
    }
  ],
  total: 1,
  page: 1,
  pageSize: 20,
  loading: false,
  acting: false,
  pendingTotal: 1,
  fetchAll: vi.fn(async () => undefined),
  refreshPendingCount: vi.fn(async () => 1),
  setFilterStatus: vi.fn(),
  accept: vi.fn(async () => ({
    suggestion: { id: 11, status: 'accepted' },
    fault_type: { id: 30, name: '调度抖动失稳' }
  })),
  reject: vi.fn(async () => ({ id: 11, status: 'rejected' })),
  remove: vi.fn(async () => undefined)
}

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock })
}))

vi.mock('element-plus', () => ({
  ElMessage: { error: messageError, success: messageSuccess, warning: vi.fn() },
  ElMessageBox: { confirm: confirmMock }
}))

vi.mock('@annotation/stores/faultTypeSuggestionStore', () => ({
  useFaultTypeSuggestionStore: () => mockStore
}))

describe('FaultTypeSuggestionListView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.acting = false
  })

  function mountView() {
    return mount(FaultTypeSuggestionListView, {
      global: {
        components: {
          ElSpace: ElSpaceStub,
          ElCard: ElCardStub,
          ElForm: ElFormStub,
          ElFormItem: ElFormItemStub,
          ElInput: ElInputStub,
          ElButton: ElButtonStub,
          ElSelect: ElSelectStub,
          ElEmpty: ElEmptyStub,
          ElTable: ElTableStub,
          ElTableColumn: ElTableColumnStub,
          ElTag: ElTagStub,
          ElBadge: ElBadgeStub,
          ElPagination: ElPaginationStub,
          ElDialog: { template: '<div v-if="modelValue"><slot /><slot name="footer" /></div>', props: ['modelValue', 'title', 'width'] }
        },
        directives: { loading: {} }
      }
    })
  }

  it('loads pending suggestions on mount', async () => {
    mountView()
    await flushPromises()
    expect(mockStore.setFilterStatus).toHaveBeenCalledWith('pending')
    expect(mockStore.fetchAll).toHaveBeenCalledWith({ status: 'pending', page: 1 })
  })

  it('accepts a suggestion via the dialog', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      openAccept: (row: { id: number; suggested_name: string; suggested_description: string }) => void
      submitAccept: () => Promise<void>
    }
    vm.openAccept({ id: 11, suggested_name: '调度抖动失稳', suggested_description: '定义A' })
    await flushPromises()

    await vm.submitAccept()
    await flushPromises()

    expect(mockStore.accept).toHaveBeenCalledWith(11, {
      name: '调度抖动失稳',
      description: '定义A'
    })
    expect(messageSuccess).toHaveBeenCalled()
    expect(mockStore.refreshPendingCount).toHaveBeenCalled()
  })

  it('rejects a suggestion after confirmation', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      rejectRow: (row: { id: number }) => Promise<void>
    }
    await vm.rejectRow({ id: 11 })

    expect(confirmMock).toHaveBeenCalled()
    expect(mockStore.reject).toHaveBeenCalledWith(11)
    expect(messageSuccess).toHaveBeenCalled()
  })

  it('navigates back to fault type management', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="suggestion-back-button"]').trigger('click')
    expect(pushMock).toHaveBeenCalledWith({ name: 'fault-type-manage' })
  })
})
