import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ElAlertStub,
  ElButtonStub,
  ElCardStub,
  ElFormItemStub,
  ElFormStub,
  ElInputStub,
  ElSelectStub,
  ElSpaceStub,
  ElTableColumnStub,
  ElTableStub,
  ElTagStub
} from '@annotation/test-utils/elementStubs'
import SlicingWorkbenchView from '@annotation/views/slices/SlicingWorkbenchView.vue'

const { pushMock, resolveMock, windowOpenMock, messageError, messageSuccess, messageWarning } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  resolveMock: vi.fn((to: unknown) => ({ href: `#resolved:${JSON.stringify(to)}` })),
  windowOpenMock: vi.fn(),
  messageError: vi.fn(),
  messageSuccess: vi.fn(),
  messageWarning: vi.fn()
}))

const mockPackageStore = {
  packages: [
    {
      id: 42,
      name: 'pkg-42',
      import_status: 'imported'
    }
  ],
  loading: false,
  fetchPackages: vi.fn(async () => undefined)
}

const mockSliceTaskStore = {
  tasks: [
    {
      id: 1,
      package_id: 42,
      name: 'slice-1',
      window_seconds: 300,
      status: 'success',
      total_files: 2,
      total_lines: 120,
      total_windows: 4,
      error_message: null,
      created_at: '2026-06-02T00:00:00Z',
      started_at: null,
      finished_at: null,
      windows_count: 1
    }
  ],
  loading: false,
  creating: false,
  deleting: false,
  fetchTasks: vi.fn(async () => undefined),
  fetchTaskDetail: vi.fn(async () => ({
    id: 1,
    package_id: 42,
    name: 'slice-1',
    window_seconds: 300,
    status: 'success',
    total_files: 2,
    total_lines: 120,
    total_windows: 4,
    error_message: null,
    created_at: '2026-06-02T00:00:00Z',
    started_at: null,
    finished_at: null,
    windows_count: 1,
    windows: [
      {
        id: 301,
        window_start_ts: 1717286400,
        window_end_ts: 1717286700,
        line_count: 30,
        file_count: 2,
        cpu_count: 1,
        module_count: 1,
        created_at: '2026-06-02T00:00:00Z'
      }
    ]
  })),
  createTask: vi.fn(async () => ({
    id: 5,
    windows: []
  })),
  deleteTask: vi.fn(async () => undefined)
}

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: pushMock,
    resolve: resolveMock
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
  usePackageStore: () => mockPackageStore
}))

vi.mock('@annotation/stores/sliceTaskStore', () => ({
  useSliceTaskStore: () => mockSliceTaskStore
}))

describe('SlicingWorkbenchView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('open', windowOpenMock)
  })

  function mountView() {
    return mount(SlicingWorkbenchView, {
      global: {
        components: {
          ElSpace: ElSpaceStub,
          ElCard: ElCardStub,
          ElForm: ElFormStub,
          ElFormItem: ElFormItemStub,
          ElInput: ElInputStub,
          ElInputNumber: ElInputStub,
          ElSelect: ElSelectStub,
          ElButton: ElButtonStub,
          ElTable: ElTableStub,
          ElTableColumn: ElTableColumnStub,
          ElTag: ElTagStub,
          ElAlert: ElAlertStub,
          ElRow: { template: '<div><slot /></div>' },
          ElCol: { template: '<div><slot /></div>' },
          ElOption: { template: '<option><slot /></option>' }
        },
        directives: {
          loading: {}
        }
      }
    })
  }

  it('loads package list on mount', async () => {
    mountView()
    await flushPromises()

    expect(mockPackageStore.fetchPackages).toHaveBeenCalledTimes(1)
  })

  it('creates slice task with custom window in minutes (converted to seconds)', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      selectPackage: (row: { id: number }) => Promise<void>
      windowAmount: number
      windowUnit: 'second' | 'minute'
    }
    await vm.selectPackage({ id: 42 })
    await flushPromises()

    await wrapper.get('[data-testid="task-name-input"]').setValue(' nightly-slice ')
    vm.windowAmount = 2
    vm.windowUnit = 'minute'
    await wrapper.get('[data-testid="task-create-submit"]').trigger('click')
    await flushPromises()

    expect(mockSliceTaskStore.createTask).toHaveBeenCalledWith(42, {
      name: 'nightly-slice',
      window_seconds: 120
    })
  })

  it('creates slice task with seconds unit', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      selectPackage: (row: { id: number }) => Promise<void>
      windowAmount: number
      windowUnit: 'second' | 'minute'
    }
    await vm.selectPackage({ id: 42 })
    await flushPromises()

    await wrapper.get('[data-testid="task-name-input"]').setValue('quick')
    vm.windowAmount = 10
    vm.windowUnit = 'second'
    await wrapper.get('[data-testid="task-create-submit"]').trigger('click')
    await flushPromises()

    expect(mockSliceTaskStore.createTask).toHaveBeenCalledWith(42, {
      name: 'quick',
      window_seconds: 10
    })
  })

  it('rejects out-of-range window length', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      selectPackage: (row: { id: number }) => Promise<void>
      windowAmount: number
      windowUnit: 'second' | 'minute'
    }
    await vm.selectPackage({ id: 42 })
    await flushPromises()

    await wrapper.get('[data-testid="task-name-input"]').setValue('too-small')
    vm.windowAmount = 0
    vm.windowUnit = 'second'
    await wrapper.get('[data-testid="task-create-submit"]').trigger('click')
    await flushPromises()

    expect(mockSliceTaskStore.createTask).not.toHaveBeenCalled()
    expect(messageWarning).toHaveBeenCalled()
  })

  it('loads selected task detail for window summaries', async () => {
    const wrapper = mountView()
    await flushPromises()

    await (wrapper.vm as unknown as { selectPackage: (row: { id: number }) => Promise<void> }).selectPackage({ id: 42 })
    await (wrapper.vm as unknown as { selectTask: (row: { id: number }) => Promise<void> }).selectTask({ id: 1 })
    await flushPromises()

    expect(mockSliceTaskStore.fetchTaskDetail).toHaveBeenCalledWith(1)
  })

  it('does not render redundant view-window action button', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="task-select-button-1"]').exists()).toBe(false)
  })

  it('navigates to the shared browser route in the same page for a selected window', async () => {
    const wrapper = mountView()
    await flushPromises()

    await (wrapper.vm as unknown as { selectPackage: (row: { id: number }) => Promise<void> }).selectPackage({ id: 42 })
    await (wrapper.vm as unknown as { selectTask: (row: { id: number }) => Promise<void> }).selectTask({ id: 1 })
    await flushPromises()

    await (wrapper.vm as unknown as { goWindow: (windowId: number) => void }).goWindow(301)

    expect(pushMock).toHaveBeenCalledWith({
      name: 'slice-window-browser',
      params: { id: 42, taskId: 1 },
      query: { window_id: 301, from: 'slicing' }
    })
    expect(windowOpenMock).not.toHaveBeenCalled()
  })
})
