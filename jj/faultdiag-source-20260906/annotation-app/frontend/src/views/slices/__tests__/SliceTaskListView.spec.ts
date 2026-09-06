import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import SliceTaskListView from '@annotation/views/slices/SliceTaskListView.vue'

describe('SliceTaskListView', () => {
  it('renders slicing workbench wrapper', () => {
    const wrapper = mount(SliceTaskListView, {
      global: {
        stubs: {
          SlicingWorkbenchView: {
            template: '<div data-testid="slicing-workbench-stub" />'
          }
        }
      }
    })

    expect(wrapper.get('[data-testid="slicing-workbench-stub"]')).toBeTruthy()
  })
})
