import axios from 'axios'

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 60000,
})

client.interceptors.response.use(
  (res) => res,
  (err) => {
    const message = err.response?.data?.detail || err.message || 'Request failed'
    return Promise.reject(new Error(message))
  }
)

const get  = (url, params) => client.get(url, { params }).then((r) => r.data)
const post = (url, body, params) => client.post(url, body, { params }).then((r) => r.data)
const del  = (url) => client.delete(url).then((r) => r.data)

// ── Reference ──────────────────────────────────────────────
export const getCategories = () => get('/analytics/categories')
export const getRegions    = () => get('/analytics/regions')

// ── Analytics ─────────────────────────────────────────────
export const getSummary   = () => get('/analytics/summary')
export const getRevenue   = (params) => get('/analytics/revenue', params)
export const getBreakdown = (params) => get('/analytics/breakdown', params)

// ── Data quality ──────────────────────────────────────────
export const getSourceHealth = () => get('/dq/sources')
export const getDqSummary    = () => get('/dq/summary')

// ── Forecasting ───────────────────────────────────────────
export const getModels         = (params) => get('/forecasting/models', params)
export const getForecasts      = (params) => get('/forecasting/forecasts', params)
export const triggerTraining   = (body, runSync = true) =>
  post('/forecasting/train', body, { run_sync: runSync })
export const triggerPrediction = (body, runSync = true) =>
  post('/forecasting/predict/batch', body, { run_sync: runSync })
export const getTrainingRuns   = (params) => get('/forecasting/runs', params)
export const compareModels     = (params) => get('/forecasting/models/compare', params)
export const getAccuracyTrend  = (params) => get('/forecasting/accuracy', params)

// ── AI Insights ───────────────────────────────────────────
export const getTrendSummary        = (body) => post('/insights/trend', body)
export const getRootCause           = (body) => post('/insights/root-cause', body)
export const getForecastExplanation = (body) => post('/insights/forecast/explain', body)
export const getDrivers             = (body) => post('/insights/drivers', body)
export const getExecutiveSummary    = (body) => post('/insights/executive-summary', body)
export const getInsightLog          = (params) => get('/insights/log', params)

// ── Conversational analytics ──────────────────────────────
export const createSession = (body) => post('/conversation/sessions', body)
export const listSessions  = () => get('/conversation/sessions')
export const getSession    = (id) => get(`/conversation/sessions/${id}`)
export const deleteSession = (id) => del(`/conversation/sessions/${id}`)
export const askQuestion   = (body) => post('/conversation/ask', body)

// ── Reports ───────────────────────────────────────────────
const downloadBlob = async (url, params, filename) => {
  const res = await client.get(url, { params, responseType: 'blob' })
  const blobUrl = window.URL.createObjectURL(new Blob([res.data]))
  const link = document.createElement('a')
  link.href = blobUrl
  const disposition = res.headers['content-disposition']
  const match = disposition && disposition.match(/filename="?([^"]+)"?/)
  link.download = (match && match[1]) || filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(blobUrl)
}

export const downloadRevenueCsv   = (params) => downloadBlob('/reports/csv/revenue', params, 'revenue.csv')
export const downloadCategoryCsv  = (params) => downloadBlob('/reports/csv/category-breakdown', params, 'category_breakdown.csv')
export const downloadRegionCsv    = (params) => downloadBlob('/reports/csv/region-breakdown', params, 'region_breakdown.csv')
export const downloadForecastCsv  = (params) => downloadBlob('/reports/csv/forecast', params, 'forecast.csv')
export const downloadExecutivePdf = (params) => downloadBlob('/reports/pdf/executive-summary', params, 'executive_report.pdf')

export default client
