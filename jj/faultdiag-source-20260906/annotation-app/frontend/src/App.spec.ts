import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'

const initializePortalContext = vi.hoisted(() => vi.fn())
const clearPortalContext = vi.hoisted(() => vi.fn())

vi.mock('@annotation/platform/portalContext', () => ({ clearPortalContext, initializePortalContext }))

function mountApp(initialPortalContextError = true) {
  return mount(App, {
    props: { initialPortalContextError },
    global: {
      stubs: {
        'el-config-provider': { template: '<div><slot /></div>' },
        'el-button': { template: '<button @click="$emit(\'click\')"><slot /></button>' },
        RouterView: { template: '<main data-testid="router-view" />' }
      }
    }
  })
}

describe('App portal connection state', () => {
  beforeEach(() => {
    initializePortalContext.mockReset()
    clearPortalContext.mockReset()
  })

  it('hides API-fetching routes after a connection failure and retries explicitly', async () => {
    initializePortalContext.mockResolvedValueOnce({ token: 'fresh', userInfo: null, namespaceId: null })
    const wrapper = mountApp()

    expect(wrapper.text()).toContain('门户连接失败')
    expect(wrapper.find('[data-testid="router-view"]').exists()).toBe(false)

    await wrapper.get('[data-testid="portal-context-retry"]').trigger('click')
    await flushPromises()

    expect(initializePortalContext).toHaveBeenCalledOnce()
    expect(wrapper.find('[data-testid="router-view"]').exists()).toBe(true)
  })

  it('keeps the explicit error state when a retry fails again', async () => {
    initializePortalContext.mockRejectedValueOnce(new Error('still unavailable'))
    const wrapper = mountApp()

    await wrapper.get('[data-testid="portal-context-retry"]').trigger('click')
    await flushPromises()

    expect(initializePortalContext).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('门户连接失败')
    expect(wrapper.find('[data-testid="router-view"]').exists()).toBe(false)
  })

  it('clears the in-memory portal context when the app unmounts', () => {
    const wrapper = mountApp(false)

    wrapper.unmount()

    expect(clearPortalContext).toHaveBeenCalledOnce()
  })
})
