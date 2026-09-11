import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ElAlertStub,
  ElButtonStub,
  ElCardStub,
  ElDescriptionsItemStub,
  ElDescriptionsStub,
  ElDialogStub,
  ElEmptyStub,
  ElInputStub,
  ElPageHeaderStub,
  ElSpaceStub,
  ElTagStub
} from '@annotation/test-utils/elementStubs'
import PackageDetailView from '@annotation/views/packages/PackageDetailView.vue'

const { mockPush, messageError, messageSuccess, messageWarning } = vi.hoisted(() => ({
  mockPush: vi.fn(),
  messageError: vi.fn(),
  messageSuccess: vi.fn(),
  messageWarning: vi.fn()
}))

const mockStore = {
  currentPackage: {
    id: 42,
    name: 'demo-package',
    archive_type: 'zip',
    stored_path: '/tmp/demo.zip',
    file_size: 1024,
    sha256: 'hash',
    description: 'old desc',
    import_status: 'uploaded',
    import_error_message: null,
    source_file_count: 12,
    source_line_count: 1000,
    cpu_count: 2,
    module_count: 4,
    earliest_timestamp: 1717286400,
    latest_timestamp: 1717286700,
    created_at: '2026-05-31T00:00:00Z',
    updated_at: '2026-05-31T00:00:00Z',
    slice_task_count: 0
  },
  detailLoading: false,
  updatingDescription: false,
  deleting: false,
  fetchPackage: vi.fn(async () => undefined),
  saveDescription: vi.fn(async () => undefined),
  removePackage: vi.fn(async () => undefined),
  getKnownImportTaskId: vi.fn(() => 99),
  getImportTaskStatusForPackage: vi.fn(() => ({ status: 'running' })),
  refreshImportProgress: vi.fn(async () => undefined)
}

vi.mock('vue-router', () => ({
  useRoute: () => ({
    params: {
      id: '42'
    }
  }),
  useRouter: () => ({
    push: mockPush
  })
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    error: messageError,
    success: messageSuccess,
    warning: messageWarning
  }
}))

vi.mock('@annotation/stores/packageStore', () => ({
  usePackageStore: () => mockStore
}))

describe('PackageDetailView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.currentPackage.description = 'old desc'
    mockStore.currentPackage.import_status = 'uploaded'
  })

  function mountView() {
    return mount(PackageDetailView, {
      global: {
        components: {
          ElSpace: ElSpaceStub,
          ElPageHeader: ElPageHeaderStub,
          ElCard: ElCardStub,
          ElButton: ElButtonStub,
          ElAlert: ElAlertStub,
          ElDescriptions: ElDescriptionsStub,
          ElDescriptionsItem: ElDescriptionsItemStub,
          ElEmpty: ElEmptyStub,
          ElDialog: ElDialogStub,
          ElInput: ElInputStub,
          ElTag: ElTagStub
        },
        directives: {
          loading: {}
        }
      }
    })
  }

  it('submits edited description', async () => {
    const wrapper = mountView()

    await flushPromises()
    mockStore.saveDescription.mockClear()

    await wrapper.get('[data-testid="open-edit-dialog"]').trigger('click')
    await wrapper.get('[data-testid="description-input"]').setValue('  new description  ')
    await wrapper.get('[data-testid="description-submit"]').trigger('click')
    await flushPromises()

    expect(mockStore.saveDescription).toHaveBeenCalledTimes(1)
    expect(mockStore.saveDescription).toHaveBeenCalledWith(42, 'new description')
  })

  it('deletes package after name confirmation', async () => {
    const wrapper = mountView()

    await flushPromises()
    mockStore.removePackage.mockClear()

    await wrapper.get('[data-testid="detail-delete-button"]').trigger('click')
    await flushPromises()

    await wrapper.get('[data-testid="detail-delete-confirm-input"]').setValue('demo-package')
    await wrapper.get('[data-testid="detail-delete-confirm-submit"]').trigger('click')
    await flushPromises()

    expect(mockStore.removePackage).toHaveBeenCalledTimes(1)
    expect(mockStore.removePackage).toHaveBeenCalledWith(42, 'demo-package')
    expect(mockPush).toHaveBeenCalledWith({ name: 'packages' })
  })

  it('prevents slicing navigation before import completes', async () => {
    const wrapper = mountView()

    await flushPromises()
    mockPush.mockClear()

    const slicingButton = wrapper.get('[data-testid="go-slicing-button"]')
    expect(slicingButton.attributes('disabled')).toBeDefined()
    await slicingButton.trigger('click')
    await flushPromises()

    expect(mockPush).not.toHaveBeenCalled()
  })
})
