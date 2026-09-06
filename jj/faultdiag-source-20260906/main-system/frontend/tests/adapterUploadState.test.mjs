import test from 'node:test'
import assert from 'node:assert/strict'

import {
  MAX_ADAPTER_ARCHIVE_BYTES,
  createUploadGeneration,
  isUploadCancellation,
  uploadProgressPercent,
  validateAdapterArchive,
} from '../src/views/ModelManagement/adapterUploadState.js'

test('Adapter archives accept zip, tar, and tar.gz extensions case-insensitively', () => {
  for (const name of ['adapter.zip', 'adapter.TAR', 'adapter.Tar.Gz']) {
    assert.equal(validateAdapterArchive({ name, size: 1 }), '')
  }
  for (const name of ['adapter.gz', 'adapter.tgz', 'adapter.zip.exe', 'adapter']) {
    assert.match(validateAdapterArchive({ name, size: 1 }), /ZIP、TAR 或 TAR\.GZ/)
  }
})

test('Adapter archive validation enforces the 1 GiB compressed limit', () => {
  assert.equal(validateAdapterArchive({ name: 'adapter.zip', size: MAX_ADAPTER_ARCHIVE_BYTES }), '')
  assert.match(
    validateAdapterArchive({ name: 'adapter.zip', size: MAX_ADAPTER_ARCHIVE_BYTES + 1 }),
    /1 GiB/,
  )
  assert.match(validateAdapterArchive(null), /请选择/)
})

test('upload progress is finite, rounded, and clamped to zero through one hundred', () => {
  assert.equal(uploadProgressPercent({ loaded: 1, total: 4 }), 25)
  assert.equal(uploadProgressPercent({ loaded: -10, total: 100 }), 0)
  assert.equal(uploadProgressPercent({ loaded: 250, total: 100 }), 100)
  assert.equal(uploadProgressPercent({ loaded: 1, total: 0 }), 0)
  assert.equal(uploadProgressPercent({ loaded: Number.NaN, total: 10 }), 0)
})

test('AbortError and Axios cancellation are non-error upload outcomes', () => {
  assert.equal(isUploadCancellation({ name: 'AbortError' }), true)
  assert.equal(isUploadCancellation({ code: 'ERR_CANCELED' }), true)
  assert.equal(isUploadCancellation({ __CANCEL__: true }), true)
  assert.equal(isUploadCancellation(new Error('network failed')), false)
})

test('upload generations suppress responses after replacement or invalidation', () => {
  const generations = createUploadGeneration()
  const first = generations.start()
  assert.equal(generations.isCurrent(first), true)

  const second = generations.start()
  assert.equal(generations.isCurrent(first), false)
  assert.equal(generations.isCurrent(second), true)

  generations.invalidate()
  assert.equal(generations.isCurrent(second), false)
})
