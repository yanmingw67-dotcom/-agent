import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { ReviewDialog } from '../components/ReviewDialog'
import { Alert, Badge, EmptyState, Icon, Spinner } from '../components/ui'
import {
  countLabel,
  formatBytes,
  formatDate,
  percent,
  shortHash,
  statusLabel,
  statusTone,
  verdictTone,
} from '../format'
import { navigate, useDocumentTitle, usePolling } from '../hooks'
import type {
  AtomicRequirement,
  EvaluationJob,
  Evidence,
  ExportFormat,
  ExportJob,
  ParameterResult,
  ResultsPayload,
  Verdict,
} from '../types'

type Filter = 'all' | Verdict | 'pending' | 'reviewed'

const FILTERS: Array<{ value: Filter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: '支持', label: '支持' },
  { value: '不支持', label: '不支持' },
  { value: '无法确认', label: '无法确认' },
  { value: 'pending', label: '待复核' },
  { value: 'reviewed', label: '已复核' },
]

function EvidenceCard({ evidence }: { evidence: Evidence }) {
  const unavailable = evidence.context_status === 'unavailable'
  return (
    <div className={`evidence-card ${unavailable ? 'evidence-unavailable' : ''}`}>
      <div className="evidence-head">
        <span className="evidence-source">
          <Icon name="file" size={15} />
          {evidence.source_type_label || evidence.kind} · {evidence.source_file || evidence.source_ref}
        </span>
        <div className="evidence-badges">
          {unavailable && <Badge tone="danger">来源不可用</Badge>}
          {evidence.verification_state === 'verified_contradiction' && <Badge tone="danger">反向证据</Badge>}
          {evidence.verification_state === 'verified_support' && <Badge tone="ok">支持证据</Badge>}
        </div>
      </div>
      <blockquote>{evidence.quote || '证据摘录缺失'}</blockquote>
      <div className="evidence-meta">
        <span>定位：{evidence.locator_text || evidence.source_ref}</span>
        {evidence.capability_id && <span>能力：{evidence.capability_id}</span>}
        {evidence.used_for && evidence.used_for.length > 0 && (
          <span>用于：{evidence.used_for.join('、')}</span>
        )}
      </div>
      {(evidence.supported_scope_text || evidence.limitations_text || evidence.conditions_text) && (
        <div className="evidence-scope">
          {evidence.supported_scope_text && <p><strong>适用范围：</strong>{evidence.supported_scope_text}</p>}
          {evidence.limitations_text && <p><strong>限制条件：</strong>{evidence.limitations_text}</p>}
          {evidence.conditions_text && <p><strong>前置条件：</strong>{evidence.conditions_text}</p>}
        </div>
      )}
      {evidence.context_note && <p className="evidence-note">{evidence.context_note}</p>}
    </div>
  )
}

function AtomRow({ atom }: { atom: AtomicRequirement }) {
  return (
    <li className="atom-row">
      <div className="atom-head">
        <span className="atom-index">{atom.order_index}</span>
        <Badge tone={verdictTone(atom.verdict)}>{atom.verdict}</Badge>
        <span className="atom-flag">{atom.mandatory_status === 'optional' ? '可选' : '必需'}</span>
        <span className="atom-flag">{atom.logical_relation}</span>
        {atom.threshold !== null && atom.threshold !== undefined && (
          <span className="atom-flag">
            阈值 {atom.threshold_operator || ''} {atom.threshold}{atom.unit || ''}
          </span>
        )}
      </div>
      <p className="atom-text">{atom.requirement_text}</p>
      {atom.reason && <p className="atom-reason">{atom.reason}</p>}
      {atom.conditions_text && <p className="atom-condition">条件：{atom.conditions_text}</p>}
      {atom.ambiguity && <p className="atom-condition">歧义：{atom.ambiguity}</p>}
      {atom.evidence.length === 0 ? (
        <span className="atom-no-evidence">无可用证据，保持“{atom.verdict}”</span>
      ) : (
        <div className="atom-evidence">
          {atom.evidence.map((item) => (
            <EvidenceCard key={`${atom.requirement_id}-${item.evidence_id}`} evidence={item} />
          ))}
        </div>
      )}
    </li>
  )
}

