import type {
  Category,
  CorrectionRecord,
  EvaluationJob,
  ExportFormat,
  ExportJob,
  ExtractionJob,
  ReviewPayload,
  ReviewResponse,
  ResultsPayload,
  WorkbenchList,
} from './types'

export const API_BASE = (import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000').replace(
  /\/$/,
  '',
)

export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

type RequestOptions = {
  method?: string
  body?: unknown
  form?: FormData
  signal?: AbortSignal
}

async function parseError(response: Response): Promise<ApiError> {
  let code = `HTTP_${response.status}`
  let message = `请求失败（HTTP ${response.status}）`
  try {
    const data = (await response.json()) as {
      error_code?: string
      message?: string
    }
    if (data.error_code) code = data.error_code
    if (data.message) message = data.message
  } catch {
    // 非 JSON 错误响应保留 HTTP 状态说明。
  }
  return new ApiError(response.status, code, message)
}

export class ApiClient {
  token: string
  onUnauthorized?: () => void

  constructor(token: string, onUnauthorized?: () => void) {
    this.token = token
    this.onUnauthorized = onUnauthorized
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.token}`,
    }
    let body: BodyInit | undefined
    if (options.form) {
      body = options.form
    } else if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(options.body)
    }
    const response = await fetch(`${API_BASE}${path}`, {
      method: options.method ?? 'GET',
      headers,
      body,
      signal: options.signal,
    })
    if (!response.ok) {
      if (response.status === 401) this.onUnauthorized?.()
      throw await parseError(response)
    }
    if (response.status === 204) {
      return undefined as T
    }
    return (await response.json()) as T
  }

  listCategories(): Promise<Category[]> {
    return this.request<Category[]>('/api/v1/categories')
  }

  listTasks(params: {
    status?: string
    categoryId?: string
    limit?: number
    offset?: number
  } = {}): Promise<WorkbenchList> {
    const query = new URLSearchParams()
    if (params.status) query.set('status', params.status)
    if (params.categoryId) query.set('category_id', params.categoryId)
    query.set('limit', String(params.limit ?? 50))
    query.set('offset', String(params.offset ?? 0))
    return this.request<WorkbenchList>(`/api/v1/tender/workbench/tasks?${query.toString()}`)
  }

  getResults(jobId: string): Promise<ResultsPayload> {
    return this.request<ResultsPayload>(`/api/v1/tender/workbench/tasks/${jobId}/results`)
  }

  getEvaluation(jobId: string): Promise<EvaluationJob> {
    return this.request<EvaluationJob>(`/api/v1/tender/workbench/tasks/${jobId}`)
  }

  listExtractions(limit = 20): Promise<{ jobs: ExtractionJob[] }> {
    return this.request<{ jobs: ExtractionJob[] }>(
      `/api/v1/tender/extractions?limit=${limit}`,
    )
  }

  getExtraction(jobId: string): Promise<ExtractionJob> {
    return this.request<ExtractionJob>(`/api/v1/tender/extractions/${jobId}`)
  }

  createTextExtraction(payload: {
    category_id: string
    text: string
    title?: string
  }): Promise<{ job_id: string; status: string }> {
    return this.request('/api/v1/tender/extractions', {
      method: 'POST',
      body: payload,
    })
  }

  uploadExtraction(categoryId: string, file: File): Promise<{ job_id: string; status: string }> {
    const form = new FormData()
    form.set('category_id', categoryId)
    form.set('file', file)
    return this.request('/api/v1/tender/extractions/upload', {
      method: 'POST',
      form,
    })
  }

  editParameter(
    extractionId: string,
    parameterId: string,
    payload: { parameter_name?: string; original_text?: string },
  ): Promise<unknown> {
    return this.request(
      `/api/v1/tender/extractions/${extractionId}/parameters/${parameterId}`,
      { method: 'PATCH', body: payload },
    )
  }

  splitParameter(
    extractionId: string,
    parameterId: string,
    parts: Array<{ parameter_name?: string; original_text: string }>,
  ): Promise<unknown> {
    return this.request(
      `/api/v1/tender/extractions/${extractionId}/parameters/${parameterId}/split`,
      { method: 'POST', body: { parts } },
    )
  }

  mergeParameters(
    extractionId: string,
    parameterIds: string[],
    parameterName?: string,
  ): Promise<unknown> {
    return this.request(`/api/v1/tender/extractions/${extractionId}/merge`, {
      method: 'POST',
      body: { parameter_ids: parameterIds, parameter_name: parameterName || undefined },
    })
  }

  addParameter(
    extractionId: string,
    payload: { parameter_name?: string; original_text: string },
  ): Promise<unknown> {
    return this.request(`/api/v1/tender/extractions/${extractionId}/parameters`, {
      method: 'POST',
      body: payload,
    })
  }

  listCorrections(
    extractionId: string,
    parameterId?: string,
  ): Promise<{ job_id: string; items: CorrectionRecord[] }> {
    const query = new URLSearchParams()
    if (parameterId) query.set('parameter_id', parameterId)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return this.request(`/api/v1/tender/extractions/${extractionId}/corrections${suffix}`)
  }

  confirmParameter(extractionId: string, parameterId: string): Promise<unknown> {
    return this.request(
      `/api/v1/tender/extractions/${extractionId}/parameters/${parameterId}/confirm`,
      { method: 'POST' },
    )
  }

  createEvaluation(extractionId: string): Promise<{ job_id: string; status: string }> {
    return this.request('/api/v1/tender/evaluations', {
      method: 'POST',
      body: { extraction_job_id: extractionId },
    })
  }

  retryEvaluation(jobId: string): Promise<{ job_id: string; status: string }> {
    return this.request(`/api/v1/tender/evaluations/${jobId}/retry`, { method: 'POST' })
  }

  listReviews(jobId: string, parameterId: string): Promise<{ items: ReviewResponse['review_history'] }> {
    return this.request(
      `/api/v1/tender/workbench/tasks/${jobId}/parameters/${parameterId}/reviews`,
    )
  }

  submitReview(
    jobId: string,
    parameterId: string,
    payload: ReviewPayload,
  ): Promise<ReviewResponse> {
    return this.request(
      `/api/v1/tender/workbench/tasks/${jobId}/parameters/${parameterId}/review`,
      { method: 'POST', body: payload },
    )
  }

  listExports(jobId: string): Promise<{ job_id: string; items: ExportJob[] }> {
    return this.request(`/api/v1/tender/workbench/tasks/${jobId}/exports`)
  }

  createExport(jobId: string, format: ExportFormat): Promise<ExportJob> {
    return this.request(`/api/v1/tender/workbench/tasks/${jobId}/exports`, {
      method: 'POST',
      body: { format },
    })
  }

  getExport(exportId: string): Promise<ExportJob> {
    return this.request<ExportJob>(`/api/v1/tender/workbench/exports/${exportId}`)
  }

  refreshDownloadToken(
    exportId: string,
  ): Promise<{ export_id: string; download_token: string; download_expires_at: string }> {
    return this.request(`/api/v1/tender/workbench/exports/${exportId}/download-token`, {
      method: 'POST',
    })
  }

  async downloadExport(exportJob: ExportJob): Promise<void> {
    const request = (token: string) =>
      fetch(
        `${API_BASE}/api/v1/tender/workbench/exports/${exportJob.export_id}/download`,
        {
          headers: {
            Authorization: `Bearer ${this.token}`,
            // 下载凭证只放在专用请求头，绝不拼进 URL、历史记录或日志。
            'X-Export-Token': token,
          },
        },
      )
    let response = await request(exportJob.download_token)
    if (!response.ok) {
      const failure = await parseError(response)
      if (failure.code === 'EXPORT_TOKEN_EXPIRED') {
        const fresh = await this.refreshDownloadToken(exportJob.export_id)
        response = await request(fresh.download_token)
      } else {
        if (response.status === 401) this.onUnauthorized?.()
        throw failure
      }
    }
    if (!response.ok) {
      if (response.status === 401) this.onUnauthorized?.()
      throw await parseError(response)
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = exportJob.filename || `评估结果.${exportJob.format}`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(url)
  }
}
