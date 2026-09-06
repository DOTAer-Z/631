import request from './index'

export const uploadDataImport = (formData) => request.post('/data-imports', formData, {
  headers: { 'Content-Type': 'multipart/form-data' },
})

export const listDataImports = (params) => request.get('/data-imports', { params })

export const getDataImport = (importId) => request.get(`/data-imports/${importId}`)

export const updateDataImport = (importId, data) => request.patch(`/data-imports/${importId}`, data)

export const deleteDataImport = (importId) => request.delete(`/data-imports/${importId}`)

export const previewDataImport = (importId) => request.get(`/data-imports/${importId}/preview`)

export const downloadDataImportUrl = (importId) => request.getUri({
  url: `/data-imports/${importId}/download`,
})

// 手动触发/重新触发自动摄入（解压 + 写入 cases / runs / log_entries）
export const ingestDataImport = (importId) => request.post(`/data-imports/${importId}/ingest`)
