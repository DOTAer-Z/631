import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const helperPath = new URL('../src/views/ModelManagement/trainingUiState.js', import.meta.url)

async function loadHelpers() {
  const source = await readFile(helperPath, 'utf8')
  return import(`data:text/javascript;charset=utf-8,${encodeURIComponent(source)}`)
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function createFakeScheduler() {
  const scheduled = []

  return {
    schedule(callback, delay) {
      const handle = { callback, cancelled: false, delay }
      scheduled.push(handle)
      return handle
    },
    cancel(handle) {
      handle.cancelled = true
    },
    pending() {
      return scheduled.filter(handle => !handle.cancelled).length
    },
    runNext() {
      let handle
      while ((handle = scheduled.shift())) {
        if (!handle.cancelled) return handle.callback()
      }
      return null
    },
  }
}

test('latest request tokens become stale after lifecycle invalidation', async () => {
  const { createLatestRequestState } = await loadHelpers()
  const requests = createLatestRequestState()
  const lifecycleToken = requests.start()

  assert.equal(requests.isLatest(lifecycleToken), true)
  requests.invalidate()
  assert.equal(requests.isLatest(lifecycleToken), false)
})

test('latest request semantics preserve the newest detail or log result', async () => {
  const { createLatestRequestState } = await loadHelpers()
  const requests = createLatestRequestState()
  const older = deferred()
  const newer = deferred()
  const applied = []

  async function load(request) {
    const token = requests.start()
    const value = await request.promise
    if (requests.isLatest(token)) applied.push(value)
  }

  const olderLoad = load(older)
  const newerLoad = load(newer)
  newer.resolve('new detail')
  await newerLoad
  older.resolve('old detail')
  await olderLoad

  assert.deepEqual(applied, ['new detail'])
})

test('stable dialog lifecycle keeps the opening catalog current across step navigation', async () => {
  const { createLatestRequestState } = await loadHelpers()
  const lifecycle = createLatestRequestState()
  const catalogRequests = createLatestRequestState()
  const advanceRequests = createLatestRequestState()
  const dialogToken = lifecycle.start()
  const catalog = deferred()
  const applied = []
  let step = 0

  async function loadOpeningCatalog() {
    const requestToken = catalogRequests.start()
    const value = await catalog.promise
    if (lifecycle.isLatest(dialogToken) && catalogRequests.isLatest(requestToken)) applied.push(value)
  }

  const catalogLoad = loadOpeningCatalog()
  const advanceToken = advanceRequests.start()
  if (lifecycle.isLatest(dialogToken) && advanceRequests.isLatest(advanceToken)) step += 1

  catalog.resolve(['test-a'])
  await catalogLoad

  assert.equal(step, 1)
  assert.deepEqual(applied, [['test-a']])
  assert.equal(lifecycle.isLatest(dialogToken), true)

  lifecycle.invalidate()
  assert.equal(lifecycle.isLatest(dialogToken), false)
})

test('backward navigation makes an in-flight submit token stale', async () => {
  const { createLatestRequestState } = await loadHelpers()
  const submitRequests = createLatestRequestState()
  const submission = deferred()
  const created = []

  async function submit() {
    const submitToken = submitRequests.start()
    const task = await submission.promise
    if (submitRequests.isLatest(submitToken)) created.push(task)
  }

  const pendingSubmit = submit()
  submitRequests.invalidate()
  submission.resolve({ id: 'stale-task' })
  await pendingSubmit

  assert.deepEqual(created, [])
})

test('serial polling never overlaps a slow request and applies it when it settles', async () => {
  const { createSerialPoller } = await loadHelpers()
  const scheduler = createFakeScheduler()
  const slow = deferred()
  let inFlight = 0
  let maximumInFlight = 0
  const applied = []

  const poller = createSerialPoller({
    delay: 5000,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
    async run() {
      inFlight += 1
      maximumInFlight = Math.max(maximumInFlight, inFlight)
      try {
        const value = await slow.promise
        applied.push(value)
      } finally {
        inFlight -= 1
      }
    },
  })

  poller.start()
  const firstTick = scheduler.runNext()
  await Promise.resolve()

  assert.equal(scheduler.pending(), 0, 'the next tick is not scheduled while work is running')
  assert.equal(maximumInFlight, 1)

  slow.resolve('slow response')
  await firstTick

  assert.deepEqual(applied, ['slow response'])
  assert.equal(scheduler.pending(), 1, 'the next tick is scheduled only after settlement')
  poller.stop()
  assert.equal(scheduler.pending(), 0)
})

test('serial polling catches a background rejection and continues', async () => {
  const { createSerialPoller } = await loadHelpers()
  const scheduler = createFakeScheduler()
  const errors = []
  let runs = 0

  const poller = createSerialPoller({
    delay: 2000,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
    onError(error) {
      errors.push(error.message)
    },
    async run() {
      runs += 1
      if (runs === 1) throw new Error('temporary failure')
    },
  })

  poller.start()
  await scheduler.runNext()

  assert.deepEqual(errors, ['temporary failure'])
  assert.equal(scheduler.pending(), 1)

  await scheduler.runNext()
  assert.equal(runs, 2)
  assert.equal(scheduler.pending(), 1)
  poller.stop()
})

test('keyed operations reject conflicting starts and release only the exact token', async () => {
  const { createKeyedOperationState } = await loadHelpers()
  const operations = createKeyedOperationState()
  const retry = operations.start('task:7', 'retry')
  const otherTask = operations.start('task:8', 'cancel')

  assert.ok(retry)
  assert.ok(otherTask)
  assert.equal(operations.start('task:7', 'delete'), null)
  assert.equal(operations.isPending('task:7'), true)
  assert.equal(operations.isActive('task:7', 'retry'), true)
  assert.equal(operations.finish({ ...retry }), false, 'a copied token cannot release active work')
  assert.equal(operations.isPending('task:7'), true)
  assert.equal(operations.finish(retry), true)
  assert.equal(operations.isPending('task:7'), false)
  assert.equal(operations.finish(retry), false)
})

test('paginated ID collection freezes filters, uses page size 100, and deduplicates IDs', async () => {
  const { collectPaginatedIds } = await loadHelpers()
  const filters = { search: 'alpha', completeness: 'complete' }
  const queries = []

  const result = await collectPaginatedIds({
    filters,
    async fetchPage(query) {
      queries.push(query)
      filters.search = 'changed while loading'
      return {
        total: 201,
        items: query.page === 1
          ? [{ version_id: 'a' }, { version_id: 'b' }]
          : query.page === 2
            ? [{ version_id: 'b' }, { version_id: 'c' }]
            : [{ version_id: 'c' }, { version_id: 'd' }],
      }
    },
  })

  assert.deepEqual(result.ids, ['a', 'b', 'c', 'd'])
  assert.equal(result.cancelled, false)
  assert.equal(Object.isFrozen(result.filters), true)
  assert.equal(result.filters.search, 'alpha')
  assert.equal(queries.length, 3)
  for (const query of queries) {
    assert.equal(Object.isFrozen(query), true)
    assert.equal(query.search, 'alpha')
    assert.equal(query.page_size, 100)
  }
})

test('paginated ID collection stops at its page bound', async () => {
  const { collectPaginatedIds } = await loadHelpers()
  let calls = 0

  const result = await collectPaginatedIds({
    filters: { search: '' },
    maxPages: 2,
    async fetchPage() {
      calls += 1
      return { total: 1000, items: [{ version_id: `id-${calls}` }] }
    },
  })

  assert.equal(calls, 2)
  assert.equal(result.truncated, true)
  assert.deepEqual(result.ids, ['id-1', 'id-2'])
})

test('paginated ID collection discards partial IDs when its latest token is cancelled', async () => {
  const { collectPaginatedIds, createLatestRequestState } = await loadHelpers()
  const requests = createLatestRequestState()
  const token = requests.start()
  const firstPage = deferred()
  let calls = 0

  const collection = collectPaginatedIds({
    filters: { search: 'stable' },
    isCurrent: () => requests.isLatest(token),
    async fetchPage() {
      calls += 1
      return firstPage.promise
    },
  })

  requests.invalidate()
  firstPage.resolve({ total: 200, items: [{ version_id: 'stale' }] })
  const result = await collection

  assert.equal(calls, 1)
  assert.equal(result.cancelled, true)
  assert.deepEqual(result.ids, [])
})
