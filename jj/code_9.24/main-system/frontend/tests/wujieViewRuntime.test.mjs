import test from 'node:test'
import assert from 'node:assert/strict'

import { getWujieViewMode } from '../src/utils/wujieViewRuntime.mjs'

test('standalone view mode shows the project Header and reserves its exact height', () => {
  const mode = getWujieViewMode({ location: { href: 'https://child.example/' } })

  assert.deepEqual(mode, {
    isWujie: false,
    showProjectHeader: true,
    mainHeight: 'calc(100vh - 60px)',
  })
  assert.equal(Object.isFrozen(mode), true)
})

test('Wujie view mode hides only the project Header and gives main the full viewport', () => {
  for (const windowRef of [
    { __POWERED_BY_WUJIE__: true },
    { $wujie: {} },
    { __POWERED_BY_WUJIE__: true, $wujie: { props: undefined } },
  ]) {
    assert.deepEqual(getWujieViewMode(windowRef), {
      isWujie: true,
      showProjectHeader: false,
      mainHeight: '100vh',
    })
  }
})
