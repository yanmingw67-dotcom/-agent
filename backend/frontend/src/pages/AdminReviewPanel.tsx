import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { Alert, Badge, Icon, Modal, Spinner } from '../components/ui'
import { shortHash } from '../format'
import type {
  AdminReviewClaim,
  AdminReviewQueue,
  RetrievalItem,
  ReviewAssertionPayload,
  ReviewedContent,
} from '../types'

type ContentForm = {
  name: string
  action: string
  target: string
  business_goal: string
  implementation_mechanism: string
  supported_scope: string
  conditions: string
  limitations: string
}

type FieldState = {
  evidenceIds: string[]
  semantic: 'supported' | 'unverified' | 'contradicted'
  rationale: string
  documentConfirmed: boolean
}

type PoolItem = {
  id: string
  kind: 'source' | 'document'
  label: string
  quote: string
  locator: string
}

const EMPTY_FORM: ContentForm = {
  name: '',
  action: '',
  target: '',
  business_goal: '',
  implementation_mechanism: '',
  supported_scope: '',
  conditions: '',
  limitations: '',
}

const SCALAR_FIELDS: Array<{ key: keyof ContentForm; label: string; hint: string }> = [
  { key: 'name', label: '能力名称', hint: '可审核的产品能力名称，不得写成调查计划。' },
  { key: 'action', label: '动作', hint: '产品实际执行的动作。' },
  { key: 'target', label: '对象', hint: '动作作用的产品对象或数据。' },
  { key: 'business_goal', label: '业务目标', hint: '客户可理解的业务价值。' },
  { key: 'implementation_mechanism', label: '实现机制', hint: '仅写证据支持的范围，不扩大承诺。' },
]

function listText(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => String(item)).join('\n') : ''
}

function toForm(content: unknown): ContentForm {
  if (!content || typeof content !== 'object') return { ...EMPTY_FORM }
  const value = content as Record<string, unknown>
  return {
    name: String(value.name ?? ''),
    action: String(value.action ?? ''),
    target: String(value.target ?? ''),
    business_goal: String(value.business_goal ?? ''),
    implementation_mechanism: String(value.implementation_mechanism ?? ''),
    supported_scope: listText(value.supported_scope),
    conditions: listText(value.conditions),
    limitations: listText(value.limitations),
  }
}

