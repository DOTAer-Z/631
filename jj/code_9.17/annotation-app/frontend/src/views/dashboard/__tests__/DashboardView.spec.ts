import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ElButtonStub, ElCardStub, ElTableColumnStub, ElTableStub } from '@annotation/test-utils/elementStubs'
import DashboardView from '@annotation/views/dashboard/DashboardView.vue'

const { messageError } = vi.hoisted(() => ({
  messageError: vi.fn()
}))

const mockStore = {
  summary: {
    total_packages: 2,
    total_slice_tasks: 3,
    total_windows: 18,
    annotated_windows: 7,
    pending_windows: 11,
    normal_count: 5,
    abnormal_count: 2
  },
  recentPackages: [{ id: 1, name: 'pkg-a', import_status: 'imported', created_at: '2026-06-02T09:00:00Z' }],
  recentSliceTasks: [{ id: 8, package_id: 1, name: 'slice-a', status: 'success', created_at: '2026-06-02T09:10:00Z' }],
  recentAnnotations: [{ id: 12, slice_window_id: 21, label: 'abnormal', anomaly_type: 'cpu', updated_at: '2026-06-02T09:20:00Z' }],
  loading: false,
  refreshing: false,
  initialize: vi.fn(async () => undefined),
  refreshDashboard: vi.fn(async () => undefined)
}

vi.mock('element-plus', () => ({
  ElMessage: {
    error: messageError
  }
}))

vi.mock('@annotation/stores/dashboardStore', () => ({
  useDashboardStore: () => mockStore
}))

describe('DashboardView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('loads dashboard data on mount', async () => {
    mount(DashboardView, {
      global: {
        components: {
          ElButton: ElButtonStub,
          ElCard: ElCardStub,
          ElTable: ElTableStub,
          ElTableColumn: ElTableColumnStub,
          ElRow: { template: '<div><slot /></div>' },
          ElCol: { template: '<div><slot /></div>' },
          ElTag: { template: '<span><slot /></span>' }
        }
      }
    })

    await flushPromises()

    expect(mockStore.initialize).toHaveBeenCalledTimes(1)
  })

  it('refreshes dashboard when clicking refresh button', async () => {
    const wrapper = mount(DashboardView, {
      global: {
        components: {
          ElButton: ElButtonStub,
          ElCard: ElCardStub,
          ElTable: ElTableStub,
          ElTableColumn: ElTableColumnStub,
          ElRow: { template: '<div><slot /></div>' },
          ElCol: { template: '<div><slot /></div>' },
          ElTag: { template: '<span><slot /></span>' }
        }
      }
    })

    await flushPromises()
    mockStore.refreshDashboard.mockClear()

    await wrapper.get('[data-testid="dashboard-refresh"]').trigger('click')
    await flushPromises()

    expect(mockStore.refreshDashboard).toHaveBeenCalledTimes(1)
  })
})
