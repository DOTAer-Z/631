import http from './http'

import type {
  DataImportDetail,
  DataImportListResponse,
  DatasetPackage,
  DeletePackageParams,
  ImportTaskStatusResponse,
  PackageListParams,
  PackageListResponse,
  UpdatePackagePayload
} from '@annotation/types/package'

export async function createPackage(formData: FormData) {
  const { data } = await http.post<DatasetPackage>('/packages', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
  return data
}

// 文件夹上传：多文件 + 各自相对路径，后端打包为 .tar.gz 再走导入流水线。
export async function createPackageFromFolder(formData: FormData) {
  const { data } = await http.post<DatasetPackage>('/packages/folder', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
  return data
}

export async function listPackages(params: PackageListParams) {
  const { data } = await http.get<PackageListResponse>('/packages', {
    params
  })
  return data
}

export async function getPackage(id: number | string) {
  const { data } = await http.get<DatasetPackage>(`/packages/${id}`)
  return data
}

export async function updatePackage(id: number | string, description: string) {
  const payload: UpdatePackagePayload = { description }
  const { data } = await http.patch<DatasetPackage>(`/packages/${id}`, payload)
  return data
}

export async function deletePackage(id: number | string, params: DeletePackageParams) {
  await http.delete(`/packages/${id}`, { params })
}

export async function getImportTask(taskId: number | string) {
  const { data } = await http.get<ImportTaskStatusResponse>(`/import-tasks/${taskId}`)
  return data
}

// 「从主系统导入」：列出主系统（DB1）的数据导入列表；从某数据导入创建数据包并触发导入。
export async function listMainSystemDataImports(params: {
  page?: number
  page_size?: number
  keyword?: string
  status?: string
}) {
  const { data } = await http.get<DataImportListResponse>('/packages/main-system-data-imports', { params })
  return data
}

export async function createPackageFromDataImport(payload: {
  import_id: string | number
  filename?: string
  name?: string
  description?: string | null
}) {
  const { data } = await http.post<DatasetPackage>('/packages/from-data-import', payload)
  return data
}

// 「从主系统导入 → 入知识库」：不建标注包，触发主系统侧摄入（runs/cases / RAG）。
export async function importDataImportToKnowledgeBase(payload: {
  import_id: string | number
  filename?: string
}) {
  const { data } = await http.post<DataImportDetail>(
    '/packages/from-data-import/ingest',
    payload
  )
  return data
}
