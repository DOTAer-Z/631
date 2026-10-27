export function createLatestRequestState() {
  let latestRequestId = 0

  return {
    start() {
      latestRequestId += 1
      return latestRequestId
    },
    invalidate() {
      latestRequestId += 1
    },
    isLatest(requestId) {
      return requestId === latestRequestId
    },
  }
}

export function createSerialPoller({
  run,
  delay,
  schedule = (callback, wait) => setTimeout(callback, wait),
  cancel = handle => clearTimeout(handle),
  onError = () => {},
}) {
  let active = false
  let generation = 0
  let inFlight = false
  let scheduledHandle = null

  function scheduleNext(wait) {
    if (!active || inFlight || scheduledHandle !== null) return
    const scheduledGeneration = generation
    scheduledHandle = schedule(() => tick(scheduledGeneration), wait)
  }

  async function tick(scheduledGeneration) {
    scheduledHandle = null
    if (!active || scheduledGeneration !== generation) {
      scheduleNext(0)
      return
    }

    inFlight = true
    try {
      await run()
    } catch (error) {
      try {
        onError(error)
      } catch {
        // A reporting failure must not break the polling lifecycle.
      }
    } finally {
      inFlight = false
      scheduleNext(delay)
    }
  }

  return {
    start() {
      if (active) return false
      active = true
      generation += 1
      scheduleNext(0)
      return true
    },
    stop() {
      if (!active && scheduledHandle === null) return false
      active = false
      generation += 1
      if (scheduledHandle !== null) {
        cancel(scheduledHandle)
        scheduledHandle = null
      }
      return true
    },
    isRunning() {
      return active
    },
  }
}

export function createKeyedOperationState() {
  const activeOperations = new Map()
  let nextTokenId = 0

  return {
    start(key, action) {
      if (activeOperations.has(key)) return null
      nextTokenId += 1
      const token = Object.freeze({ action, id: nextTokenId, key })
      activeOperations.set(key, token)
      return token
    },
    finish(token) {
      if (!token || activeOperations.get(token.key) !== token) return false
      activeOperations.delete(token.key)
      return true
    },
    isPending(key) {
      return activeOperations.has(key)
    },
    isActive(key, action) {
      return activeOperations.get(key)?.action === action
    },
  }
}

export async function collectPaginatedIds({
  fetchPage,
  filters = {},
  getId = item => item.version_id,
  isCurrent = () => true,
  maxPages = 1000,
}) {
  const pageSize = 100
  const filterSnapshot = Object.freeze({ ...filters })
  const ids = new Set()
  const pageLimit = Math.max(1, Math.floor(maxPages))
  let page = 1
  let totalPages = 1

  while (page <= totalPages && page <= pageLimit) {
    if (!isCurrent()) {
      return { cancelled: true, filters: filterSnapshot, ids: [], truncated: false }
    }

    const query = Object.freeze({
      ...filterSnapshot,
      page,
      page_size: pageSize,
    })
    const result = await fetchPage(query)

    if (!isCurrent()) {
      return { cancelled: true, filters: filterSnapshot, ids: [], truncated: false }
    }

    for (const item of result.items || []) {
      const id = getId(item)
      if (id !== null && id !== undefined) ids.add(id)
    }

    if (Number.isFinite(Number(result.total))) {
      totalPages = Math.ceil(Math.max(0, Number(result.total)) / pageSize)
    } else {
      totalPages = result.has_more ? page + 1 : page
    }
    page += 1
  }

  return {
    cancelled: false,
    filters: filterSnapshot,
    ids: [...ids],
    truncated: totalPages > pageLimit,
  }
}