function ParameterCard({
  parameter,
  expanded,
  onToggle,
  onReview,
}: {
  parameter: ParameterResult
  expanded: boolean
  onToggle: () => void
  onReview: () => void
}) {
  const hasHuman = Boolean(parameter.review)
  const differs =
    parameter.human_final_verdict !== null &&
    parameter.human_final_verdict !== parameter.verdict
  return (
    <article className={`result-card ${expanded ? 'result-expanded' : ''}`}>
      <button type="button" className="result-head" onClick={onToggle} aria-expanded={expanded}>
        <span className="result-index">{parameter.order_index}</span>
        <span className="result-title">
          <strong>{parameter.parameter_name}</strong>
          <span className="result-rationale">{parameter.rationale || parameter.logic_reason || '无技术论证说明'}</span>
        </span>
        <span className="result-badges">
          {parameter.error_code && <Badge tone="danger">执行失败</Badge>}
          <Badge tone={verdictTone(parameter.verdict)}>{parameter.verdict}</Badge>
          <Badge tone={hasHuman ? (parameter.review_status === 'accepted' ? 'ok' : 'warn') : 'muted'}>
            {parameter.review_status_label}
          </Badge>
          {differs && <Badge tone="warn">人工：{parameter.human_final_verdict}</Badge>}
        </span>
        <span className={`result-chevron ${expanded ? 'result-chevron-open' : ''}`}>
          <Icon name="chevron" size={18} />
        </span>
      </button>

      {expanded && (
        <div className="result-body">
          {parameter.error_code && (
            <Alert tone="danger" title="该参数评估执行失败">
              错误码 {parameter.error_code}。系统按“无法确认”关闭，不能视为支持。
            </Alert>
          )}

          <div className="result-columns">
            <div>
              <h4>招标参数原文</h4>
              <p className="parameter-text">{parameter.original_text}</p>
            </div>
            <div>
              <h4>结论与论证</h4>
              <div className="verdict-line">
                <span>系统自动结论</span>
                <Badge tone={verdictTone(parameter.verdict)}>{parameter.verdict}</Badge>
              </div>
              <p className="rationale">{parameter.rationale || '无技术论证说明'}</p>
              {parameter.logic_reason && <p className="logic-reason">逻辑汇总：{parameter.logic_reason}</p>}
            </div>
          </div>

          <div className="human-compare">
            <div>
              <span>系统自动结论</span>
              <strong className={`text-${verdictTone(parameter.verdict)}`}>{parameter.verdict}</strong>
            </div>
            <div>
              <span>人工应答结论</span>
              <strong className={`text-${verdictTone(parameter.human_final_verdict)}`}>
                {parameter.human_final_verdict || '未复核'}
              </strong>
            </div>
            <div>
              <span>复核状态</span>
              <strong>{parameter.review_status_label}</strong>
            </div>
            <div>
              <span>是否需重评</span>
              <strong>{parameter.requires_reevaluation ? '是' : '否'}</strong>
            </div>
          </div>
          {hasHuman && (parameter.human_comment || parameter.human_adjust_reason) && (
            <div className="review-note-box">
              {parameter.human_comment && <p><strong>复核意见：</strong>{parameter.human_comment}</p>}
              {parameter.human_adjust_reason && <p><strong>调整理由：</strong>{parameter.human_adjust_reason}</p>}
            </div>
          )}

          <h4>原子要求与证据（{parameter.atomic_requirements.length} 项）</h4>
          {parameter.atomic_requirements.length === 0 ? (
            <p className="muted">该参数没有原子要求，无法形成确定性结论。</p>
          ) : (
            <ol className="atom-list">
              {parameter.atomic_requirements.map((atom) => (
                <AtomRow key={atom.requirement_id} atom={atom} />
              ))}
            </ol>
          )}

          <div className="result-actions">
            <button type="button" className="button button-secondary" onClick={onReview}>
              <Icon name="eye" size={16} />
              人工复核
            </button>
            <span className="form-hint">复核只追加记录，不改写自动结论与证据。</span>
          </div>
        </div>
      )}
    </article>
  )
}

