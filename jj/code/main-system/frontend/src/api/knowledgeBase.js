import request from './index'

export const listFaultTypes = () => request.get('/fault-types')
export const createFaultType = (data) => request.post('/fault-types', data)
export const updateFaultType = (id, data) => request.put(`/fault-types/${id}`, data)
export const deleteFaultType = (id) => request.delete(`/fault-types/${id}`)

export const listLogs = (params) => request.get('/logs', { params })
export const uploadLog = (formData) => request.post('/logs/upload', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
export const deleteLog = (id) => request.delete(`/logs/${id}`)
export const batchIndex = () => request.post('/logs/batch-index')
// 新增结构化知识案例（故障类型+根因+解决方案+可选样例日志），自动向量化入库
export const createKnowledgeCase = (data) => request.post('/knowledge-cases', data)

// 从标注典型案例导入：列出标注子系统(DB2)异常案例；导入选中项入知识库(RAG)+知识图谱(KG)
export const listAnnotationCases = (params) => request.get('/knowledge-base/annotation-cases', { params })
export const importAnnotationCases = (data) => request.post('/knowledge-base/import-annotation-cases', data)

// 标注库(DB2 data_bj)全局计数，供「文件选择」页展示。
export const getAnnotationSummary = () => request.get('/knowledge-base/annotation-summary')
