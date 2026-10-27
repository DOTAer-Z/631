import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ElButtonStub,
  ElCardStub,
  ElFormItemStub,
  ElFormStub,
  ElInputStub,
  ElSelectStub,
  ElSpaceStub,
  ElTableColumnStub,
  ElTableStub
} from '@annotation/test-utils/elementStubs'
import AnnotationWorkbenchView from '@annotation/views/annotations/AnnotationWorkbenchView.vue'
import type { PendingAnnotationItem } from '@annotation/types/annotation'

const { pushMock, resolveMock, windowOpenMock, messageError, messageSuccess, messageWarning, triggerDownloadMock, requestRecommendationMock, analyzeMultiMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  resolveMock: vi.fn((to: unknown) => ({ href: `#resolved:${JSON.stringify(to)}` })),
  windowOpenMock: vi.fn(),
  messageError: vi.fn(),
  messageSuccess: vi.fn(),
  messageWarning: vi.fn(),
  triggerDownloadMock: vi.fn(),
  requestRecommendationMock: vi.fn(),
  analyzeMultiMock: vi.fn()
}))

vi.stubEnv('VITE_API_BASE_URL', 'http://localhost:8000/api/v1')

const mockStore = {
  stats: {
    total_annotations: 1,
    normal_count: 1,
    abnormal_count: 0
  },
  pendingItems: [
    {
      package_id: 1,
      package_name: 'pkg-a',
      task_id: 9,
      task_name: 'task-a',
      window_id: 100,
      window_start_ts: 1717286400,
      window_end_ts: 1717286700,
      line_count: 4,
      file_count: 1,
      cpu_count: 1,
      module_count: 1
    }
  ],
  pendingTotal: 25,
  workbenchItems: [],
  workbenchTotal: 0,
  items: [
    {
      id: 9,
      package_id: 1,
      task_id: 9,
      window_id: 101,
      window_start_ts: 1717286700,
      window_end_ts: 1717287000,
      label: 'abnormal' as const,
      anomaly_type: 'softlockup',
      note: 'needs review',
      created_at: '2026-06-02T00:00:00Z',
      updated_at: '2026-06-02T00:00:00Z'
    }
  ],
  loadingList: false,
  page: 1,
  pageSize: 20,
  total: 1,
  currentAnnotation: null as null | { id: number; label: 'normal' | 'abnormal'; anomaly_type: string | null; note: string | null },
  saving: false,
  deleting: false,
  exportLoading: false,
  pendingFilters: {},
  refreshWorkbenchData: vi.fn(async () => undefined),
  fetchForWindow: vi.fn(async () => null),
  createForWindow: vi.fn(async () => ({ id: 5 })),
  updateCurrent: vi.fn(async () => ({ id: 5 })),
  deleteCurrent: vi.fn(async () => undefined),
  exportWithFilters: vi.fn(async () => undefined),
  setFilters: vi.fn(),
  setPendingFilters: vi.fn(),
  discardPendingWindow: vi.fn(async () => undefined),
  fetchList: vi.fn(async () => undefined),
  fetchStats: vi.fn(async () => undefined),
  fetchPending: vi.fn(async () => undefined),
  fetchWorkbench: vi.fn(async () => undefined)
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
  },
  ElMessageBox: {
    confirm: vi.fn(async () => undefined)
  }
}))

vi.mock('@annotation/api/annotations', async () => {
  const actual = await vi.importActual<typeof import('@annotation/api/annotations')>('@annotation/api/annotations')
  return {
    ...actual,
    triggerAnnotationExportDownload: triggerDownloadMock
  }
})

vi.mock('@annotation/api/recommendations', () => ({
  requestWindowRecommendation: requestRecommendationMock,
  analyzeWindowMulti: analyzeMultiMock
}))

vi.mock('@annotation/api/sliceWindows', () => ({
  getSliceWindowFull: vi.fn(async () => ({
    window: { id: 100 },
    tree: { window_id: 100, root: [] },
    annotations: [],
    navigation: { current_window_id: 100, prev_window_id: null, next_window_id: null }
  }))
}))

