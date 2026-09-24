import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App.vue'

const clearPortalContext = vi.hoisted(() => vi.fn())

vi.mock('@annotation/platform/portalContext', () => ({ clearPortalContext }))

function mountApp() {
  return mount(App, {
    global: {
      stubs: {
        'el-config-provider': { template: '<div><slot /></div>' },
        RouterView: { template: '<main data-testid="router-view" />' }
      }
    }
  })
}

describe('App', () => {
  beforeEach(() => {
    clearPortalContext.mockReset()
  })

  // 深融合后握手已移除,不再有「门户连接失败」中间态:路由内容始终直接渲染。
  it('renders routed content immediately without a portal handshake gate', () => {
    const wrapper = mountApp()

    expect(wrapper.find('[data-testid="router-view"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('门户连接失败')
  })

  it('clears the in-memory portal context when the app unmounts', () => {
    const wrapper = mountApp()

    wrapper.unmount()

    expect(clearPortalContext).toHaveBeenCalledOnce()
  })
})
