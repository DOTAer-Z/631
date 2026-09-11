import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ElBadgeStub,
  ElButtonStub,
  ElCardStub,
  ElFormItemStub,
  ElFormStub,
  ElInputStub,
  ElSpaceStub,
  ElTableColumnStub,
  ElTableStub
} from '@annotation/test-utils/elementStubs'
import FaultTypeManageView from '@annotation/views/faultTypes/FaultTypeManageView.vue'

const { pushMock, messageError, messageSuccess, messageWarning, confirmMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  messageError: vi.fn(),
  messageSuccess: vi.fn(),
  messageWarning: vi.fn(),
  confirmMock: vi.fn(async () => undefined)
}))

const mockStore = {
  items: [
    { id: 1, name: '内存泄漏', description: '描述A', created_at: '2026-06-13T00:00:00Z', updated_at: '2026-06-13T00:00:00Z' },
    { id: 2, name: '死锁', description: '描述B', created_at: '2026-06-13T00:00:00Z', updated_at: '2026-06-13T00:00:00Z' }
  ],
  loading: false,
  saving: false,
  removing: false,
  fetchAll: vi.fn(async () => undefined),
  create: vi.fn(async () => undefined),
  update: vi.fn(async () => undefined),
  remove: vi.fn(async () => ({ id: 1, name: '内存泄漏', referenced_count: 0 }))
}

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: pushMock })
}))

vi.mock('element-plus', () => ({
  ElMessage: { error: messageError, success: messageSuccess, warning: messageWarning },
  ElMessageBox: { confirm: confirmMock }
}))

vi.mock('@annotation/stores/faultTypeStore', () => ({
  useFaultTypeStore: () => mockStore
}))

const mockSuggestionStore = {
  pendingTotal: 0,
  refreshPendingCount: vi.fn(async () => 0)
}

vi.mock('@annotation/stores/faultTypeSuggestionStore', () => ({
  useFaultTypeSuggestionStore: () => mockSuggestionStore
}))

describe('FaultTypeManageView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function mountView() {
    return mount(FaultTypeManageView, {
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
          ElBadge: ElBadgeStub,
          ElDialog: { template: '<div v-if="modelValue"><slot /><slot name="footer" /></div>', props: ['modelValue', 'title', 'width'] }
        },
        directives: { loading: {} }
      }
    })
  }

  it('loads fault types on mount', async () => {
    mountView()
    await flushPromises()
    expect(mockStore.fetchAll).toHaveBeenCalledTimes(1)
  })

  it('navigates back to annotation workbench', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="back-to-workbench"]').trigger('click')
    expect(pushMock).toHaveBeenCalledWith({ name: 'annotation-workbench' })
  })

  it('rejects empty name on submit', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="fault-type-create-button"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="fault-type-submit-button"]').trigger('click')
    await flushPromises()

    expect(mockStore.create).not.toHaveBeenCalled()
    expect(messageError).toHaveBeenCalled()
  })

  it('creates a new fault type with valid input', async () => {
    const wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-testid="fault-type-create-button"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="fault-type-name-input"]').setValue('新故障')
    await wrapper.get('[data-testid="fault-type-description-input"]').setValue('详细描述')
    await wrapper.get('[data-testid="fault-type-submit-button"]').trigger('click')
    await flushPromises()

    expect(mockStore.create).toHaveBeenCalledWith({ name: '新故障', description: '详细描述' })
    expect(messageSuccess).toHaveBeenCalled()
  })

  it('warns when delete affects existing annotations', async () => {
    mockStore.remove.mockResolvedValueOnce({ id: 1, name: '内存泄漏', referenced_count: 3 })

    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as { confirmDelete: (row: { id: number; name: string }) => Promise<void> }
    await vm.confirmDelete({ id: 1, name: '内存泄漏' })
    await flushPromises()

    expect(confirmMock).toHaveBeenCalled()
    expect(mockStore.remove).toHaveBeenCalledWith(1)
    expect(messageWarning).toHaveBeenCalled()
  })
})
