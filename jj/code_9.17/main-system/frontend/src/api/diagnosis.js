import request from './index'

export const diagnoseFile = (formData) => request.post('/diagnosis', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
export const diagnoseText = (data) => request.post('/diagnosis/text', data)
export const getDiagnosisHistory = (params) => request.get('/diagnosis/history', { params })
// 按日志(run_id)读取最近一次诊断结果（无则返回 null）
export const getDiagnosisByRun = (runId) => request.get(`/diagnosis/by-run/${runId}`)
// 删除一条诊断记录
export const deleteDiagnosis = (id) => request.delete(`/diagnosis/history/${id}`)
// 清除全部「正常 / 非故障」诊断记录
export const deleteNormalDiagnoses = () => request.delete('/diagnosis/history/normal')