function splitLines(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function buildContent(form: ContentForm): ReviewedContent {
  return {
    name: form.name.trim(),
    action: form.action.trim(),
    target: form.target.trim(),
    business_goal: form.business_goal.trim(),
    implementation_mechanism: form.implementation_mechanism.trim(),
    supported_scope: splitLines(form.supported_scope),
    conditions: splitLines(form.conditions),
    limitations: splitLines(form.limitations),
  }
}

function flattenContent(content: ReviewedContent): Array<{ key: string; text: string; group: string }> {
  const result: Array<{ key: string; text: string; group: string }> = []
  for (const field of SCALAR_FIELDS) {
    result.push({ key: field.key, text: content[field.key] as string, group: field.label })
  }
  for (const key of ['supported_scope', 'conditions', 'limitations'] as const) {
    ;(content[key] as string[]).forEach((text, index) => {
      result.push({ key: `${key}.${index}`, text, group: key })
    })
  }
  return result
}

function defaultFieldState(): FieldState {
  return { evidenceIds: [], semantic: 'unverified', rationale: '', documentConfirmed: false }
}

export function AdminReviewPanel({
  api,
  categoryId,
  snapshotId,
  snapshotStatus,
  onChanged,
}: {
  api: ApiClient
  categoryId: string
  snapshotId: string
  snapshotStatus: string
  onChanged: () => void
}) {
  const [queue, setQueue] = useState<AdminReviewQueue | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [claim, setClaim] = useState<AdminReviewClaim | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setQueue(await api.adminReviewQueue(snapshotId))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '审核队列加载失败')
    } finally {
      setLoading(false)
    }
  }, [api, snapshotId])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <section className="panel admin-review-panel">
      <div className="panel-head">
        <div>
          <h2>能力审核队列</h2>
          <p>
            所有 AI 生成的候选都必须逐字段绑定已登记证据，并由管理员显式确认语义后才能发布。
            {snapshotStatus !== 'extracted' && ' 当前快照不是可编辑暂存快照，审核操作将被服务端拒绝。'}
          </p>
        </div>
        <button type="button" className="button button-ghost button-small" onClick={() => void load()}>
          <Icon name="refresh" size={15} />
          刷新队列
        </button>
      </div>

      {error && <Alert tone="danger">{error}</Alert>}

      {loading ? (
        <div className="loading-panel">
          <Spinner label="正在加载候选…" />
        </div>
      ) : queue ? (
        <>
          <div className="admin-review-summary">
            <div>
              <span>候选总数</span>
              <strong>{queue.summary.total}</strong>
            </div>
            <div>
              <span>已接受</span>
              <strong>{queue.summary.accepted}</strong>
            </div>
            <div>
              <span>待审核</span>
              <strong>{queue.summary.pending}</strong>
            </div>
            <div>
              <span>退回</span>
              <strong>{queue.summary.returned}</strong>
            </div>
            <div>
              <span>拒绝</span>
              <strong>{queue.summary.rejected}</strong>
            </div>
            <div>
              <span>已合并</span>
              <strong>{queue.summary.merged}</strong>
            </div>
          </div>
          {queue.items.length === 0 ? (
            <Alert tone="info" title="没有待审核候选">
              先完成源码索引，再由管理员显式发起能力调查。调查任务完成后候选才会出现在这里。
            </Alert>
          ) : (
            <div className="admin-claim-list">
              {queue.items.map((item) => {
                const revision = item.latest_revision
                const decision = revision?.decision || 'unreviewed'
                const draft = toForm(revision?.content ?? item.draft)
                const gateErrors = revision?.gate?.errors ?? []
                return (
                  <article key={item.claim_id} className="admin-claim-card">
                    <div className="admin-claim-main">
                      <div className="admin-claim-title">
                        <strong>{item.name || draft.name || '未命名候选'}</strong>
                        <Badge tone={decisionTone(decision)}>{decisionLabel(decision)}</Badge>
                        {revision ? <Badge tone="muted">修订 {revision.number}</Badge> : null}
                      </div>
                      <p className="admin-claim-text">
                        {draft.action || '—'} · {draft.target || '—'}
                      </p>
                      <div className="admin-meta-line">
                        <span>候选 {shortHash(item.claim_id, 10)}</span>
                        <span>{gateErrors.length ? `门槛错误 ${gateErrors.length}` : '门槛未校验'}</span>
                        <span>断言 {revision?.assertions_count ?? 0}</span>
                      </div>
                      {gateErrors.length > 0 && (
                        <div className="admin-chip-row">
                          {gateErrors.slice(0, 5).map((code) => (
                            <code key={code} className="admin-error-chip">
                              {code}
                            </code>
                          ))}
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      className="button button-secondary button-small"
                      onClick={() => setClaim(item)}
                    >
                      打开审核
                    </button>
                  </article>
                )
              })}
            </div>
          )}
          {queue.truncated && (
            <p className="admin-fineprint">
              当前只显示前 100 条候选；可通过服务端分页继续读取，界面不会静默隐藏剩余候选。
            </p>
          )}
        </>
      ) : null}

      {claim && (
        <ClaimReviewModal
          api={api}
          categoryId={categoryId}
          snapshotId={snapshotId}
          claim={claim}
          onClose={() => setClaim(null)}
          onChanged={() => {
            setClaim(null)
            void load()
            onChanged()
          }}
        />
      )}
    </section>
  )
}

function decisionLabel(value: string): string {
  if (value === 'accept') return '已接受'
  if (value === 'modify') return '已修改'
  if (value === 'return') return '已退回'
  if (value === 'reject') return '已拒绝'
  if (value === 'merged') return '已合并'
  return '未审核'
}

function decisionTone(value: string): string {
  if (value === 'accept') return 'ok'
  if (value === 'modify') return 'info'
  if (value === 'return') return 'warn'
  if (value === 'reject' || value === 'merged') return 'danger'
  return 'muted'
}

