import type {
  DerivationChain,
  MaterialProvenance,
  ProvenanceCreate,
  AdminCategoryOverview,
  AdminJob,
  AdminOverview,
  AdminReleaseCheck,
  AdminReviewQueue,
  AdminWorkspace,
  Category,
  CorrectionRecord,
  EvaluationJob,
  ExportFormat,
  ExportJob,
  ExtractionJob,
  Identity,
  RetrievalEnvelope,
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

  me(): Promise<Identity> {
    return this.request<Identity>('/api/v1/auth/me')
  }

  adminOverview(): Promise<AdminOverview> {
    return this.request<AdminOverview>('/api/v1/admin/overview')
  }

  materialProvenance(categoryId: string): Promise<{ items: MaterialProvenance[] }> {
    return this.request(`/api/v1/admin/categories/${encodeURIComponent(categoryId)}/material-provenance`)
  }

  confirmMaterial(categoryId: string, body: ProvenanceCreate): Promise<Pick<MaterialProvenance, 'id' | 'status'>> {
    return this.request(`/api/v1/admin/categories/${encodeURIComponent(categoryId)}/material-provenance`, { method: 'POST', body })
  }

  revokeMaterial(recordId: string): Promise<{ id: string; status: string }> {
    return this.request(`/api/v1/admin/material-provenance/${encodeURIComponent(recordId)}/revoke`, { method: 'POST' })
  }

  derivationChain(snapshotId: string): Promise<DerivationChain> {
    return this.request(`/api/v1/admin/snapshots/${encodeURIComponent(snapshotId)}/derivation-chain`)
  }

  adminCategories(): Promise<{ items: AdminCategoryOverview[] }> {
    return this.request('/api/v1/admin/categories')
  }

  adminWorkspace(categoryId: string, snapshotId?: string): Promise<AdminWorkspace> {
    const query = snapshotId ? `?snapshot_id=${encodeURIComponent(snapshotId)}` : ''
    return this.request<AdminWorkspace>(`/api/v1/admin/categories/${categoryId}${query}`)
  }

  adminJobs(params: { categoryId?: string; status?: string; limit?: number } = {}): Promise<{
    total: number
    items: AdminJob[]
  }> {
    const query = new URLSearchParams()
    if (params.categoryId) query.set('category_id', params.categoryId)
    if (params.status) query.set('status', params.status)
    query.set('limit', String(params.limit ?? 30))
    return this.request(`/api/v1/admin/jobs?${query.toString()}`)
  }

  createCategory(payload: { code: string; name: string }): Promise<Category> {
    return this.request<Category>('/api/v1/categories', { method: 'POST', body: payload })
  }

  uploadSource(categoryId: string, file: File): Promise<{ snapshot_id: string; job_id: string }> {
    const form = new FormData()
    form.set('file', file)
    return this.request(`/api/v1/categories/${categoryId}/source`, { method: 'POST', form })
  }

  uploadSourceWithProgress(
    categoryId: string,
    file: File,
    onProgress: (percent: number) => void,
  ): Promise<{ snapshot_id: string; job_id: string }> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${API_BASE}/api/v1/categories/${categoryId}/source`)
      xhr.setRequestHeader('Authorization', `Bearer ${this.token}`)
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)))
        }
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as { snapshot_id: string; job_id: string })
          } catch {
            reject(new ApiError(xhr.status, 'INVALID_RESPONSE', '服务端响应无法解析'))
          }
          return
        }
        let code = `HTTP_${xhr.status}`
        let message = `请求失败（HTTP ${xhr.status}）`
        try {
          const data = JSON.parse(xhr.responseText) as {
            error_code?: string
            message?: string
          }
          if (data.error_code) code = data.error_code
          if (data.message) message = data.message
        } catch {
          // Nginx 413 等非 JSON 响应保留 HTTP 状态，由前端映射为可读说明。
        }
        if (xhr.status === 401) this.onUnauthorized?.()
        reject(new ApiError(xhr.status, code, message))
      }
      xhr.onerror = () =>
        reject(new ApiError(0, 'NETWORK_ERROR', '上传网络中断，请检查连接后重试'))
      xhr.onabort = () =>
        reject(new ApiError(0, 'UPLOAD_ABORTED', '上传已取消'))
      const form = new FormData()
      form.set('file', file)
      xhr.send(form)
    })
  }

  buildSnapshot(snapshotId: string): Promise<{ snapshot_id: string; job_id: string }> {
    return this.request(`/api/v1/snapshots/${snapshotId}/build`, { method: 'POST' })
  }

  deriveDocumentStaging(
    categoryId: string,
  ): Promise<{ snapshot_id: string; derived_from_snapshot_id: string; documents_copied: number }> {
    return this.request(`/api/v1/categories/${categoryId}/document-staging-snapshots`, {
      method: 'POST',
    })
  }

  uploadAdminDocument(
    categoryId: string,
    snapshotId: string,
    file: File,
  ): Promise<{ document_id: string; job_id: string; snapshot_id: string }> {
    const form = new FormData()
    form.set('snapshot_id', snapshotId)
    form.set('file', file)
    return this.request(`/api/v1/categories/${categoryId}/documents`, {
      method: 'POST',
      form,
    })
  }

  replaceAdminDocument(
    snapshotId: string,
    documentId: string,
    file: File,
  ): Promise<{ document_id: string; job_id: string }> {
    const form = new FormData()
    form.set('file', file)
    return this.request(`/api/v1/snapshots/${snapshotId}/documents/${documentId}/replace`, {
      method: 'POST',
      form,
    })
  }

  deleteAdminDocument(
    snapshotId: string,
    documentId: string,
  ): Promise<{ document_id: string; status: string }> {
    return this.request(`/api/v1/snapshots/${snapshotId}/documents/${documentId}`, {
      method: 'DELETE',
    })
  }

  reparseAdminDocument(
    snapshotId: string,
    documentId: string,
  ): Promise<{ document_id: string; job_id: string }> {
    return this.request(`/api/v1/snapshots/${snapshotId}/documents/${documentId}/reparse`, {
      method: 'POST',
    })
  }

  discoverDocuments(snapshotId: string): Promise<{ job_id: string; run_id: string; mode: string }> {
    return this.request(`/api/v1/snapshots/${snapshotId}/discover-documents`, { method: 'POST' })
  }

  investigate(
    snapshotId: string,
    payload: {
      module_ids: string[]
      data_classification: 'synthetic_public' | 'enterprise'
      investigation_mode: 'bounded' | 'explore' | 'autonomous'
      capability_limit: number
      include_equivalence: boolean
    },
  ): Promise<{ job_id: string; module_count: number }> {
    return this.request(`/api/v1/snapshots/${snapshotId}/investigate`, {
      method: 'POST',
      body: payload,
    })
  }

  retrieval(
    categoryId: string,
    snapshotId: string,
    payload: Record<string, unknown>,
  ): Promise<RetrievalEnvelope> {
    return this.request(
      `/api/v1/retrieval/${categoryId}?snapshot_id=${encodeURIComponent(snapshotId)}`,
      { method: 'POST', body: payload },
    )
  }

  registerEvidence(
    snapshotId: string,
    payload: {
      kind: 'source' | 'document'
      file_id?: string
      segment_id?: string
      start_line?: number
      end_line?: number
      expected_sha256: string
      supported_statement: string
    },
  ): Promise<{ evidence_id: string }> {
    return this.request(`/api/v1/snapshots/${snapshotId}/review-evidence`, {
      method: 'POST',
      body: payload,
    })
  }

  adminReviewQueue(snapshotId: string, limit = 100): Promise<AdminReviewQueue> {
    return this.request(`/api/v1/admin/snapshots/${snapshotId}/review-queue?limit=${limit}`)
  }

  adminClaim(claimId: string): Promise<{
    id: string
    snapshot_id: string
    original_model_output: Record<string, unknown>
    machine_declaration_type: string
    latest_revision: Record<string, unknown> | null
    review_events: Array<Record<string, unknown>>
  }> {
    return this.request(`/api/v1/admin/capabilities/${claimId}`)
  }

  validateReview(claimId: string, payload: Record<string, unknown>): Promise<{
    eligible_for_human_acceptance: boolean
    errors: string[]
    dependency_gaps: Array<Record<string, unknown>>
  }> {
    return this.request(`/api/v1/capabilities/${claimId}/validate`, {
      method: 'POST',
      body: payload,
    })
  }

  submitCapabilityReview(
    claimId: string,
    payload: Record<string, unknown>,
  ): Promise<{ id: string; number: number; decision: string }> {
    return this.request(`/api/v1/capabilities/${claimId}/review`, {
      method: 'POST',
      body: payload,
    })
  }

  adminReleaseCheck(snapshotId: string): Promise<AdminReleaseCheck> {
    return this.request(`/api/v1/admin/snapshots/${snapshotId}/release-check`)
  }

  publishSnapshot(
    snapshotId: string,
    payload: {
      revision_ids: string[]
      expected_active_publication_id: string | null
      acknowledge_coverage_gaps: boolean
      comment: string
    },
  ): Promise<{ publication_id: string; snapshot_id: string; payload_hash: string }> {
    return this.request(`/api/v1/snapshots/${snapshotId}/publish`, {
      method: 'POST',
      body: payload,
    })
  }

  rollbackPublication(
    categoryId: string,
    payload: {
      publication_id: string
      expected_active_publication_id: string | null
      comment: string
    },
  ): Promise<{ publication_id: string; snapshot_id: string }> {
    return this.request(`/api/v1/categories/${categoryId}/rollback`, {
      method: 'POST',
      body: payload,
    })
  }

  publications(categoryId: string): Promise<
    Array<{
      publication_id: string
      previous_publication_id: string | null
      action: string
      actor: string
      comment: string
    }>
  > {
    return this.request(`/api/v1/categories/${categoryId}/publications`)
  }

  adminAudit(categoryId: string): Promise<{ items: Array<Record<string, unknown>> }> {
    return this.request(`/api/v1/admin/audit?category_id=${encodeURIComponent(categoryId)}&limit=100`)
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
