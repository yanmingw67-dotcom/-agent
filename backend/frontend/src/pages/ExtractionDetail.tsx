import { useCallback, useEffect, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { Alert, Badge, EmptyState, Icon, Spinner } from '../components/ui'
import { formatDate, shortHash, statusLabel, statusTone } from '../format'
import { navigate, useDocumentTitle, usePolling } from '../hooks'
import type { ExtractionJob, TenderParameter } from '../types'

export function ExtractionDetail({ api, id }: { api: ApiClient; id: string }) {
  useDocumentTitle('确认提取结果')
  const [job, setJob] = useState<ExtractionJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [checked, setChecked] = useState(false)
  const [starting, setStarting] = useState(false)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await api.getExtraction(id)
      setJob(data)
      setError('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '提取结果加载失败')
    } finally {
      setLoading(false)
    }
  }, [api, id])

  useEffect(() => {
    void load()
  }, [load])

  const working = job?.status === 'queued' || job?.status === 'running'
  usePolling(() => void load(), Boolean(working), 1500)

  async function confirmParameter(parameter: TenderParameter) {
    if (!job) return
    setConfirming(parameter.id)
    setNotice('')
    try {
      await api.confirmParameter(job.job_id, parameter.id)
      setJob({
        ...job,
        parameters: job.parameters.map((item) =>
          item.id === parameter.id ? { ...item, status: 'confirmed' } : item,
        ),
      })
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : '确认参数失败')
    } finally {
      setConfirming(null)
    }
  }

  async function startEvaluation() {
    if (!job || !checked) return
    setStarting(true)
    setNotice('')
    try {
      const result = await api.createEvaluation(job.job_id)
      navigate(`/evaluations/${result.job_id}`)
    } catch (err) {
      setNotice(err instanceof ApiError ? err.message : '启动评估失败')
    } finally {
      setStarting(false)
    }
  }

  if (loading) {
    return (
      <div className="page">
        <div className="loading-panel"><Spinner label="正在加载提取结果…" /></div>
      </div>
    )
  }

  if (error || !job) {
    return (
      <div className="page">
        <Alert tone="danger" title="无法打开提取任务">{error || '提取任务不存在'}</Alert>
        <button type="button" className="button button-secondary" onClick={() => navigate('/new')}>
          <Icon name="arrow-left" size={16} /> 返回新建评估
        </button>
      </div>
    )
  }

  const unconfirmed = job.parameters.filter((item) => item.status !== 'confirmed').length
  const reviewRequired = job.parameters.filter((item) => item.review_required).length

  return (
    <div className="page page-wide">
      <button type="button" className="back-link" onClick={() => navigate('/tasks')}>
        <Icon name="arrow-left" size={16} /> 返回任务
      </button>

      <header className="page-head">
        <div>
          <span className="eyebrow">第 3 步 · 确认提取</span>
          <h1>核对招标参数提取结果</h1>
          <p>
            请先核对参数名称、原文和原子要求。确认无误后再启动评估；评估会固定使用当前品类已发布的产品知识版本。
          </p>
        </div>
        <Badge tone={statusTone(job.status)} dot>{statusLabel(job.status)}</Badge>
      </header>

      {notice && <Alert tone="danger" title="操作未完成">{notice}</Alert>}
      {working && (
        <Alert tone="info" title="正在提取">
          系统正在解析招标文件并生成原子要求，页面会自动刷新。
        </Alert>
      )}
      {job.status === 'failed' && (
        <Alert tone="danger" title="提取失败">
          错误码 {job.error_code || '未知'}。请检查文件格式与内容后重新提交。
        </Alert>
      )}
      {job.coverage_incomplete && (
        <Alert tone="warn" title="提取覆盖不完整">
          覆盖状态为 {job.coverage_status}，可能存在未识别条款。请对照招标原文核对后再启动评估。
        </Alert>
      )}
      {job.ambiguous_count > 0 && (
        <Alert tone="warn" title="存在歧义区域">
          有 {job.ambiguous_count} 个区域存在歧义，建议逐条核对参数原文与原子要求。
        </Alert>
      )}
      {reviewRequired > 0 && (
        <Alert tone="warn" title="需人工确认的参数">
          有 {reviewRequired} 条参数被标记为需人工确认。可在下方逐条确认，或整体核对后启动评估。
        </Alert>
      )}

      <section className="stat-strip">
        <div className="stat"><span>提取参数</span><strong>{job.extracted_count}</strong></div>
        <div className="stat"><span>原子要求</span><strong>{job.parameters.reduce((sum, item) => sum + item.atomic_requirements.length, 0)}</strong></div>
        <div className="stat"><span>跳过区域</span><strong>{job.skipped_count}</strong></div>
        <div className="stat"><span>未确认参数</span><strong>{unconfirmed}</strong></div>
      </section>

      <div className="meta-grid card">
        <div><span>来源文件</span><strong>{job.original_filename || '粘贴文本'}</strong></div>
        <div><span>提取任务</span><strong>{shortHash(job.job_id, 12)}</strong></div>
        <div><span>创建时间</span><strong>{formatDate(job.created_at)}</strong></div>
        <div><span>覆盖状态</span><strong>{job.coverage_status}</strong></div>
      </div>

      {job.parameters.length === 0 ? (
        <EmptyState
          title="没有提取到技术参数"
          description="请检查文件是否包含技术参数表，或返回新建评估粘贴技术参数文本。"
          action={
            <button type="button" className="button button-primary" onClick={() => navigate('/new')}>
              重新提交
            </button>
          }
        />
      ) : (
        <section className="card">
          <div className="section-head">
            <div>
              <h2>提取参数清单</h2>
              <p>参数原文用于评估；原子要求是逐条判断的最小单位。确认操作只记录核对状态，不修改原文。</p>
            </div>
            <span className="section-count">{job.parameters.length} 条</span>
          </div>
          <div className="extraction-list">
            {job.parameters.map((parameter) => {
              const confirmed = parameter.status === 'confirmed'
              return (
                <article key={parameter.id} className="extraction-row">
                  <div className="extraction-index">{parameter.order_index}</div>
                  <div className="extraction-main">
                    <div className="extraction-title">
                      <h3>{parameter.parameter_name}</h3>
                      {parameter.review_required && <Badge tone="warn">需人工确认</Badge>}
                      <Badge tone={confirmed ? 'ok' : 'muted'}>{confirmed ? '已确认' : '待确认'}</Badge>
                    </div>
                    <p className="parameter-text">{parameter.original_text}</p>
                    <details className="atom-details">
                      <summary>
                        <Icon name="chevron" size={15} />
                        {parameter.atomic_requirements.length} 条原子要求
                      </summary>
                      <ul>
                        {parameter.atomic_requirements.map((atom, index) => (
                          <li key={`${parameter.id}-${index}`}>
                            <span>{index + 1}</span>
                            {atom.requirement_text || atom.original_text || '原子要求原文缺失'}
                          </li>
                        ))}
                      </ul>
                    </details>
                  </div>
                  <div className="extraction-actions">
                    {!confirmed && (
                      <button
                        type="button"
                        className="button button-ghost button-small"
                        disabled={confirming === parameter.id}
                        onClick={() => void confirmParameter(parameter)}
                      >
                        {confirming === parameter.id ? '确认中…' : '确认此条'}
                      </button>
                    )}
                  </div>
                </article>
              )
            })}
          </div>
        </section>
      )}

      <section className="start-panel card">
        <div>
          <h2>启动逐条评估</h2>
          <p>评估会读取任务固定的知识发布版本，逐条给出支持、不支持或无法确认，并保留证据定位。人工复核在评估完成后进行。</p>
          <label className="check-line">
            <input
              type="checkbox"
              checked={checked}
              onChange={(event) => setChecked(event.target.checked)}
              disabled={working || job.status === 'failed'}
            />
            <span>我已核对提取参数与原子要求，确认启动评估</span>
          </label>
        </div>
        <button
          type="button"
          className="button button-primary"
          disabled={!checked || starting || working || job.status === 'failed'}
          onClick={() => void startEvaluation()}
        >
          {starting ? <Spinner label="正在启动…" /> : '确认提取并启动评估'}
        </button>
      </section>
    </div>
  )
}
