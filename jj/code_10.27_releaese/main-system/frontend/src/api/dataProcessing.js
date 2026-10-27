import request from './index'

export const preprocessText = (data) => request.post('/data-processing/preprocess', data)
export const preprocessFile = (formData) => request.post('/data-processing/preprocess/file', formData, {
  headers: { 'Content-Type': 'multipart/form-data' },
})
export const saveProcessedLog = (data) => request.post('/data-processing/save', data)
