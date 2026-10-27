import test from 'node:test'
import assert from 'node:assert/strict'
import vm from 'node:vm'
import { readFileSync } from 'node:fs'

for (const [file, entry, healthRef] of [
  ['KeyTechnologyDemo/AcceleratedReplay.vue', 'load', 'online'],
  ['KeyTechnologyDemo/DataExpansion.vue', 'load', 'online'],
  ['VirtualPrototype/index.vue', 'loadAll', 'agentOnline'],
]) {
  test(`${file}: successful health remains online when catalog loading fails`, async () => {
    const source = readFileSync(new URL(`../src/views/${file}`, import.meta.url), 'utf8')
    const start = source.indexOf(`async function ${entry}(`)
    const end = source.indexOf('async function ', start + 1)
    const context = {
      online: { value: false }, agentOnline: { value: false }, loading: { value: false },
      loadError: { value: '' }, samples: { value: [] }, catalog: { value: [] },
      capabilities: { value: {} }, alternatives: { value: {} }, fallback: [], FALLBACK_FAULT_CATALOG: [],
      getVirtualPrototypeHealth: async () => ({ status: 'ok' }),
      getVirtualPrototypeCapabilities: async () => { throw new Error('catalog timeout') },
      listVirtualPrototypeSamples: async () => { throw new Error('catalog timeout') },
      refresh: async () => {}, loadJobs: async () => {},
    }
    vm.createContext(context)
    vm.runInContext(source.slice(start, end), context)
    await context[entry]()
    assert.equal(context[healthRef].value, true)
  })
}