function ClaimReviewModal({
  api,
  categoryId,
  snapshotId,
  claim,
  onClose,
  onChanged,
}: {
  api: ApiClient
  categoryId: string
  snapshotId: string
  claim: AdminReviewClaim
  onClose: () => void
  onChanged: () => void
}) {
  const revision = claim.latest_revision
  const [mode, setMode] = useState<'accept' | 'disposition'>('accept')
  const [form, setForm] = useState<ContentForm>(() =>
    toForm(revision?.content ?? claim.draft),
  )
  const [declarationType, setDeclarationType] = useState(
    revision?.declaration_type === 'technical_mechanism' ? 'technical_mechanism' : 'product_function',
  )
  const [availabilityBasis, setAvailabilityBasis] = useState<'source_runtime' | 'document_declared' | 'mixed'>(
    revision?.availability_basis === 'document_declared' || revision?.availability_basis === 'mixed'
      ? revision.availability_basis
      : 'source_runtime',
  )
  const [productScope, setProductScope] = useState(Boolean(revision?.product_scope_confirmed))
  const [fieldState, setFieldState] = useState<Record<string, FieldState>>({})
  const [pool, setPool] = useState<PoolItem[]>([])
  const [reachability, setReachability] = useState<string[]>([])
  const [comment, setComment] = useState('')
  const [humanConfirmed, setHumanConfirmed] = useState(false)
  const [gate, setGate] = useState<{
    eligible_for_human_acceptance: boolean
    errors: string[]
    dependency_gaps: Array<Record<string, unknown>>
  } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [events, setEvents] = useState<Array<Record<string, unknown>>>([])
  const [disposition, setDisposition] = useState<'return' | 'reject'>('return')

  const expectedRevision = revision?.number ?? 0
  const content = useMemo(() => buildContent(form), [form])
  const fields = useMemo(() => flattenContent(content), [content])
  const poolMap = useMemo(() => new Map(pool.map((item) => [item.id, item])), [pool])

  useEffect(() => {
    let active = true
    api
      .adminClaim(claim.claim_id)
      .then((detail) => {
        if (active) setEvents(detail.review_events)
      })
      .catch(() => {
        // 审核事件只用于辅助阅读，读取失败不阻断表单；提交仍由服务端校验。
      })
    return () => {
      active = false
    }
  }, [api, claim.claim_id])

  function updateField(key: keyof ContentForm, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  function stateFor(key: string): FieldState {
    return fieldState[key] ?? defaultFieldState()
  }

  function updateState(key: string, patch: Partial<FieldState>) {
    setFieldState((prev) => ({
      ...prev,
      [key]: { ...(prev[key] ?? defaultFieldState()), ...patch },
    }))
  }

  function toggleEvidence(key: string, evidenceId: string) {
    const current = stateFor(key)
    const selected = current.evidenceIds.includes(evidenceId)
    if (!selected && current.evidenceIds.length >= 10) {
      setError('同一断言最多绑定 10 条证据')
      return
    }
    setError('')
    updateState(key, {
      evidenceIds: selected
        ? current.evidenceIds.filter((item) => item !== evidenceId)
        : [...current.evidenceIds, evidenceId],
    })
  }

  function toggleReachability(evidenceId: string) {
    setReachability((prev) =>
      prev.includes(evidenceId) ? prev.filter((item) => item !== evidenceId) : [...prev, evidenceId],
    )
  }

  function removeEvidence(id: string) {
    setPool((prev) => prev.filter((item) => item.id !== id))
    setReachability((prev) => prev.filter((item) => item !== id))
    setFieldState((prev) => {
      const next: Record<string, FieldState> = {}
      for (const [key, value] of Object.entries(prev)) {
        next[key] = {
          ...value,
          evidenceIds: value.evidenceIds.filter((item) => item !== id),
        }
      }
      return next
    })
  }

  function buildAssertions(): ReviewAssertionPayload[] {
    return fields.map((field) => {
      const selected = stateFor(field.key).evidenceIds
      const kinds = selected.map((id) => poolMap.get(id)?.kind).filter(Boolean)
      const hasSource = kinds.includes('source')
      const hasDocument = kinds.includes('document')
      const proofBasis: ReviewAssertionPayload['proof_basis'] =
        hasSource && hasDocument ? 'mixed' : hasDocument ? 'document' : hasSource ? 'source' : null
      return {
        field: field.key,
        text: field.text,
        evidence_ids: selected,
        semantic_decision: stateFor(field.key).semantic,
        rationale: stateFor(field.key).rationale.trim(),
        dependencies: [],
        implementation_evidence_ids: [],
        proof_basis: proofBasis,
        document_scope_confirmed: hasDocument ? stateFor(field.key).documentConfirmed : false,
      }
    })
  }

  function localErrors(): string[] {
    const problems: string[] = []
    if (!content.name || !content.action || !content.target || !content.business_goal || !content.implementation_mechanism) {
      problems.push('能力名称、动作、对象、业务目标与实现机制都必须填写')
    }
    if (content.supported_scope.length === 0) problems.push('支持范围至少一条')
    if (content.limitations.length === 0) problems.push('限制至少一条，不能把文档一句话当成无限能力')
    if (fields.length === 0) problems.push('没有可审核字段')
    for (const field of fields) {
      const state = stateFor(field.key)
      if (state.evidenceIds.length === 0) problems.push(`${field.key} 缺少证据`)
      if (state.semantic !== 'supported') problems.push(`${field.key} 尚未确认“证据支持”`)
      if (!state.rationale.trim()) problems.push(`${field.key} 缺少人工核对说明`)
      const hasDocument = state.evidenceIds.some((id) => poolMap.get(id)?.kind === 'document')
      if (hasDocument && !state.documentConfirmed) problems.push(`${field.key} 尚未确认文档引文范围`)
    }
    const anyDocument = fields.some((field) =>
      stateFor(field.key).evidenceIds.some((id) => poolMap.get(id)?.kind === 'document'),
    )
    if (anyDocument && availabilityBasis === 'source_runtime') {
      problems.push('使用文档证据时可用性依据必须选择“文档明示”或“混合”')
    }
    const allDocumentOnly = fields.every((field) => {
      const kinds = stateFor(field.key).evidenceIds.map((id) => poolMap.get(id)?.kind)
      return kinds.length > 0 && kinds.every((kind) => kind === 'document')
    })
    if (anyDocument && allDocumentOnly && !productScope) {
      problems.push('仅文档证据时必须显式确认描述落在当前产品范围内')
    }
    if (!comment.trim()) problems.push('必须填写审核意见')
    if (!humanConfirmed) problems.push('必须勾选“我已人工核对证据与描述”')
    return problems
  }

  function payload(action: 'accept' | 'modify') {
    return {
      action,
      expected_revision: expectedRevision,
      declaration_type: declarationType,
      content,
      assertions: buildAssertions(),
      reachability_evidence_ids: reachability,
      comment: comment.trim(),
      human_confirmed: humanConfirmed,
      availability_basis: availabilityBasis,
      product_scope_confirmed: productScope,
    }
  }

  async function validate() {
    const problems = localErrors()
    if (problems.length) {
      setGate(null)
      setError(problems.slice(0, 8).join('；'))
      return
    }
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      const result = await api.validateReview(claim.claim_id, payload('accept'))
      setGate(result)
      if (result.eligible_for_human_acceptance) {
        setSuccess('服务端门槛已通过，仍需人工确认后才能接受。')
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '门槛校验失败')
    } finally {
      setBusy(false)
    }
  }

  async function submit(action: 'accept' | 'modify') {
    const problems = localErrors()
    if (problems.length) {
      setError(problems.slice(0, 8).join('；'))
      return
    }
    if (action === 'accept' && gate && !gate.eligible_for_human_acceptance) {
      setError('服务端门槛未通过，不能接受；请先修正断言、范围或证据。')
      return
    }
    setBusy(true)
    setError('')
    setSuccess('')
    try {
      await api.submitCapabilityReview(claim.claim_id, payload(action))
      setSuccess(action === 'accept' ? '已接受并生成新的审核修订。' : '已保存修改修订，等待进一步审核。')
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '审核提交失败')
      setBusy(false)
    }
  }

  async function submitDisposition() {
    if (!comment.trim()) {
      setError('退回或拒绝必须填写原因')
      return
    }
    setBusy(true)
    setError('')
    try {
      await api.submitCapabilityReview(claim.claim_id, {
        action: disposition,
        expected_revision: expectedRevision,
        declaration_type: 'unknown',
        content: null,
        assertions: [],
        reachability_evidence_ids: [],
        comment: comment.trim(),
        human_confirmed: false,
        availability_basis: null,
        product_scope_confirmed: false,
      })
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '处理失败')
      setBusy(false)
    }
  }

  return (
    <Modal
      title={`审核：${claim.name || form.name || '候选能力'}`}
      subtitle={`修订 ${expectedRevision} · 快照 ${shortHash(snapshotId, 12)} · ${claim.status}`}
      onClose={onClose}
      wide
    >
      <div className="admin-review-modal">
        <div className="admin-review-tabs">
          <button
            type="button"
            className={mode === 'accept' ? 'tab-button tab-active' : 'tab-button'}
            onClick={() => setMode('accept')}
          >
            人工接受 / 修改
          </button>
          <button
            type="button"
            className={mode === 'disposition' ? 'tab-button tab-active' : 'tab-button'}
            onClick={() => setMode('disposition')}
          >
            退回 / 拒绝
          </button>
          <span className="admin-review-event-count">历史审核事件 {events.length}</span>
        </div>

        {error && <Alert tone="danger" title="未提交">{error}</Alert>}
        {success && <Alert tone="success">{success}</Alert>}

        {mode === 'disposition' ? (
          <div className="form-stack">
            <Alert tone="warn" title="退回或拒绝不会修改能力描述">
              请写明缺失的证据、错误的声明或需要补充的产品范围；该操作会写入不可变审核修订与审计日志。
            </Alert>
            <label className="field">
              <span>处理</span>
              <select
                value={disposition}
                onChange={(event) => setDisposition(event.target.value as 'return' | 'reject')}
              >
                <option value="return">退回补充证据或修改描述</option>
                <option value="reject">拒绝该候选</option>
              </select>
            </label>
            <label className="field">
              <span>原因（必填）</span>
              <textarea
                rows={4}
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                placeholder="例如：实现机制缺少被调方证据，且支持范围超出文档明示范围。"
              />
            </label>
            <div className="modal-actions">
              <button type="button" className="button button-ghost" onClick={onClose}>
                取消
              </button>
              <button
                type="button"
                className="button button-danger"
                disabled={busy}
                onClick={() => void submitDisposition()}
              >
                {busy ? '提交中…' : disposition === 'return' ? '确认退回' : '确认拒绝'}
              </button>
            </div>
          </div>
        ) : (
          <div className="admin-review-layout">
            <div className="admin-review-main">
              <div className="admin-form-grid">
                {SCALAR_FIELDS.map((field) => (
                  <label key={field.key} className="field admin-field-wide">
                    <span>{field.label}</span>
                    <input
                      value={form[field.key]}
                      onChange={(event) => updateField(field.key, event.target.value)}
                      placeholder={field.hint}
                    />
                  </label>
                ))}
                <label className="field admin-field-wide">
                  <span>支持范围（每行一条）</span>
                  <textarea
                    rows={3}
                    value={form.supported_scope}
                    onChange={(event) => updateField('supported_scope', event.target.value)}
                  />
                </label>
                <label className="field admin-field-wide">
                  <span>适用条件（每行一条，可空）</span>
                  <textarea
                    rows={2}
                    value={form.conditions}
                    onChange={(event) => updateField('conditions', event.target.value)}
                  />
                </label>
                <label className="field admin-field-wide">
                  <span>限制（每行一条，至少一条）</span>
                  <textarea
                    rows={2}
                    value={form.limitations}
                    onChange={(event) => updateField('limitations', event.target.value)}
                  />
                </label>
              </div>

              <div className="admin-assertion-section">
                <div className="admin-section-head">
                  <div>
                    <h3>逐字段断言</h3>
                    <p>每个字段都必须单独选择证据、标记“支持”并写明核对依据。</p>
                  </div>
                  <Badge tone={fields.every((field) => stateFor(field.key).evidenceIds.length) ? 'ok' : 'warn'}>
                    {fields.filter((field) => stateFor(field.key).evidenceIds.length).length} / {fields.length}
                  </Badge>
                </div>
                {fields.map((field) => {
                  const state = stateFor(field.key)
                  const hasDocument = state.evidenceIds.some(
                    (id) => poolMap.get(id)?.kind === 'document',
                  )
                  return (
                    <div key={field.key} className="admin-assertion-card">
                      <div className="admin-assertion-head">
                        <div>
                          <code>{field.key}</code>
                          <p>{field.text || '（空字段）'}</p>
                        </div>
                        <select
                          value={state.semantic}
                          onChange={(event) =>
                            updateState(field.key, {
                              semantic: event.target.value as FieldState['semantic'],
                            })
                          }
                          aria-label={`${field.key} 语义结论`}
                        >
                          <option value="unverified">未确认</option>
                          <option value="supported">证据支持</option>
                          <option value="contradicted">证据矛盾</option>
                        </select>
                      </div>
                      <div className="admin-evidence-choices">
                        {pool.length === 0 ? (
                          <span className="admin-fineprint">先在右侧检索并登记证据。</span>
                        ) : (
                          pool.map((item) => (
                            <label key={item.id} className="admin-check admin-check-compact">
                              <input
                                type="checkbox"
                                checked={state.evidenceIds.includes(item.id)}
                                onChange={() => toggleEvidence(field.key, item.id)}
                              />
                              <span>
                                <Badge tone={item.kind === 'document' ? 'info' : 'muted'}>
                                  {item.kind === 'document' ? '文档' : '源码'}
                                </Badge>{' '}
                                {item.label}
                              </span>
                            </label>
                          ))
                        )}
                      </div>
                      {hasDocument && (
                        <label className="admin-check">
                          <input
                            type="checkbox"
                            checked={state.documentConfirmed}
                            onChange={(event) =>
                              updateState(field.key, { documentConfirmed: event.target.checked })
                            }
                          />
                          <span>我已核对文档引文与该字段明示范围一致，未扩大解释。</span>
                        </label>
                      )}
                      <label className="field">
                        <span>人工核对说明（必填）</span>
                        <input
                          value={state.rationale}
                          onChange={(event) =>
                            updateState(field.key, { rationale: event.target.value })
                          }
                          placeholder="说明这条证据如何支持该字段，以及排除了哪些扩大解释。"
                        />
                      </label>
                    </div>
                  )
                })}
              </div>

              <div className="admin-decision-box">
                <div className="admin-form-grid">
                  <label className="field">
                    <span>声明类型</span>
                    <select
                      value={declarationType}
                      onChange={(event) => setDeclarationType(event.target.value)}
                    >
                      <option value="product_function">产品功能</option>
                      <option value="technical_mechanism">技术机制</option>
                    </select>
                  </label>
                  <label className="field">
                    <span>可用性依据</span>
                    <select
                      value={availabilityBasis}
                      onChange={(event) =>
                        setAvailabilityBasis(
                          event.target.value as 'source_runtime' | 'document_declared' | 'mixed',
                        )
                      }
                    >
                      <option value="source_runtime">源码运行路径</option>
                      <option value="document_declared">文档明示</option>
                      <option value="mixed">源码 + 文档</option>
                    </select>
                  </label>
                  <label className="field admin-field-wide">
                    <span>运行时可达证据（产品功能至少选一条源码证据；纯文档路径需显式范围确认）</span>
                    <div className="admin-evidence-choices">
                      {pool.filter((item) => item.kind === 'source').length === 0 ? (
                        <span className="admin-fineprint">尚未登记源码证据。</span>
                      ) : (
                        pool
                          .filter((item) => item.kind === 'source')
                          .map((item) => (
                            <label key={item.id} className="admin-check admin-check-compact">
                              <input
                                type="checkbox"
                                checked={reachability.includes(item.id)}
                                onChange={() => toggleReachability(item.id)}
                              />
                              <span>{item.label}</span>
                            </label>
                          ))
                      )}
                    </div>
                  </label>
                </div>
                <label className="admin-check">
                  <input
                    type="checkbox"
                    checked={productScope}
                    onChange={(event) => setProductScope(event.target.checked)}
                  />
                  <span>我确认该能力描述落在当前产品范围内；文档证据只证明其明示范围。</span>
                </label>
                <label className="admin-check">
                  <input
                    type="checkbox"
                    checked={humanConfirmed}
                    onChange={(event) => setHumanConfirmed(event.target.checked)}
                  />
                  <span>我已人工阅读全部证据与描述，并对其真实性、范围与限制负责。</span>
                </label>
                <label className="field">
                  <span>审核意见（必填）</span>
                  <textarea
                    rows={3}
                    value={comment}
                    onChange={(event) => setComment(event.target.value)}
                    placeholder="记录本次审核的依据、范围判断与仍需后续跟踪的事项。"
                  />
                </label>

                {gate && (
                  <div className={gate.eligible_for_human_acceptance ? 'admin-gate admin-gate-ok' : 'admin-gate admin-gate-bad'}>
                    <strong>
                      {gate.eligible_for_human_acceptance
                        ? '服务端门槛：可进入人工接受'
                        : '服务端门槛：存在阻断项'}
                    </strong>
                    {gate.errors.length ? (
                      <div className="admin-chip-row">
                        {gate.errors.map((code) => (
                          <code key={code} className="admin-error-chip">
                            {code}
                          </code>
                        ))}
                      </div>
                    ) : (
                      <p>断言字段、证据和声明类型均已闭合；最终接受仍由本次人工确认触发。</p>
                    )}
                    {gate.dependency_gaps?.length ? (
                      <p className="admin-fineprint">依赖缺口 {gate.dependency_gaps.length} 项，需核对调用链。</p>
                    ) : null}
                  </div>
                )}

                <div className="modal-actions">
                  <button type="button" className="button button-ghost" onClick={onClose}>
                    关闭
                  </button>
                  <button
                    type="button"
                    className="button button-ghost"
                    disabled={busy}
                    onClick={() => void validate()}
                  >
                    校验门槛
                  </button>
                  <button
                    type="button"
                    className="button button-secondary"
                    disabled={busy}
                    onClick={() => void submit('modify')}
                  >
                    保存修改
                  </button>
                  <button
                    type="button"
                    className="button button-primary"
                    disabled={busy}
                    onClick={() => void submit('accept')}
                  >
                    {busy ? '处理中…' : '接受并生成修订'}
                  </button>
                </div>
              </div>
            </div>

            <aside className="admin-review-side">
              <EvidenceFinder
                api={api}
                categoryId={categoryId}
                snapshotId={snapshotId}
                pool={pool}
                onAdd={(item) => setPool((prev) => (prev.some((row) => row.id === item.id) ? prev : [...prev, item]))}
                onRemove={removeEvidence}
              />
              <div className="admin-evidence-pool">
                <div className="admin-section-head">
                  <h3>已登记证据</h3>
                  <Badge tone="muted">{pool.length}</Badge>
                </div>
                {pool.length === 0 ? (
                  <p className="admin-fineprint">证据按快照隔离；登记时会再次校验来源哈希。</p>
                ) : (
                  pool.map((item) => (
                    <div key={item.id} className="admin-evidence-item">
                      <div>
                        <Badge tone={item.kind === 'document' ? 'info' : 'muted'}>
                          {item.kind === 'document' ? '文档' : '源码'}
                        </Badge>
                        <strong>{item.label}</strong>
                        <span>{item.locator}</span>
                      </div>
                      <p>{item.quote.slice(0, 220)}</p>
                      <div className="admin-evidence-item-foot">
                        <code>{item.id}</code>
                        <button type="button" className="link-button" onClick={() => removeEvidence(item.id)}>
                          移除
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
              <div className="admin-evidence-pool">
                <div className="admin-section-head">
                  <h3>审核历史</h3>
                  <Badge tone="muted">{events.length}</Badge>
                </div>
                {events.length === 0 ? (
                  <p className="admin-fineprint">该候选还没有审核事件。</p>
                ) : (
                  events.slice(-8).reverse().map((event, index) => (
                    <div key={String(event.id ?? index)} className="admin-event-row">
                      <strong>{String(event.action ?? '')}</strong>
                      <span>{String(event.comment ?? '')}</span>
                    </div>
                  ))
                )}
              </div>
            </aside>
          </div>
        )}
      </div>
    </Modal>
  )
}

function EvidenceFinder({
  api,
  categoryId,
  snapshotId,
  pool,
  onAdd,
  onRemove,
}: {
  api: ApiClient
  categoryId: string
  snapshotId: string
  pool: PoolItem[]
  onAdd: (item: PoolItem) => void
  onRemove: (id: string) => void
}) {
  const [tool, setTool] = useState<'search_symbols' | 'search_documents'>('search_symbols')
  const [query, setQuery] = useState('')
  const [statement, setStatement] = useState('')
  const [results, setResults] = useState<RetrievalItem[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [adding, setAdding] = useState<string | null>(null)

  async function search() {
    if (!query.trim()) {
      setError('请输入检索词')
      return
    }
    setBusy(true)
    setError('')
    try {
      const envelope = await api.retrieval(categoryId, snapshotId, {
        tool,
        query: query.trim(),
        limit: 20,
      })
      setResults(envelope.results)
      setWarnings(envelope.warnings)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '检索失败')
    } finally {
      setBusy(false)
    }
  }

  async function register(result: RetrievalItem) {
    if (!statement.trim()) {
      setError('登记证据前必须写明这条证据支持什么')
      return
    }
    setBusy(true)
    setError('')
    try {
      let receipt: { evidence_id: string }
      let item: PoolItem
      if (tool === 'search_symbols') {
        if (!result.file_id || !result.sha256 || !result.start_line || !result.end_line) {
          throw new Error('源码结果缺少文件或行号，不能登记')
        }
        receipt = await api.registerEvidence(snapshotId, {
          kind: 'source',
          file_id: result.file_id,
          start_line: result.start_line,
          end_line: result.end_line,
          expected_sha256: result.sha256,
          supported_statement: statement.trim(),
        })
        item = {
          id: receipt.evidence_id,
          kind: 'source',
          label: result.name || result.path || '源码',
          quote: result.snippet || '',
          locator: `${result.path ?? ''}:${result.start_line}-${result.end_line}`,
        }
      } else {
        if (!result.id || !result.sha256) {
          throw new Error('文档段落缺少分段 ID 或文本哈希，不能登记')
        }
        receipt = await api.registerEvidence(snapshotId, {
          kind: 'document',
          segment_id: result.id,
          expected_sha256: result.sha256,
          supported_statement: statement.trim(),
        })
        item = {
          id: receipt.evidence_id,
          kind: 'document',
          label: result.path || '技术文档',
          quote: result.snippet || '',
          locator: String(result.locator?.heading || result.locator?.page || '段落'),
        }
      }
      if (pool.some((row) => row.id === item.id)) {
        onRemove(item.id)
      }
      onAdd(item)
      setAdding(item.id)
      setResults((prev) => prev.filter((row) => row !== result))
    } catch (err) {
      setError(err instanceof Error ? err.message : '证据登记失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="admin-evidence-pool">
      <div className="admin-section-head">
        <div>
          <h3>检索并登记证据</h3>
          <p>只读工具不会写入知识；登记时服务端重新读取原文并校验哈希。</p>
        </div>
      </div>
      <div className="admin-evidence-search">
        <select value={tool} onChange={(event) => setTool(event.target.value as typeof tool)}>
          <option value="search_symbols">源码符号</option>
          <option value="search_documents">技术文档段落</option>
        </select>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={tool === 'search_symbols' ? '符号名，例如 waf_rule' : '文档关键词'}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void search()
          }}
        />
        <button type="button" className="button button-ghost button-small" disabled={busy} onClick={() => void search()}>
          检索
        </button>
      </div>
      <label className="field">
        <span>证据用途说明（登记时必填）</span>
        <input
          value={statement}
          onChange={(event) => setStatement(event.target.value)}
          placeholder="例如：该函数实现规则匹配与阻断动作，用于支持“动作”字段。"
        />
      </label>
      {error && <Alert tone="danger">{error}</Alert>}
      {warnings.length > 0 && (
        <p className="admin-fineprint">检索提示：{warnings.join('、')}</p>
      )}
      {busy && <Spinner label="正在读取与校验来源…" />}
      <div className="admin-search-results">
        {results.map((result, index) => (
          <div key={result.id || result.file_id || index} className="admin-search-result">
            <div>
              <strong>{result.name || result.path || '结果'}</strong>
              <span>{result.path}</span>
              <p>{result.snippet?.slice(0, 280)}</p>
            </div>
            <button
              type="button"
              className="button button-secondary button-small"
              disabled={busy || adding === result.id}
              onClick={() => void register(result)}
            >
              登记为证据
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
