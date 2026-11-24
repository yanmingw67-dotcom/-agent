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

export interface Identity {
  user_id: string
  role: 'admin' | 'reader' | 'principal' | string
  source: string
  is_admin: boolean
  auth_mode: string
  sso_ready: boolean
}

export interface AdminSnapshot {
  id: string
  category_id: string
  status: string
  index_status: string
  source_sha256: string | null
  derived_from_snapshot_id: string | null
  derivation_type: string | null
  data_classification: string | null
  created_at: string | null
}

export interface AdminJob {
  id: string
  category_id: string
  category_name: string | null
  snapshot_id: string | null
  job_type: string
  stage: string
  status: TaskStatus | string
  error_code: string | null
  attempt: number
  metrics: Record<string, unknown>
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export interface AdminDocument {
  id: string
  snapshot_id: string
  category_id: string
  filename: string
  sha256: string
  size: number
  parse_status: string
  error_code: string | null
  parser_version: string | null
  parse_details: Record<string, unknown>
  origin: Record<string, unknown>
  segment_count: number
  data_classification: string
}

export interface MaterialProvenance {
  id: string
  category_id: string
  snapshot_id: string
  document_id: string | null
  material_type: 'source_code' | 'technical_document'
  content_sha256: string
  data_classification: string
  declared_origin: string
  usage_scope: string
  confirmation_reference: string
  confirmed_by: string
  status: string
  created_at: string | null
  revoked_at: string | null
  revoked_by: string | null
}

export interface ProvenanceCreate {
  snapshot_id: string
  material_type: 'source_code' | 'technical_document'
  document_id?: string
  declared_origin: string
  usage_scope: string
  confirmation_reference: string
}

export interface DerivationChain {
  snapshot_id: string
  category_id: string
  root_snapshot_id: string | null
  valid: boolean
  error_code: string | null
  chain: Omit<AdminSnapshot, 'created_at'>[]
}

export interface AdminReviewSummary {
  total: number
  accepted: number
  unreviewed: number
  rejected: number
  returned: number
  modified: number
  merged: number
  pending: number
}

export interface AdminCategoryOverview {
  id: string
  code: string
  name: string
  knowledge_ready: boolean
  active_snapshot_id: string | null
  active_publication_id: string | null
  latest_snapshot: AdminSnapshot | null
  latest_job: AdminJob | null
  document_count: number
  pending_review_count: number
  published_capability_count: number
  published_at: string | null
  created_at: string | null
}

export interface AdminOverview {
  summary: {
    category_count: number
    ready_category_count: number
    pending_review_count: number
    failed_job_count: number
    unpublished_snapshot_count: number
    publication_count: number
  }
  categories: AdminCategoryOverview[]
  recent_jobs: AdminJob[]
  recent_publications: Array<{
    publication_id: string
    category_id: string
    category_name: string | null
    snapshot_id: string
    capability_count: number
    published_by: string
    created_at: string | null
  }>
  model_authorization: ModelAuthorization
  limits: {
    document_types: string[]
    document_only_build_supported: boolean
    document_only_note: string
  }
}

export interface ModelAuthorization {
  server_side_authorization_required: boolean
  frontend_can_override: boolean
  builder: {
    model_configured: boolean
    model_name: string | null
    data_classification: string
    reason_code: string | null
    active_authorization: { expires_at: string | null } | null
    allow_synthetic: boolean
    allow_enterprise: boolean
    document_discovery_mode: string
    status: string
  }
  tender: { model_mode: string; queue_enabled: boolean }
  evaluation: { model_mode: string; queue_enabled: boolean }
}

export interface AdminWorkspace {
  category: {
    id: string
    code: string
    name: string
    knowledge_ready: boolean
    active_snapshot_id: string | null
    active_publication_id: string | null
  }
  working_snapshot: AdminSnapshot | null
  snapshots: AdminSnapshot[]
  jobs: AdminJob[]
  documents: AdminDocument[]
  active_publication: {
    publication_id: string
    snapshot_id: string
    payload_hash: string
    capability_count: number
    published_at: string | null
    published_by: string
  } | null
  review: AdminReviewSummary
  document_discovery: {
    run_id: string
    status: string
    mode: string
    error_code: string | null
    metrics: Record<string, unknown>
  } | null
  model_authorization: ModelAuthorization
  capabilities: {
    can_derive_documents: boolean
    source_required: boolean
    derive_blocker: string | null
  }
}

export interface AdminReviewClaim {
  claim_id: string
  name: string
  status: string
  origin: Record<string, unknown>
  draft: ReviewedContent
  latest_revision: {
    id: string
    number: number
    decision: string
    declaration_type: string
    content: ReviewedContent
    assertions_count: number
    gate: { errors?: string[]; dependency_gaps?: Array<Record<string, unknown>> }
    availability_basis: string | null
    product_scope_confirmed: boolean
    proof_kind: string | null
  } | null
}

export interface ReviewedContent {
  name: string
  action: string
  target: string
  business_goal: string
  implementation_mechanism: string
  supported_scope: string[]
  conditions: string[]
  limitations: string[]
  [key: string]: string | string[]
}

export interface AdminReviewQueue {
  snapshot_id: string
  snapshot: AdminSnapshot
  summary: AdminReviewSummary
  accepted_revision_ids: string[]
  truncated: boolean
  next_cursor: number | null
  items: AdminReviewClaim[]
}

export interface AdminReleaseCheck {
  snapshot_id: string
  category_id: string
  expected_active_publication_id: string | null
  accepted_revision_ids: string[]
  items: Array<{
    claim_id: string
    revision_id: string
    name: string
    decision: string
    declaration_type: string
    gate_errors: string[]
    blockers: string[]
    published: boolean
  }>
  blocking: Array<{
    claim_id: string | null
    revision_id: string | null
    name?: string
    errors: string[]
  }>
  publishable: boolean
  coverage: Record<string, unknown>
  coverage_acknowledgement_required: boolean
  relations_available: boolean
}

export interface EvidenceReceipt {
  evidence_id: string
  snapshot_id: string
  source_ref: Record<string, string>
  locator: Record<string, string | number>
  quote_hash: string
  warnings: string[]
}

export interface RetrievalItem {
  id: string | null
  file_id: string | null
  symbol_id: string | null
  document_id: string | null
  path: string | null
  name: string | null
  kind: string | null
  sha256: string | null
  start_line: number | null
  end_line: number | null
  snippet: string | null
  locator: Record<string, string | number>
  status: string | null
}

export interface RetrievalEnvelope {
  snapshot_id: string
  tool: string
  results: RetrievalItem[]
  truncated: boolean
  coverage_incomplete: boolean
  warnings: string[]
}

export interface ReviewAssertionPayload {
  field: string
  text: string
  evidence_ids: string[]
  semantic_decision: 'supported' | 'unverified' | 'contradicted'
  rationale: string
  dependencies: Array<{ file_id: string; symbol_id: string; evidence_ids: string[] }>
  implementation_evidence_ids: string[]
  proof_basis: 'source' | 'document' | 'mixed' | null
  document_scope_confirmed: boolean
}
