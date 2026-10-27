import { defineComponent, h } from 'vue'

export const ElSpaceStub = defineComponent({
  name: 'ElSpaceStub',
  setup(_, { slots }) {
    return () => h('div', {}, slots.default?.())
  }
})

export const ElCardStub = defineComponent({
  name: 'ElCardStub',
  setup(_, { slots }) {
    return () =>
      h('section', {}, [
        slots.header ? h('div', { class: 'card-header' }, slots.header()) : null,
        h('div', { class: 'card-body' }, slots.default?.())
      ])
  }
})

export const ElFormStub = defineComponent({
  name: 'ElFormStub',
  emits: ['submit'],
  setup(_, { slots, emit }) {
    return () => h('form', { onSubmit: (event: Event) => emit('submit', event) }, slots.default?.())
  }
})

export const ElFormItemStub = defineComponent({
  name: 'ElFormItemStub',
  setup(_, { slots }) {
    return () => h('div', {}, slots.default?.())
  }
})

export const ElInputStub = defineComponent({
  name: 'ElInputStub',
  inheritAttrs: false,
  props: {
    modelValue: {
      type: String,
      default: ''
    }
  },
  emits: ['update:modelValue', 'keyup', 'clear'],
  setup(props, { attrs, emit }) {
    return () =>
      h('input', {
        ...attrs,
        value: props.modelValue,
        onInput: (event: Event) => emit('update:modelValue', (event.target as HTMLInputElement).value),
        onKeyup: (event: KeyboardEvent) => emit('keyup', event)
      })
  }
})

export const ElButtonStub = defineComponent({
  name: 'ElButtonStub',
  inheritAttrs: false,
  emits: ['click'],
  setup(_, { attrs, slots, emit }) {
    return () =>
      h(
        'button',
        {
          ...attrs,
          onClick: (event: Event) => emit('click', event)
        },
        slots.default?.()
      )
  }
})

export const ElDialogStub = defineComponent({
  name: 'ElDialogStub',
  props: {
    modelValue: {
      type: Boolean,
      default: false
    }
  },
  setup(props, { slots }) {
    return () =>
      props.modelValue
        ? h('div', { 'data-testid': 'dialog' }, [slots.default?.(), slots.footer ? h('footer', {}, slots.footer()) : null])
        : null
  }
})

export const ElPaginationStub = defineComponent({
  name: 'ElPaginationStub',
  setup() {
    return () => h('div')
  }
})

export const ElTableStub = defineComponent({
  name: 'ElTableStub',
  props: {
    data: {
      type: Array,
      default: () => []
    }
  },
  setup() {
    return () => h('div')
  }
})

export const ElTableColumnStub = defineComponent({
  name: 'ElTableColumnStub',
  setup() {
    return () => null
  }
})

export const ElPageHeaderStub = defineComponent({
  name: 'ElPageHeaderStub',
  emits: ['back'],
  setup(_, { slots, emit }) {
    return () =>
      h('div', {}, [
        h('button', { 'data-testid': 'page-back', onClick: () => emit('back') }, 'back'),
        slots.content?.(),
        slots.default?.()
      ])
  }
})

export const ElDescriptionsStub = defineComponent({
  name: 'ElDescriptionsStub',
  setup(_, { slots }) {
    return () => h('div', {}, slots.default?.())
  }
})

export const ElDescriptionsItemStub = defineComponent({
  name: 'ElDescriptionsItemStub',
  setup(_, { slots }) {
    return () => h('div', {}, slots.default?.())
  }
})

export const ElEmptyStub = defineComponent({
  name: 'ElEmptyStub',
  setup() {
    return () => h('div')
  }
})

export const ElSelectStub = defineComponent({
  name: 'ElSelectStub',
  inheritAttrs: false,
  props: {
    modelValue: {
      type: [String, Number, null],
      default: null
    },
    disabled: {
      type: Boolean,
      default: false
    }
  },
  emits: ['update:modelValue'],
  setup(props, { attrs, slots, emit }) {
    return () =>
      h(
        'select',
        {
          ...attrs,
          disabled: props.disabled,
          value: props.modelValue ?? '',
          onChange: (event: Event) => emit('update:modelValue', (event.target as HTMLSelectElement).value || undefined)
        },
        slots.default?.()
      )
  }
})

export const ElAlertStub = defineComponent({
  name: 'ElAlertStub',
  inheritAttrs: false,
  props: {
    title: {
      type: String,
      default: ''
    }
  },
  setup(props) {
    return () => h('div', { role: 'alert' }, props.title)
  }
})

export const ElUploadStub = defineComponent({
  name: 'ElUploadStub',
  setup(_, { slots }) {
    return () => h('div', {}, slots.default?.())
  }
})

export const ElIconStub = defineComponent({
  name: 'ElIconStub',
  setup(_, { slots }) {
    return () => h('span', {}, slots.default?.())
  }
})

export const ElTagStub = defineComponent({
  name: 'ElTagStub',
  setup(_, { slots }) {
    return () => h('span', {}, slots.default?.())
  }
})

export const ElBadgeStub = defineComponent({
  name: 'ElBadgeStub',
  inheritAttrs: false,
  setup(_, { slots }) {
    return () => h('div', {}, slots.default?.())
  }
})
