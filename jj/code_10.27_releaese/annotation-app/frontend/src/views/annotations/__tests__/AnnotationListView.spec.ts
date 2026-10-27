import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ElAlertStub,
  ElButtonStub,
  ElCardStub,
  ElFormItemStub,
  ElFormStub,
  ElInputStub,
  ElPaginationStub,
  ElSelectStub,
  ElSpaceStub,
  ElTableColumnStub,
  ElTableStub
} from '@annotation/test-utils/elementStubs'
import AnnotationListView from '@annotation/views/annotations/AnnotationListView.vue'

const { messageSuccess, messageError } = vi.hoisted(() => ({
  messageSuccess: vi.fn(),
  messageError: vi.fn()
}))

const mockStore = {
  items: [],
  loadingList: false,
  page: 1,
  pageSize: 20,
  total: 0,
  exportLoading: false,
  lastExport: null as null | { format: 'csv' | 'json'; scope: 'current_filter' | 'all'; file_path: string; item_count: number },
  setFilters: vi.fn(),
  fetchList: vi.fn(async () => undefined),
  exportWithFilters: vi.fn(async () => ({ format: 'csv', scope: 'current_filter', file_path: '/tmp/a.csv', item_count: 1 }))
}

vi.mock('element-plus', () => ({
  ElMessage: {
    success: messageSuccess,
    error: messageError
  },
  ElMessageBox: {
    confirm: vi.fn(async () => undefined)
  }
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: vi.fn()
  })
}))

const mockFaultTypeStore = {
  items: [],
  loading: false,
  saving: false,
  removing: false,
  names: [],
  fetchAll: vi.fn(async () => undefined),
  create: vi.fn(),
  update: vi.fn(),
  remove: vi.fn()
}

vi.mock('@annotation/stores/annotationStore', () => ({
  useAnnotationStore: () => mockStore
}))

vi.mock('@annotation/stores/faultTypeStore', () => ({
  useFaultTypeStore: () => mockFaultTypeStore
}))

describe('AnnotationListView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.lastExport = null
  })

  function mountView() {
    return mount(AnnotationListView, {
      global: {
        components: {
          ElSpace: ElSpaceStub,
          ElCard: ElCardStub,
          ElForm: ElFormStub,
          ElFormItem: ElFormItemStub,
          ElInput: ElInputStub,
          ElSelect: ElSelectStub,
          ElButton: ElButtonStub,
          ElTable: ElTableStub,
          ElTableColumn: ElTableColumnStub,
          ElPagination: ElPaginationStub,
          ElAlert: ElAlertStub,
          ElDialog: { template: '<div v-if="modelValue"><slot /><slot name="footer" /></div>', props: ['modelValue', 'title', 'width'] },
          ElOption: { template: '<option><slot /></option>' }
        },
        directives: {
          loading: {}
        }
      }
    })
  }

  it('fetches list on mount', async () => {
    mountView()

    await flushPromises()
    expect(mockStore.setFilters).toHaveBeenCalledTimes(1)
    expect(mockStore.fetchList).toHaveBeenCalledWith({ page: 1 })
  })

  it('exports csv with current_filter scope', async () => {
    const wrapper = mountView()

    await flushPromises()
    mockStore.exportWithFilters.mockClear()

    await wrapper.get('[data-testid="export-csv-button"]').trigger('click')
    await flushPromises()

    expect(mockStore.exportWithFilters).toHaveBeenCalledWith('csv', 'current_filter')
  })
})
