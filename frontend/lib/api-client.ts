import axios, { AxiosHeaders } from 'axios'
import { supabase } from '@/lib/supabase'

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

let supabaseAccessToken: string | null = null

if (typeof window !== 'undefined') {
  supabase.auth
    .getSession()
    .then(({ data }) => {
      supabaseAccessToken = data.session?.access_token ?? null
    })
    .catch(() => {
      supabaseAccessToken = null
    })

  supabase.auth.onAuthStateChange((_event, session) => {
    supabaseAccessToken = session?.access_token ?? null
  })
}

apiClient.interceptors.request.use((config) => {
  if (typeof window === 'undefined') return config
  if (!supabaseAccessToken) return config

  const existing =
    config.headers instanceof AxiosHeaders
      ? config.headers.get('Authorization')
      : (config.headers as any)?.Authorization

  if (existing) return config

  if (!config.headers) {
    config.headers = new AxiosHeaders()
  }

  if (config.headers instanceof AxiosHeaders) {
    config.headers.set('Authorization', `Bearer ${supabaseAccessToken}`)
  } else {
    ;(config.headers as any).Authorization = `Bearer ${supabaseAccessToken}`
  }

  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (typeof window === 'undefined') return Promise.reject(error)

    const status = error?.response?.status
    const originalConfig = error?.config

    if (status !== 401 || !originalConfig) {
      return Promise.reject(error)
    }

    if ((originalConfig as any).__financesumRetriedAuth) {
      return Promise.reject(error)
    }

    ;(originalConfig as any).__financesumRetriedAuth = true

    try {
      const { data } = await supabase.auth.refreshSession()
      const refreshed = data?.session?.access_token ?? null
      if (!refreshed) return Promise.reject(error)

      supabaseAccessToken = refreshed

      if (!originalConfig.headers) {
        originalConfig.headers = new AxiosHeaders()
      }

      if (originalConfig.headers instanceof AxiosHeaders) {
        originalConfig.headers.set('Authorization', `Bearer ${refreshed}`)
      } else {
        ;(originalConfig.headers as any).Authorization = `Bearer ${refreshed}`
      }

      return apiClient.request(originalConfig)
    } catch {
      return Promise.reject(error)
    }
  }
)

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

export type SummaryExportPayload = {
  format: 'pdf' | 'docx'
  title?: string
  summary: string
  filing_type?: string
  filing_date?: string
  generated_at?: string
}

export type AnalysisExportPayload = {
  format: 'pdf' | 'docx'
  title?: string
  summary: string
  ticker?: string
  company_name?: string
  analysis_date?: string
  generated_at?: string
  filing_type?: string
  filing_date?: string
}

export type AnalysisRunOptions = {
  includePersonas?: string[]
  targetLength?: number
  complexity?: string
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
  
  listCompanyFilings: (
    companyId: string,
    opts?: { filingType?: string; limit?: number; offset?: number },
  ) =>
    apiClient.get(`/api/v1/filings/company/${companyId}`, {
      params: {
        filing_type: opts?.filingType,
        limit: opts?.limit,
        offset: opts?.offset,
      },
    }),
  
  parseFiling: (filingId: string) =>
    apiClient.post(`/api/v1/filings/${filingId}/parse`),
  
  summarizeFiling: (filingId: string, preferences?: FilingSummaryPreferencesPayload) =>
    apiClient.post(`/api/v1/filings/${filingId}/summary`, preferences ?? {}),

  getSummaryProgress: (filingId: string) =>
    apiClient.get(`/api/v1/filings/${filingId}/progress`),

  exportSummary: (filingId: string, payload: SummaryExportPayload) =>
    apiClient.post(`/api/v1/filings/${filingId}/summary/export`, payload, { responseType: 'blob' }),

  getSpotlightKpi: (filingId: string, opts?: { refresh?: boolean }) =>
    apiClient.get(`/api/v1/filings/${filingId}/spotlight`, {
      params: opts?.refresh ? { refresh: true } : undefined,
    }),
}

// Analysis API
export const analysisApi = {
  run: (companyId: string, filingIds?: string[], options?: AnalysisRunOptions) =>
    apiClient.post('/api/v1/analysis/run', {
      company_id: companyId,
      filing_ids: filingIds,
      analysis_options: {
        include_personas: options?.includePersonas,
        target_length: options?.targetLength,
        complexity: options?.complexity,
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

  exportAnalysis: (analysisId: string, payload: AnalysisExportPayload) =>
    apiClient.post(`/api/v1/analysis/${analysisId}/export`, payload, { responseType: 'blob' }),
}

// Dashboard API
export const dashboardApi = {
  overview: (params?: { tz_offset_minutes?: number }) =>
    apiClient.get('/api/v1/dashboard/overview', { params }),
}

const authConfig = (accessToken?: string) =>
  accessToken ? { headers: { Authorization: `Bearer ${accessToken}` } } : undefined

// Billing API
export const billingApi = {
  getConfig: () => apiClient.get('/api/v1/billing/config'),

  createCheckoutSession: (
    payload?: { plan?: 'pro'; success_path?: string; cancel_path?: string },
    accessToken?: string,
  ) => apiClient.post('/api/v1/billing/create-checkout-session', payload ?? { plan: 'pro' }, authConfig(accessToken)),

  createPortalSession: (accessToken?: string) =>
    apiClient.post('/api/v1/billing/create-portal-session', undefined, authConfig(accessToken)),

  getSubscription: (accessToken?: string) => apiClient.get('/api/v1/billing/subscription', authConfig(accessToken)),

  getUsage: (accessToken?: string) => apiClient.get('/api/v1/billing/usage', authConfig(accessToken)),

  cancelSubscription: (accessToken?: string) =>
    apiClient.post('/api/v1/billing/cancel', undefined, authConfig(accessToken)),

  syncCheckoutSession: (sessionId: string, accessToken?: string) =>
    apiClient.post('/api/v1/billing/sync', { session_id: sessionId }, authConfig(accessToken)),
}

export default apiClient