export function ResultDetail({ api, id }: { api: ApiClient; id: string }) {
  useDocumentTitle('评估结果')
  const [data, setData] = useState<ResultsPayload | null>(null)
  const [exports, setExports] = useState<ExportJob[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [initialized, setInitialized] = useState(false)
  const [reviewTarget, setReviewTarget] = useState<ParameterResult | null>(null)
  const [exporting, setExporting] = useState<ExportFormat | null>(null)
  const [downloading, setDownloading] = useState<string | null>(null)

  const loadExports = useCallback(async () => {
    try {
      const list = await api.listExports(id)
      setExports(list.items)
    } catch (err) {
      if (err instanceof ApiError && err.status !== 401) setNotice(err.message)
    }
  }, [api, id])

  const load = useCallback(async () => {
    try {
      const [payload, list] = await Promise.all([api.getResults(id), api.listExports(id)])
      setData(payload)
      setExports(list.items)
      setError('')
      if (!initialized && payload.parameters.length > 0) {
        setExpanded(new Set([payload.parameters[0].parameter_id]))
        setInitialized(true)
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '评估结果加载失败')
    } finally {
      setLoading(false)
    }
  }, [api, id, initialized])

  useEffect(() => {
    void load()
  }, [load])

  const pendingExports = exports.some((item) => item.status === 'queued' || item.status === 'running')
  usePolling(() => void loadExports(), pendingExports, 1500)

  const job: EvaluationJob | null = data?.job ?? null
  const parameters = data?.parameters ?? []
  const evidenceAvailable = job?.evidence_context_available !== false

  const counts = useMemo(() => {
    const result = { 支持: 0, 不支持: 0, 无法确认: 0 }
    for (const item of parameters) result[item.verdict] += 1
    return result
  }, [parameters])

  const filtered = useMemo(() => {
    const text = query.trim().toLowerCase()
    return parameters.filter((item) => {
      if (filter === 'pending' && item.review_status !== 'pending') return false
      if (filter === 'reviewed' && item.review_status === 'pending') return false
      if (filter === '支持' || filter === '不支持' || filter === '无法确认') {
        if (item.verdict !== filter) return false
      }
      if (!text) return true
      return (
        item.parameter_name.toLowerCase().includes(text) ||
        item.original_text.toLowerCase().includes(text) ||
        item.rationale.toLowerCase().includes(text)
      )
    })
  }, [parameters, filter, query])

  function toggle(parameterId: string) {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(parameterId)) next.delete(parameterId)
      else next.add(parameterId)
      return next
    })
  }

  async function startExport(format: ExportFormat) {
    setExporting(format)
    setNotice('')
    try {
      await api.createExport(id, format)
      await loadExports()
      setNotice(`已提交 ${format === 'docx' ? 'Word' : 'Excel'} 导出，生成后可在这里下载。`)
    } catch (err) {
      setNotice(err instanceof ApiError ? `${err.code}：${err.message}` : '导出提交失败')
    } finally {
      setExporting(null)
    }
  }

  async function download(item: ExportJob) {
    setDownloading(item.export_id)
    setNotice('')
    try {
      await api.downloadExport(item)
    } catch (err) {
      setNotice(err instanceof ApiError ? `${err.code}：${err.message}` : '下载失败')
    } finally {
      setDownloading(null)
    }
  }

  if (loading) {
    return <div className="page"><div className="loading-panel"><Spinner label="正在加载评估结果…" /></div></div>
  }

  if (error || !job) {
    return (
      <div className="page">
        <Alert tone="danger" title="无法加载评估结果">{error || '评估任务不存在'}</Alert>
        <button type="button" className="button button-secondary" onClick={() => navigate('/tasks')}>
          <Icon name="arrow-left" size={16} /> 返回任务
        </button>
      </div>
    )
  }

  const reviewSummary = job.review_summary
  const reviewProgress = percent(reviewSummary?.reviewed ?? 0, job.parameter_count)
  const exportable = job.exportable !== false && evidenceAvailable

  return (
    <div className="page page-wide">
      <button type="button" className="back-link" onClick={() => navigate('/tasks')}>
        <Icon name="arrow-left" size={16} /> 返回任务
      </button>

      <header className="page-head result-page-head">
        <div>
          <span className="eyebrow">评估结果</span>
          <h1>{job.category_name || '产品品类评估'}</h1>
          <div className="head-meta">
            <Badge tone={statusTone(job.status)} dot>{statusLabel(job.status)}</Badge>
            <span>任务 {shortHash(job.job_id, 12)}</span>
            <span>固定发布 {shortHash(job.publication_id, 12)}</span>
            <span>完成 {formatDate(job.finished_at || job.created_at)}</span>
          </div>
        </div>
        <div className="head-actions">
          <button type="button" className="button button-ghost" onClick={() => void load()}>
            <Icon name="refresh" size={16} /> 刷新
          </button>
          <button
            type="button"
            className="button button-secondary"
            disabled={!exportable || exporting !== null}
            onClick={() => void startExport('docx')}
          >
            {exporting === 'docx' ? <Spinner label="提交中…" /> : <><Icon name="download" size={16} /> 导出 Word</>}
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={!exportable || exporting !== null}
            onClick={() => void startExport('xlsx')}
          >
            {exporting === 'xlsx' ? <Spinner label="提交中…" /> : <><Icon name="download" size={16} /> 导出 Excel</>}
          </button>
        </div>
      </header>

      {notice && <Alert tone={notice.includes('失败') || notice.includes(':') ? 'warn' : 'info'}>{notice}</Alert>}
      {!evidenceAvailable && (
        <Alert tone="danger" title="证据来源不可用，导出已禁用">
          固定发布版本校验失败（{job.publication_context_error || '未知错误'}）。系统不会替换为当前生效版本，
          也不会生成缺少来源的导出文件。
        </Alert>
      )}
      {job.warnings && job.warnings.length > 0 && (
        <div className="warning-list">
          {job.warnings.map((warning) => (
            <Alert key={warning.code} tone={warning.code === 'PARAMETER_EVALUATION_FAILED' ? 'danger' : 'warn'}>
              {warning.message}
            </Alert>
          ))}
        </div>
      )}

      <section className="result-summary">
        <div className="summary-card summary-ok">
          <span>支持</span>
          <strong>{counts.支持}</strong>
          <small>自动结论</small>
        </div>
        <div className="summary-card summary-danger">
          <span>不支持</span>
          <strong>{counts.不支持}</strong>
          <small>自动结论</small>
        </div>
        <div className="summary-card summary-warn">
          <span>无法确认</span>
          <strong>{counts.无法确认}</strong>
          <small>自动结论</small>
        </div>
        <div className="summary-card">
          <span>人工复核</span>
          <strong>{reviewSummary?.reviewed ?? 0}<em> / {job.parameter_count}</em></strong>
          <small>待复核 {reviewSummary?.pending ?? 0} 条</small>
        </div>
        <div className="summary-card summary-wide">
          <span>证据与版本</span>
          <div className="summary-inline">
            <span>{job.atomic_count} 项原子要求</span>
            <span>{job.parameter_count} 条参数</span>
            <span>发布哈希 {shortHash(job.publication_hash, 10)}</span>
          </div>
          <div className="mini-progress"><span style={{ width: `${reviewProgress}%` }} /></div>
        </div>
      </section>

      <section className="card export-panel">
        <div className="section-head">
          <div>
            <h2>导出与下载</h2>
            <p>
              Word 为可编辑表格；Excel 含评估汇总、原子子要求、待确认清单与证据索引四张工作表。
              导出只读取本任务已持久化结果与固定发布版本，与页面一致。
            </p>
          </div>
          {!exportable && <Badge tone="danger">当前不可导出</Badge>}
        </div>
        {exports.length === 0 ? (
          <p className="muted">还没有导出记录。点击页面右上角按钮生成 Word 或 Excel。</p>
        ) : (
          <div className="export-list">
            {exports.map((item) => (
              <div key={item.export_id} className="export-row">
                <span className={`export-icon export-${item.format}`}>
                  <Icon name="file" size={18} />
                </span>
                <div className="export-main">
                  <strong>{item.filename}</strong>
                  <span>
                    {item.format === 'docx' ? 'Word' : 'Excel'} ·
                    {' '}{formatDate(item.created_at)} ·
                    {' '}{formatBytes(item.size_bytes)} ·
                    {' '}参数 {countLabel(item.parameter_count)} 条
                  </span>
                  {item.error_code && <span className="text-danger">失败原因：{item.error_code}</span>}
                  {item.status === 'succeeded' && (
                    <span>
                      下载凭证到期：{item.download_expires_at ? formatDate(item.download_expires_at) : '历史任务无凭证'}
                      {item.integrity_available
                        ? ' · 文件完整性摘要可用'
                        : ' · 缺少生成时完整性摘要，需重新生成'}
                    </span>
                  )}
                </div>
                <Badge tone={item.status === 'succeeded' ? 'ok' : item.status === 'failed' ? 'danger' : 'info'} dot>
                  {item.status === 'succeeded' ? '可下载' : item.status === 'failed' ? '生成失败' : '生成中'}
                </Badge>
                <button
                  type="button"
                  className="button button-secondary button-small"
                  disabled={
                    item.status !== 'succeeded' ||
                    downloading === item.export_id ||
                    item.file_available === false ||
                    !item.integrity_available
                  }
                  onClick={() => void download(item)}
                >
                  {downloading === item.export_id ? '下载中…' : '下载'}
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="result-toolbar">
        <div className="filter-tabs">
          {FILTERS.map((item) => {
            const count =
              item.value === 'all'
                ? parameters.length
                : item.value === 'pending'
                  ? parameters.filter((p) => p.review_status === 'pending').length
                  : item.value === 'reviewed'
                    ? parameters.filter((p) => p.review_status !== 'pending').length
                    : counts[item.value]
            return (
              <button
                key={item.value}
                type="button"
                className={filter === item.value ? 'filter-tab filter-tab-active' : 'filter-tab'}
                onClick={() => setFilter(item.value)}
              >
                {item.label}<span>{count}</span>
              </button>
            )
          })}
        </div>
        <div className="toolbar-right">
          <input
            className="search-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索参数名称或原文"
          />
          <button
            type="button"
            className="button button-ghost button-small"
            onClick={() =>
              setExpanded(
                expanded.size === filtered.length
                  ? new Set()
                  : new Set(filtered.map((item) => item.parameter_id)),
              )
            }
          >
            {expanded.size === filtered.length ? '全部收起' : '展开全部'}
          </button>
        </div>
      </section>

      {filtered.length === 0 ? (
        <EmptyState
          title="没有符合条件的参数"
          description="尝试切换结论筛选、清空搜索词，或刷新评估结果。"
          action={
            <button type="button" className="button button-secondary" onClick={() => { setFilter('all'); setQuery('') }}>
              清除筛选
            </button>
          }
        />
      ) : (
        <div className="result-list">
          {filtered.map((parameter) => (
            <ParameterCard
              key={parameter.parameter_id}
              parameter={parameter}
              expanded={expanded.has(parameter.parameter_id)}
              onToggle={() => toggle(parameter.parameter_id)}
              onReview={() => setReviewTarget(parameter)}
            />
          ))}
        </div>
      )}

      <footer className="page-foot">
        <span>系统自动结论、人工应答与证据分列保存。</span>
        <span>证据不足时保持“无法确认”，不把无法确认改成支持。</span>
      </footer>

      {reviewTarget && (
        <ReviewDialog
          api={api}
          jobId={job.job_id}
          parameter={reviewTarget}
          onClose={() => setReviewTarget(null)}
          onSaved={() => {
            setNotice('人工复核记录已保存；系统自动结论与证据未改变。')
            void load()
          }}
        />
      )}
    </div>
  )
}
