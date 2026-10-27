import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ElButtonStub,
  ElCardStub,
  ElDialogStub,
  ElFormItemStub,
  ElFormStub,
  ElIconStub,
  ElInputStub,
  ElPaginationStub,
  ElSpaceStub,
  ElTableColumnStub,
  ElTableStub,
  ElTagStub,
  ElUploadStub
} from '@annotation/test-utils/elementStubs'
import PackageListView from '@annotation/views/packages/PackageListView.vue'

const { mockPush, messageError, messageWarning, messageSuccess } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  messageError: vi.fn(),
  messageWarning: vi.fn(),
  messageSuccess: vi.fn()
}))

const mockStore = {
  query: '',
  packages: [],
  loading: false,
  uploadLoading: false,
  deleting: false,
  page: 1,
  pageSize: 10,
  total: 0,
  fetchPackages: vi.fn(async () => undefined),
  refreshImportProgress: vi.fn(async () => undefined),
  getKnownImportTaskId: vi.fn(() => undefined),
  getImportTaskStatusForPackage: vi.fn(() => null),
  setQuery: vi.fn(),
  setPage: vi.fn(),
  setPageSize: vi.fn(),
  uploadPackage: vi.fn(async () => ({ id: 5, import_task_id: 11 })),
  removePackage: vi.fn(async () => undefined)
}

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: mockPush
  })
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    error: messageError,
    warning: messageWarning,
    success: messageSuccess
  }
}))

vi.mock('@annotation/stores/packageStore', () => ({
  usePackageStore: () => mockStore
}))

describe('PackageListView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.packages = []
    mockStore.query = ''
  })

  function mountView() {
    return mount(PackageListView, {
      global: {
        components: {
          ElSpace: ElSpaceStub,
          ElCard: ElCardStub,
          ElForm: ElFormStub,
          ElFormItem: ElFormItemStub,
          ElInput: ElInputStub,
          ElButton: ElButtonStub,
          ElTable: ElTableStub,
          ElTableColumn: ElTableColumnStub,
          ElPagination: ElPaginationStub,
          ElDialog: ElDialogStub,
          ElTag: ElTagStub,
          ElUpload: ElUploadStub,
          ElIcon: ElIconStub,
          UploadFilled: { template: '<span />' }
        },
        directives: {
          loading: {}
        }
      }
    })
  }

  it('triggers search when pressing Enter in search input', async () => {
    const wrapper = mountView()

    await flushPromises()
    mockStore.fetchPackages.mockClear()
    mockStore.setQuery.mockClear()

    const input = wrapper.get('[data-testid="package-search-input"]')
    await input.setValue('  package keyword  ')
    await input.trigger('keyup', { key: 'Enter' })
    await flushPromises()

    expect(mockStore.setQuery).toHaveBeenCalledTimes(1)
    expect(mockStore.setQuery).toHaveBeenCalledWith('package keyword')
    expect(mockStore.fetchPackages).toHaveBeenCalledTimes(1)
  })

  it('requires matching package name before delete submit', async () => {
    const wrapper = mountView()
    await flushPromises()
    mockStore.removePackage.mockClear()

    await (wrapper.vm as unknown as { openDeleteDialog: (id: number, name: string) => void }).openDeleteDialog(5, 'pkg-5')
    await flushPromises()

    await wrapper.get('[data-testid="package-delete-confirm-input"]').setValue('wrong-name')
    await wrapper.get('[data-testid="package-delete-confirm-submit"]').trigger('click')
    await flushPromises()

    expect(mockStore.removePackage).not.toHaveBeenCalled()
    expect(messageError).toHaveBeenCalled()
  })
})
