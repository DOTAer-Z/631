import axios from 'axios'

import { getAnnotationAppOrigin, getPortalContext } from '@annotation/platform/portalContext'

const configuredBaseURL = import.meta.env.VITE_API_BASE_URL || '/api'
const annotationAppOrigin = getAnnotationAppOrigin()
const configuredApiUrl = new URL(configuredBaseURL, `${annotationAppOrigin}/`)
if (
  (configuredApiUrl.protocol !== 'http:' && configuredApiUrl.protocol !== 'https:')
  || configuredApiUrl.username
  || configuredApiUrl.password
) {
  throw new TypeError('annotation API base URL must use HTTP(S) without credentials')
}
const confinedApiUrl = new URL(`${annotationAppOrigin}/`)
confinedApiUrl.pathname = configuredApiUrl.pathname
confinedApiUrl.search = ''
confinedApiUrl.hash = ''
if (confinedApiUrl.origin !== annotationAppOrigin) {
  throw new TypeError('annotation API base URL must stay on the annotation app origin')
}
const baseURL = confinedApiUrl.toString().replace(/\/$/, '')

const http = axios.create({
  baseURL,
  timeout: 10000
})

http.interceptors.request.use((config) => {
  const token = getPortalContext().token
  if (token) {
    config.headers.set('X-Token', token)
  } else {
    config.headers.delete('X-Token')
  }
  return config
})

http.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(error)
)

function isAbsoluteUrl(value: string) {
  return /^https?:\/\//i.test(value)
}

export function resolveApiUrl(path: string) {
  if (isAbsoluteUrl(path)) {
    return path
  }

  return new URL(path, `${getAnnotationAppOrigin()}/`).toString()
}

export default http
