import axios from 'axios'

// Absolute backend URL (used for generating links outside the proxy)
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const API_PROXY_BASE = (process.env.NEXT_PUBLIC_API_PROXY_BASE ?? '/api/backend').trim()
const API_CLIENT_BASE_URL = API_PROXY_BASE.length > 0 ? API_PROXY_BASE : API_URL

export const API_BASE_URL = API_URL

export const apiClient = axios.create({
  baseURL: API_CLIENT_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

export type FilingSummaryPreferencesPayload = {
  mode?: 'default' | 'custom'
  investor_focus?: string
  focus_areas?: string[]
  tone?: string
  detail_level?: string
  output_style?: string
  target_length?: number
  complexity?: string
  health_rating?: {
    enabled?: boolean
    framework?: string
    primary_factor_weighting?: string
    risk_tolerance?: string
    analysis_depth?: string
    display_style?: string
  }
}

// Company API
export const companyApi = {
  lookup: (query: string) => 
    apiClient.post('/api/v1/companies/lookup', { query }),
  
  getCompany: (companyId: string, params?: { ticker?: string | null }) => 
    apiClient.get(`/api/v1/companies/${companyId}`, {
      params: params?.ticker ? { ticker: params.ticker } : undefined,
    }),
  
  listCompanies: (params?: { limit?: number; offset?: number }) => 
    apiClient.get('/api/v1/companies', { params }),
}

// Filings API
export const filingsApi = {
  fetch: (companyId: string, filingTypes?: string[], maxHistoryYears?: number) =>
    apiClient.post('/api/v1/filings/fetch', {
      company_id: companyId,
      filing_types: filingTypes || ['10-K', '10-Q'],
      max_history_years: maxHistoryYears || 40,
    }),
  
  getFiling: (filingId: string) =>
    apiClient.get(`/api/v1/filings/${filingId}`),
  
  listCompanyFilings: (companyId: string, filingType?: string) =>
    apiClient.get(`/api/v1/filings/company/${companyId}`, {
      params: { filing_type: filingType },
    }),
  
  parseFiling: (filingId: string) =>
    apiClient.post(`/api/v1/filings/${filingId}/parse`),
  
  summarizeFiling: (filingId: string, preferences?: FilingSummaryPreferencesPayload) =>
    apiClient.post(`/api/v1/filings/${filingId}/summary`, preferences ?? undefined),

  getSummaryProgress: (filingId: string) =>
    apiClient.get(`/api/v1/filings/${filingId}/progress`),
}

// Analysis API
export const analysisApi = {
  run: (companyId: string, filingIds?: string[], includePersonas?: string[]) =>
    apiClient.post('/api/v1/analysis/run', {
      company_id: companyId,
      filing_ids: filingIds,
      analysis_options: {
        include_personas: includePersonas,
      },
    }),
  
  getAnalysis: (analysisId: string) =>
    apiClient.get(`/api/v1/analysis/${analysisId}`),
  
  getAnalysisStatus: (analysisId: string) =>
    apiClient.get(`/api/v1/analysis/${analysisId}/status`),
  
  listCompanyAnalyses: (companyId: string) =>
    apiClient.get(`/api/v1/analysis/company/${companyId}`),
  
  getTaskStatus: (taskId: string) =>
    apiClient.get(`/api/v1/analysis/task/${taskId}`),

  deleteAnalysis: (analysisId: string) =>
    apiClient.delete(`/api/v1/analysis/${analysisId}`),
}

// Dashboard API
export const dashboardApi = {
  overview: () => apiClient.get('/api/v1/dashboard/overview'),
}

export default apiClient