const mockFaultTypeStore = {
  items: [
    { id: 1, name: 'softlockup', description: 'd', created_at: '', updated_at: '' },
    { id: 2, name: '内存泄漏', description: 'd', created_at: '', updated_at: '' }
  ],
  loading: false,
  saving: false,
  removing: false,
  names: ['softlockup', '内存泄漏'],
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

describe('AnnotationWorkbenchView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('open', windowOpenMock)
    mockStore.currentAnnotation = null
    mockStore.pendingItems = [
      {
        package_id: 1,
        package_name: 'pkg-a',
        task_id: 9,
        task_name: 'task-a',
        window_id: 100,
        window_start_ts: 1717286400,
        window_end_ts: 1717286700,
        line_count: 4,
        file_count: 1,
        cpu_count: 1,
        module_count: 1
      }
    ]
  })

  function mountView() {
    return mount(AnnotationWorkbenchView, {
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
          ElDialog: { template: '<div v-if="modelValue"><slot /><slot name="footer" /></div>', props: ['modelValue', 'title', 'width'] },
          ElRow: { template: '<div><slot /></div>' },
          ElCol: { template: '<div><slot /></div>' },
          ElOption: { template: '<option><slot /></option>' },
          ElPagination: { template: '<div class="el-pagination-stub" />', props: ['pageSize', 'total', 'currentPage'] },
          ElTag: { template: '<span><slot /></span>' },
          ElInputNumber: { template: '<input />', props: ['modelValue', 'min', 'max'] }
        },
        directives: {
          loading: {}
        }
      }
    })
  }

  it('loads workbench data on mount', async () => {
    mountView()
    await flushPromises()

    expect(mockStore.refreshWorkbenchData).toHaveBeenCalledTimes(1)
    expect(mockStore.fetchForWindow).toHaveBeenCalledWith(100)
  })

  it('navigates to the slice-window-browser in the same page when 查看 is clicked on a pending row', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      viewPendingWindow: (row: { package_id: number; task_id: number; window_id: number }) => void
    }
    vm.viewPendingWindow({ package_id: 1, task_id: 9, window_id: 100 })
    await flushPromises()

    expect(pushMock).toHaveBeenCalledWith({
      name: 'slice-window-browser',
      params: { id: 1, taskId: 9 },
      query: { window_id: 100, from: 'annotation' }
    })
    expect(windowOpenMock).not.toHaveBeenCalled()
  })

  it('applies pending filters and pushes them through the store', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="pending-filter-keyword"]').setValue('panic')
    await wrapper.get('[data-testid="pending-filter-min-line-count"]').setValue('5')
    await wrapper.get('[data-testid="pending-filter-submit"]').trigger('click')
    await flushPromises()

    expect(mockStore.setPendingFilters).toHaveBeenCalledWith(
      expect.objectContaining({
        keyword: 'panic',
        min_line_count: 5,
        sort_by: 'window_start_ts',
        sort_order: 'asc'
      })
    )
  })

  it('discards a pending window via the store after confirmation', async () => {
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      confirmDiscardPending: (row: PendingAnnotationItem) => Promise<void>
    }
    await vm.confirmDiscardPending({
      package_id: 1,
      package_name: 'pkg-a',
      task_id: 9,
      task_name: 'task-a',
      window_id: 100,
      window_start_ts: 1,
      window_end_ts: 2,
      line_count: 0,
      file_count: 0,
      cpu_count: 0,
      module_count: 0
    })

    expect(mockStore.discardPendingWindow).toHaveBeenCalledWith(100)
    expect(mockStore.fetchStats).toHaveBeenCalled()
  })

  it('requires anomaly_type when saving abnormal label', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="annotation-label-select"]').setValue('abnormal')
    await wrapper.get('[data-testid="annotation-save-button"]').trigger('click')
    await flushPromises()

    expect(mockStore.createForWindow).not.toHaveBeenCalled()
    expect(messageError).toHaveBeenCalled()
  })

  it('allows custom anomaly_type when saving abnormal label', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="annotation-label-select"]').setValue('abnormal')
    wrapper
      .findAllComponents(ElSelectStub)
      .find((component) => component.attributes('data-testid') === 'annotation-anomaly-select')
      ?.vm.$emit('update:modelValue', 'softlockup')
    await flushPromises()
    await wrapper.get('[data-testid="annotation-save-button"]').trigger('click')
    await flushPromises()

    expect(mockStore.createForWindow).toHaveBeenCalledWith(
      100,
      expect.objectContaining({
        label: 'abnormal',
        anomaly_type: 'softlockup'
      })
    )
    expect(messageSuccess).toHaveBeenCalled()
  })

  it('renders annotated records in the same workbench and removes AI analysis placeholder', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('已标注记录')
    expect(wrapper.text()).not.toContain('AI Analysis')
    expect(wrapper.text()).toContain('数据标注')
    expect(wrapper.find('[data-testid="annotation-records-link"]').exists()).toBe(false)
  })

  it('filters annotated records inside the same workbench', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="record-filter-anomaly-type"]').setValue('softlockup')
    await wrapper.get('[data-testid="record-filter-submit"]').trigger('click')
    await flushPromises()

    expect(mockStore.setFilters).toHaveBeenCalledWith(
      expect.objectContaining({
        anomaly_type: 'softlockup'
      })
    )
    expect(mockStore.fetchList).toHaveBeenCalledWith({ page: 1 })
  })

  it('paginates annotated records via the pagination control', async () => {
    const wrapper = mountView()
    await flushPromises()
    mockStore.fetchList.mockClear()

    await (wrapper.vm as unknown as { goRecordsPage: (p: number) => Promise<void> }).goRecordsPage(2)
    await flushPromises()

    expect(mockStore.fetchList).toHaveBeenCalledWith({ page: 2 })
  })

  it('sorts annotated records on a sortable column change', async () => {
    const wrapper = mountView()
    await flushPromises()
    mockStore.fetchList.mockClear()

    await (
      wrapper.vm as unknown as {
        handleRecordSortChange: (p: { prop?: string; order?: string | null }) => Promise<void>
      }
    ).handleRecordSortChange({ prop: 'id', order: 'ascending' })
    await flushPromises()

    expect(mockStore.fetchList).toHaveBeenCalledWith({ page: 1, sort_by: 'id', sort_order: 'asc' })
  })

  it('loads next pending page and selects the first item on that page', async () => {
    mockStore.fetchPending
      .mockImplementationOnce(async () => {
        mockStore.pendingItems = [
          {
            package_id: 1,
            package_name: 'pkg-a',
            task_id: 9,
            task_name: 'task-a',
            window_id: 100,
            window_start_ts: 1717286400,
            window_end_ts: 1717286700,
            line_count: 4,
            file_count: 1,
            cpu_count: 1,
            module_count: 1
          }
        ]
        return undefined
      })
      .mockImplementationOnce(async () => {
        mockStore.pendingItems = [
          {
            package_id: 1,
            package_name: 'pkg-a',
            task_id: 10,
            task_name: 'task-b',
            window_id: 200,
            window_start_ts: 1717287000,
            window_end_ts: 1717287300,
            line_count: 7,
            file_count: 1,
            cpu_count: 1,
            module_count: 1
          }
        ]
        return undefined
      })

    const wrapper = mountView()
    await flushPromises()
    mockStore.fetchForWindow.mockClear()

    await wrapper.get('[data-testid="pending-next-page"]').trigger('click')
    await flushPromises()

    expect(mockStore.fetchPending).toHaveBeenCalledWith({ page: 2, page_size: 10 })
    expect(mockStore.fetchForWindow).toHaveBeenCalledWith(200)
  })

  it('downloads exported file after export succeeds', async () => {
    mockStore.exportWithFilters.mockResolvedValueOnce({
      format: 'json',
      scope: 'current_filter',
      mode: 'light',
      file_path: '/tmp/a.json',
      file_name: 'a.json',
      download_url: '/api/v1/annotations/export/download?file=a.json',
      item_count: 1
    })

    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="export-current-json"]').trigger('click')
    await flushPromises()

    expect(triggerDownloadMock).toHaveBeenCalledWith('/api/v1/annotations/export/download?file=a.json', 'a.json')
  })

  it('uses selected export scope and mode when exporting', async () => {
    mockStore.exportWithFilters.mockResolvedValueOnce({
      format: 'json',
      scope: 'all',
      mode: 'full',
      file_path: '/tmp/a.json',
      file_name: 'a.json',
      download_url: '/api/v1/annotations/export/download?file=a.json',
      item_count: 1
    })

    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="annotation-export-scope-select"]').setValue('all')
    await wrapper.get('[data-testid="annotation-export-mode-select"]').setValue('full')
    await wrapper.get('[data-testid="export-current-json"]').trigger('click')
    await flushPromises()

    expect(mockStore.exportWithFilters).toHaveBeenCalledWith('json', 'all', 'full')
  })

  it('requests an LLM recommendation and adopts it into the form', async () => {
    requestRecommendationMock.mockResolvedValueOnce({
      window_id: 100,
      status: 'success',
      recommended_label: 'abnormal',
      recommended_anomaly_type: '内存泄漏',
      reason: '内存持续增长',
      model: 'deepseek-chat',
      error_message: null,
      pending_suggestion_id: null,
      created_at: '2026-06-06T00:00:00Z',
      updated_at: '2026-06-06T00:00:00Z'
    })

    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="recommend-button"]').trigger('click')
    await flushPromises()

    expect(requestRecommendationMock).toHaveBeenCalledWith(100)
    expect(wrapper.get('[data-testid="recommendation-label"]').text()).toContain('abnormal')
    expect(wrapper.get('[data-testid="recommendation-anomaly-type"]').text()).toContain('内存泄漏')

    await wrapper.get('[data-testid="recommendation-adopt-button"]').trigger('click')
    await flushPromises()

    // Adopting fills the form; saving then forwards the recommended values.
    await wrapper.get('[data-testid="annotation-save-button"]').trigger('click')
    await flushPromises()

    expect(mockStore.createForWindow).toHaveBeenCalledWith(
      100,
      expect.objectContaining({ label: 'abnormal', anomaly_type: '内存泄漏', note: '内存持续增长' })
    )
  })

  it('runs multi-fault analysis and adopts multiple per-file suggestions in a row', async () => {
    analyzeMultiMock.mockResolvedValueOnce({
      window_id: 100,
      status: 'success',
      multiple_faults: true,
      suggest_subdivide: true,
      suggested_window_seconds: 60,
      files: [
        {
          source_log_file_id: 7,
          logical_path: 'cpu0/mm/oom.log',
          label: 'abnormal',
          anomaly_type: '内存泄漏',
          reason: 'leak'
        },
        {
          source_log_file_id: 8,
          logical_path: 'cpu0/sched/lockup.log',
          label: 'abnormal',
          anomaly_type: '死锁',
          reason: 'lock'
        }
      ],
      model: 'deepseek-chat',
      error_message: null
    })

    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="multi-analyze-button"]').trigger('click')
    await flushPromises()

    expect(analyzeMultiMock).toHaveBeenCalledWith(100)
    expect(wrapper.find('[data-testid="multi-analysis-card"]').exists()).toBe(true)
    // The AI-suggested sub-window length is surfaced on the card.
    expect(wrapper.find('[data-testid="multi-analysis-card"]').text()).toContain('60')

    const vm = wrapper.vm as unknown as {
      adoptFileSuggestion: (
        f: {
          source_log_file_id: number
          logical_path: string
          label: string
          anomaly_type: string | null
          reason: string | null
        },
        rowIndex: number
      ) => Promise<void>
    }

    mockStore.refreshWorkbenchData.mockClear()
    // Pending list reloads (and drops the now-annotated window) between adopts;
    // both adopts must still target window 100 and succeed.
    mockStore.pendingItems = []
    await vm.adoptFileSuggestion(
      {
        source_log_file_id: 7,
        logical_path: 'cpu0/mm/oom.log',
        label: 'abnormal',
        anomaly_type: '内存泄漏',
        reason: 'leak'
      },
      0
    )
    await flushPromises()
    // Only one of two files adopted so far — window must NOT be reloaded yet.
    expect(mockStore.refreshWorkbenchData).not.toHaveBeenCalled()
    await vm.adoptFileSuggestion(
      {
        source_log_file_id: 8,
        logical_path: 'cpu0/sched/lockup.log',
        label: 'abnormal',
        anomaly_type: '死锁',
        reason: 'lock'
      },
      1
    )
    await flushPromises()

    expect(mockStore.createForWindow).toHaveBeenCalledTimes(2)
    expect(mockStore.createForWindow).toHaveBeenNthCalledWith(
      1,
      100,
      expect.objectContaining({ anomaly_type: '内存泄漏', source_log_file_id: 7 })
    )
    expect(mockStore.createForWindow).toHaveBeenNthCalledWith(
      2,
      100,
      expect.objectContaining({ anomaly_type: '死锁', source_log_file_id: 8 })
    )
    // After every per-file suggestion is adopted, the window is fully annotated
    // and the pending list is reloaded so it drops out.
    expect(mockStore.refreshWorkbenchData).toHaveBeenCalledTimes(1)
  })

  it('shows an unavailable notice when recommendation degrades', async () => {
    requestRecommendationMock.mockResolvedValueOnce({
      window_id: 100,
      status: 'failed',
      recommended_label: null,
      recommended_anomaly_type: null,
      reason: null,
      model: null,
      error_message: 'LLM 未配置，无法生成推荐',
      pending_suggestion_id: null,
      created_at: '2026-06-06T00:00:00Z',
      updated_at: '2026-06-06T00:00:00Z'
    })

    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="recommend-button"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="recommendation-error"]').exists()).toBe(true)
    expect(messageWarning).toHaveBeenCalled()
  })

  it('shows the suggestion review button when LLM proposes a new fault type', async () => {
    requestRecommendationMock.mockResolvedValueOnce({
      window_id: 100,
      status: 'success',
      recommended_label: 'abnormal',
      recommended_anomaly_type: null,
      reason: '[已生成新故障类型建议]',
      model: 'deepseek-chat',
      error_message: null,
      pending_suggestion_id: 42,
      created_at: '2026-06-15T00:00:00Z',
      updated_at: '2026-06-15T00:00:00Z'
    })

    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="recommend-button"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="recommendation-suggestion-banner"]').exists()).toBe(true)
    await wrapper.get('[data-testid="recommendation-review-suggestion-button"]').trigger('click')
    expect(pushMock).toHaveBeenCalledWith({ name: 'fault-type-suggestion-list' })
  })
})
