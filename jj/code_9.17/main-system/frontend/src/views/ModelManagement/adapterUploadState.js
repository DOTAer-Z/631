export const MAX_ADAPTER_ARCHIVE_BYTES = 1024 * 1024 * 1024

const ADAPTER_ARCHIVE_PATTERN = /\.(?:zip|tar|tar\.gz)$/i

export function validateAdapterArchive(file) {
  if (!file) return '请选择 Adapter 压缩包'
  if (typeof file.name !== 'string' || !ADAPTER_ARCHIVE_PATTERN.test(file.name)) {
    return '仅支持 ZIP、TAR 或 TAR.GZ 文件'
  }
  if (!Number.isFinite(file.size) || file.size < 0 || file.size > MAX_ADAPTER_ARCHIVE_BYTES) {
    return '压缩包不能超过 1 GiB'
  }
  return ''
}

export function uploadProgressPercent(event) {
  const loaded = Number(event?.loaded)
  const total = Number(event?.total)
  if (!Number.isFinite(loaded) || !Number.isFinite(total) || total <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((loaded / total) * 100)))
}

export function isUploadCancellation(error) {
  return error?.name === 'AbortError' || error?.code === 'ERR_CANCELED' || error?.__CANCEL__ === true
}

export function createUploadGeneration() {
  let current = 0
  return {
    start() {
      current += 1
      return current
    },
    invalidate() {
      current += 1
    },
    isCurrent(generation) {
      return generation === current
    },
  }
}
