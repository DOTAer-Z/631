import request from './index'

export const predictFile = (formData) => request.post('/prediction', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
export const predictText = (data) => request.post('/prediction/text', data)
export const getPredictionHistory = (params) => request.get('/prediction/history', { params })
export const accessKeyLogs = (data) => request.post('/prediction/access/logs', data)
export const getAccessResults = (params) => request.get('/prediction/access/results', { params })
// 对接入报告(report_id)整体做大模型软件状态分级
export const gradeReport = (data) => request.post('/prediction/grade', data)
// 兼容旧入口：按已接入日志(run_id)分级
export const gradeRun = (data) => request.post('/prediction/grade', data)
// 删除一条预测/分级记录
export const deletePrediction = (id) => request.delete(`/prediction/history/${id}`)
// 读取某接入报告(run_id)最近一次分级结果（无则返回 null），供列表回显/点行回放
export const getPredictionByRun = (runId) => request.get(`/prediction/by-run/${runId}`)
// 级联删除整份接入报告（连带其分级记录与诊断记录），三页同时消失
export const deleteReport = (reportId) => request.delete(`/prediction/report/${reportId}`)
