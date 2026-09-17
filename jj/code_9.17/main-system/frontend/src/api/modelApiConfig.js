import request from './index'

export const listModelApiConfigs = () => request.get('/model-api/configs')
export const createModelApiConfig = (data) => request.post('/model-api/configs', data)
export const updateModelApiConfig = (id, data) => request.patch(`/model-api/configs/${id}`, data)
export const deleteModelApiConfig = (id) => request.delete(`/model-api/configs/${id}`)
export const activateModelApiConfig = (id) => request.post(`/model-api/configs/${id}/activate`)
export const testModelApiConfig = (id) => request.post(`/model-api/configs/${id}/test`)
