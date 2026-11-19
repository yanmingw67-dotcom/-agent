export type Verdict = '支持' | '不支持' | '无法确认'
export type ReviewAction = 'accept' | 'adjust' | 'reject' | 'reassess'
export type TaskStatus = 'queued' | 'running' | 'succeeded' | 'partial' | 'failed' | 'cancelled'
export type ExportFormat = 'docx' | 'xlsx'

export interface Category {
  id: string
  code: string
  name: string
  knowledge_ready: boolean
}

export interface Counts {
  支持: number
  不支持: number
  无法确认: number
}

export interface ExtractionSummary {
  extraction_job_id: string
  status: string
  coverage_status: string
  extracted_count: number
  skipped_count: number
  ambiguous_count: number
  review_required_count: number
  error_code: string | null
}

export interface TaskWarning {
  code: string
  message: string
}

export interface ReviewSummary {
  parameter_count: number
  reviewed: number
  pending: number
  accepted: number
  adjusted: number
  rejected: number
  reassessment_required: number
  requires_reevaluation: number
  human_final_counts: Counts
}

export interface EvaluationJob {
  job_id: string
  status: TaskStatus
  extraction_job_id: string
  publication_id: string
  published_snapshot_id: string
  publication_hash: string
  parameter_count: number
  atomic_count: number
  counts: Counts
  gap_count: number
  error_code: string | null
  checkpoint: { completed: number; total: number }
  model: Record<string, unknown>
  metrics: Record<string, unknown>
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  category_id?: string
  category_name?: string
  failed_count?: number
  result_count?: number
  reviewed_count?: number
  pending_review_count?: number
  exportable?: boolean
  review_summary?: ReviewSummary
  extraction?: ExtractionSummary | null
  warnings?: TaskWarning[]
  publication_context_error?: string | null
  evidence_context_available?: boolean
  evidence_context_note?: string
}

export interface WorkbenchList {
  total: number
  limit: number
  offset: number
  items: EvaluationJob[]
}

export type Locator = Record<string, string | number>

export interface Evidence {
  evidence_id: string
  capability_id: string
  revision_id: string
  kind: string
  source_ref: string
  locator: Locator
  source_hash: string
  quote_hash: string
  quote: string
  verification_state: string
  source_file?: string | null
  source_type_label?: string
  locator_text?: string
  capability_text?: string
  capability_action?: string
  capability_object?: string
  supported_scope_text?: string
  limitations_text?: string
  conditions_text?: string
  declaration_type?: string
  review_status?: string
  context_status?: 'available' | 'unavailable'
  context_note?: string | null
  used_for?: string[]
}

export interface AtomicRequirement {
  requirement_id: string
  order_index: number
  requirement_text: string
  necessary: boolean
  verdict: Verdict
  reason: string
  evidence_ids: string[]
  assessment_method: string
  details: Record<string, unknown>
  logical_relation: string
  mandatory_status: string
  is_mandatory: boolean
  threshold: string | number | null
  threshold_operator: string | null
  unit: string | null
  conditions: string[]
  conditions_text: string
  ambiguity: string | null
  evidence: Evidence[]
}

export interface HumanReview {
  review_id: string
  job_id: string
  parameter_id: string
  reviewer_id: string
  action: ReviewAction
  action_label: string
  review_status: string
  review_status_label: string
  auto_verdict: Verdict
  final_verdict: Verdict | null
  recognized: boolean
  requires_reevaluation: boolean
  comment: string
  adjust_reason: string
  created_at: string
  source?: string
}

export interface ParameterResult {
  parameter_id: string
  parameter_revision: number
  parameter_name: string
  original_text: string
  verdict: Verdict
  rationale: string
  counts: Counts
  evidence_ids: string[]
  error_code: string | null
  details: Record<string, unknown>
  order_index: number
  logic: Record<string, unknown>
  logic_reason: string
  logic_tree: unknown
  atomic_requirements: AtomicRequirement[]
  evidence: Evidence[]
  review: HumanReview | null
  review_status: string
  review_status_label: string
  human_final_verdict: Verdict | null
  human_final_verdict_label: string
  human_comment: string
  human_adjust_reason: string
  recognized: boolean | null
  requires_reevaluation: boolean
}

export interface ResultsPayload {
  job: EvaluationJob
  parameters: ParameterResult[]
}

export interface TenderAtomicRequirement {
  requirement_id: string
  original_text: string
  requirement_text?: string
  requirement_type: string
  logical_relation: string
  logical_group_id: string | null
  is_mandatory: boolean
  mandatory_status?: string
  threshold?: string | number | null
  threshold_operator?: string | null
  unit?: string | null
  conditions?: string[]
  ambiguity?: string | null
  source_span?: { start: number; end: number }
  parent_requirement_id?: string | null
  depth?: number
}

export interface TenderParameter {
  id: string
  order_index: number
  original_number: string | null
  parameter_name: string
  original_text: string
  normalized_text: string
  source_locator: Locator
  extraction_method: string
  extraction_confidence: string
  review_required: boolean
  atomic_requirements: TenderAtomicRequirement[]
  logic_tree?: Record<string, unknown>
  model_hints?: string[]
  status: string
  revision: number
  superseded_by?: string | null
}

export type CorrectionAction = 'edit' | 'split' | 'merge' | 'add' | 'confirm'

export interface CorrectionRecord {
  correction_id: string
  job_id: string
  parameter_id: string | null
  actor: string
  action: CorrectionAction
  correlation_id: string | null
  before: {
    parameter_name?: string
    original_text?: string
    atomic_requirements?: TenderAtomicRequirement[]
    parameter_ids?: string[]
  }
  after: {
    parameter_name?: string
    original_text?: string
    atomic_requirements?: TenderAtomicRequirement[]
    parameter_ids?: string[]
  }
  created_at: string
}

export interface ExtractionJob {
  job_id: string
  status: TaskStatus
  input_type: string
  category_id: string
  original_filename: string | null
  original_file_hash: string | null
  extracted_count: number
  skipped_count: number
  ambiguous_count: number
  coverage_status: string
  coverage_incomplete: boolean
  coverage: Record<string, unknown> | null
  metrics: Record<string, unknown> | null
  error_code: string | null
  error_message?: string | null
  state_warnings?: string[]
  review_required: boolean
  parameters: TenderParameter[]
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ExportJob {
  export_id: string
  job_id: string
  format: ExportFormat
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  filename: string
  size_bytes: number | null
  parameter_count: number | null
  atomic_count: number | null
  evidence_count: number | null
  review_count: number | null
  summary: Record<string, unknown>
  error_code: string | null
  download_token: string
  download_expires_at: string | null
  download_expired: boolean
  download_url: string
  file_sha256: string | null
  integrity_available: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
  file_available?: boolean
}

export interface ReviewResponse {
  review: HumanReview
  auto_verdict: Verdict
  review_summary: ReviewSummary
  review_history: HumanReview[]
}

export interface ReviewPayload {
  action: ReviewAction
  final_verdict?: Verdict
  comment?: string
  adjust_reason?: string
  requires_reevaluation?: boolean
}
