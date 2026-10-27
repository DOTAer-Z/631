import request from './index'

export const listTrainingTests = (params) => request.get('/model-training/tests', { params })
export const previewTrainingSplit = (data) => request.post('/model-training/splits/preview', data)
export const createTrainingTask = (data) => request.post('/model-training/tasks', data)
export const listTrainingTasks = (params) => request.get('/model-training/tasks', { params })
export const getTrainingTask = (taskId) => request.get(`/model-training/tasks/${taskId}`)
export const cancelTrainingTask = (taskId) => request.post(`/model-training/tasks/${taskId}/cancel`)
export const retryTrainingTask = (taskId, data) => data === undefined
  ? request.post(`/model-training/tasks/${taskId}/retry`)
  : request.post(`/model-training/tasks/${taskId}/retry`, data)
export const evaluateTrainingTask = (taskId) => request.post(`/model-training/tasks/${taskId}/evaluate`)
export const getTrainingTaskLogs = (taskId, afterLine = 0) => request.get(`/model-training/tasks/${taskId}/logs`, {
  params: { after_line: afterLine },
})
export const deleteTrainingTask = (taskId) => request.delete(`/model-training/tasks/${taskId}`)
export const listTrainingArtifacts = (params) => request.get('/model-training/artifacts', { params })
export const downloadTrainingArtifactUrl = (artifactId) => request.getUri({
  url: `/model-training/artifacts/${artifactId}/download`,
})
export const deleteTrainingArtifact = (artifactId) => request.delete(`/model-training/artifacts/${artifactId}`)
export const importTrainingAdapter = (formData, { signal, onUploadProgress }) => request.post(
  '/model-training/adapters/import',
  formData,
  { signal, onUploadProgress, timeout: 30 * 60 * 1000 },
)
export const listTrainingAdapters = (params) => request.get('/model-training/adapters', { params })
export const evaluateTrainingAdapter = (adapterId, data) => request.post(`/model-training/adapters/${adapterId}/evaluate`, data)
export const getTrainingWorker = () => request.get('/model-training/worker')
