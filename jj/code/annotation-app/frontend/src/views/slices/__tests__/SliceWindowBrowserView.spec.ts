import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ElButtonStub,
  ElCardStub,
  ElDialogStub,
  ElEmptyStub,
  ElFormItemStub,
  ElFormStub,
  ElInputStub,
  ElPageHeaderStub,
  ElSelectStub,
  ElTableColumnStub,
  ElTableStub,
  ElTagStub
} from '@annotation/test-utils/elementStubs'
import SliceWindowBrowserView from '@annotation/views/slices/SliceWindowBrowserView.vue'

const { pushMock, messageError, messageWarning, routeQuery } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  messageError: vi.fn(),
  messageWarning: vi.fn(),
  routeQuery: { window_id: '1' } as Record<string, string>
}))

const mockStore = {
  windows: [
    { window_id: 1, window_start_ts: 1717286400, window_end_ts: 1717286700, record_count: 2, created_at: 'x' },
    { window_id: 2, window_start_ts: 1717286700, window_end_ts: 1717287000, record_count: 3, created_at: 'y' }
  ],
  selectedWindow: { id: 1, window_start_ts: 1717286400, window_end_ts: 1717286700, line_count: 2, file_count: 1, cpu_count: 1, module_count: 1 },
  tree: [{ name: 'cpu1', type: 'cpu', path: null, source_file_id: null, children: [{ name: 'moduleA', type: 'module', path: null, source_file_id: null, children: [{ name: 'a.log', type: 'file', path: 'cpu1/moduleA/a.log', source_file_id: 77, children: [] }] }] }],
  navigation: { current_window_id: 1, prev_window_id: null, next_window_id: 2 },
  currentAnnotations: [],
  selectedSourceFileId: 77,
  keyword: '',
  logItems: [],
  nextCursor: 'next-token',
  hasMore: true,
  loadingWindows: false,
  loadingWindow: false,
  loadingLogs: false,
  fetchWindows: vi.fn(async () => ({ items: [{ window_id: 1 }] })),
  selectWindow: vi.fn(async () => undefined),
  reloadAnnotations: vi.fn(async () => undefined),
  loadLogs: vi.fn(async () => undefined),
  loadMore: vi.fn(async () => undefined),
  setKeyword: vi.fn()
}

vi.mock('vue-router', () => ({
  useRoute: () => ({
    params: { id: '7', taskId: '9' },
    query: routeQuery
  }),
  useRouter: () => ({
    push: pushMock
  })
}))

vi.mock('element-plus', () => ({
  ElMessage: {
    error: messageError,
    warning: messageWarning,
    success: vi.fn()
  },
  ElMessageBox: {
    confirm: vi.fn(async () => undefined)
  }
}))

vi.mock('@annotation/stores/sliceWindowStore', () => ({
  useSliceWindowStore: () => mockStore
}))

vi.mock('@annotation/stores/faultTypeStore', () => ({
  useFaultTypeStore: () => ({ items: [{ id: 1, name: '内存泄漏', description: '' }], fetchAll: vi.fn(async () => undefined) })
}))

vi.mock('@annotation/api/annotations', () => ({
  createAnnotation: vi.fn(async () => ({ id: 1 })),
  updateAnnotation: vi.fn(async () => ({ id: 1 })),
  deleteAnnotation: vi.fn(async () => undefined)
}))

vi.mock('@annotation/api/sliceWindows', () => ({
  subdivideSliceWindow: vi.fn(async () => [{ id: 11 }, { id: 12 }])
}))

const ElRowStub = defineComponent({
  setup(_, { slots }) {
    return () => h('div', {}, slots.default?.())
  }
})

const ElColStub = defineComponent({
  setup(_, { slots }) {
    return () => h('div', {}, slots.default?.())
  }
})

const ElTreeStub = defineComponent({
  emits: ['node-click'],
  props: { data: { type: Array, default: () => [] } },
  setup(props, { emit }) {
    return () =>
      h(
        'div',
        {},
        (props.data as any[]).map((node: any, idx: number) =>
          h(
            'button',
            {
              key: idx,
              'data-testid': `tree-node-${idx}`,
              onClick: () => emit('node-click', { type: 'file', source_file_id: 77, path: 'cpu1/moduleA/a.log', name: 'a.log', children: [] })
            },
            node.name
          )
        )
      )
  }
})

describe('SliceWindowBrowserView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Reset route query between tests (some mutate it to exercise query params).
    for (const key of Object.keys(routeQuery)) {
      delete routeQuery[key]
    }
    routeQuery.window_id = '1'
  })

  function mountView() {
    return mount(SliceWindowBrowserView, {
      global: {
        components: {
          ElPageHeader: ElPageHeaderStub,
          ElRow: ElRowStub,
          ElCol: ElColStub,
          ElCard: ElCardStub,
          ElTable: ElTableStub,
          ElTableColumn: ElTableColumnStub,
          ElTree: ElTreeStub,
          ElEmpty: ElEmptyStub,
          ElButton: ElButtonStub,
          ElSelect: ElSelectStub,
          ElDialog: ElDialogStub,
          ElTag: ElTagStub,
          ElInputNumber: { template: '<input />' },
          ElForm: ElFormStub,
          ElFormItem: ElFormItemStub,
          ElInput: ElInputStub,
          ElOption: { template: '<option><slot /></option>' }
        },
        directives: {
          loading: {}
        }
      }
    })
  }

  it('loads window list and selects initial window on mount', async () => {
    mountView()

    await flushPromises()
    expect(mockStore.fetchWindows).toHaveBeenCalledWith('9')
    expect(mockStore.selectWindow).toHaveBeenCalledWith(1)
  })

  it('loads logs when file node clicked', async () => {
    const wrapper = mountView()

    await flushPromises()
    const node = wrapper.get('[data-testid="tree-node-0"]')
    await node.trigger('click')
    await flushPromises()

    expect(mockStore.setKeyword).toHaveBeenCalledWith('')
    expect(mockStore.loadLogs).toHaveBeenCalledWith(77, false)
  })

  it('loads more logs through cursor pagination', async () => {
    const wrapper = mountView()

    await flushPromises()
    await wrapper.get('[data-testid="window-load-more"]').trigger('click')
    await flushPromises()

    expect(mockStore.loadMore).toHaveBeenCalledTimes(1)
  })

  it('shows an "all loaded" hint instead of a disabled button when there is no more', async () => {
    mockStore.hasMore = false
    mockStore.logItems = [{ id: 1, source_file_id: 77, line_no: 1, timestamp: 1, content: 'x' }] as never
    try {
      const wrapper = mountView()
      await flushPromises()
      expect(wrapper.find('[data-testid="window-load-more"]').exists()).toBe(false)
      const done = wrapper.find('[data-testid="window-load-more-done"]')
      expect(done.exists()).toBe(true)
      expect(done.text()).toContain('已显示全部')
    } finally {
      // Restore shared mock state for other tests.
      mockStore.hasMore = true
      mockStore.logItems = []
    }
  })

  it('switches current window from selector', async () => {
    const wrapper = mountView()

    await flushPromises()
    mockStore.selectWindow.mockClear()

    await wrapper.get('[data-testid="window-selector"]').setValue('2')
    await flushPromises()

    expect(mockStore.selectWindow).toHaveBeenCalledWith(2)
  })

  it('falls back to /slicing on back when no source is provided', async () => {
    const wrapper = mountView()
    await flushPromises()
    pushMock.mockClear()

    ;(wrapper.vm as unknown as { goBack: () => void }).goBack()

    expect(pushMock).toHaveBeenCalledWith({ name: 'slicing' })
  })

  it('shows an empty state when the window has no annotations', async () => {
    mockStore.currentAnnotations = []
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="window-annotation-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="window-annotation-table"]').exists()).toBe(false)
  })

  it('renders the annotation table when the window has annotations', async () => {
    mockStore.currentAnnotations = [
      {
        id: 5,
        slice_window_id: 1,
        source_log_file_id: null,
        binding_label: null,
        label: 'abnormal',
        anomaly_type: 'softlockup',
        note: 'n',
        created_at: 'x',
        updated_at: 'y'
      }
    ]
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="window-annotation-table"]').exists()).toBe(true)
    mockStore.currentAnnotations = []
  })

  it('subdivides the current window and refreshes', async () => {
    const { subdivideSliceWindow } = await import('@annotation/api/sliceWindows')
    const wrapper = mountView()
    await flushPromises()

    await (wrapper.vm as unknown as { confirmSubdivide: () => Promise<void> }).confirmSubdivide()
    await flushPromises()

    expect(subdivideSliceWindow).toHaveBeenCalledWith(1, 60)
    expect(mockStore.fetchWindows).toHaveBeenCalled()
  })

  it('pre-fills and opens the subdivide dialog from an AI suggest_seconds query', async () => {
    routeQuery.suggest_seconds = '45'
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { subdivideVisible: boolean; subdivideSeconds: number }
    expect(vm.subdivideVisible).toBe(true)
    expect(vm.subdivideSeconds).toBe(45)
  })

  it('creates a new annotation from the browser page', async () => {
    const { createAnnotation } = await import('@annotation/api/annotations')
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      openAddAnnotation: () => void
      annotationForm: { label: string; anomaly_type: string | null; note: string; source_log_file_id: number | null }
      saveAnnotation: () => Promise<void>
    }
    vm.openAddAnnotation()
    vm.annotationForm.label = 'abnormal'
    vm.annotationForm.anomaly_type = '内存泄漏'
    vm.annotationForm.source_log_file_id = 77
    await vm.saveAnnotation()
    await flushPromises()

    expect(createAnnotation).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ label: 'abnormal', anomaly_type: '内存泄漏', source_log_file_id: 77 })
    )
    expect(mockStore.reloadAnnotations).toHaveBeenCalled()
  })

  it('edits an existing annotation via update', async () => {
    const { updateAnnotation } = await import('@annotation/api/annotations')
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      openEditAnnotation: (row: Record<string, unknown>) => void
      annotationForm: { note: string }
      saveAnnotation: () => Promise<void>
    }
    vm.openEditAnnotation({ id: 9, label: 'normal', anomaly_type: null, note: 'old', source_log_file_id: null })
    vm.annotationForm.note = 'updated'
    await vm.saveAnnotation()
    await flushPromises()

    expect(updateAnnotation).toHaveBeenCalledWith(9, expect.objectContaining({ note: 'updated' }))
    expect(mockStore.reloadAnnotations).toHaveBeenCalled()
  })

  it('deletes an annotation after confirmation', async () => {
    const { deleteAnnotation } = await import('@annotation/api/annotations')
    const wrapper = mountView()
    await flushPromises()

    await (wrapper.vm as unknown as { confirmDeleteAnnotation: (r: { id: number }) => Promise<void> }).confirmDeleteAnnotation({ id: 9 })
    await flushPromises()

    expect(deleteAnnotation).toHaveBeenCalledWith(9)
    expect(mockStore.reloadAnnotations).toHaveBeenCalled()
  })
})
