import { useCallback, useEffect, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { CorrectionDialog, type CorrectionMode } from '../components/CorrectionDialog'
import { Alert, Badge, EmptyState, Icon, Spinner } from '../components/ui'
import { formatDate, shortHash, statusLabel, statusTone } from '../format'
import { navigate, useDocumentTitle, usePolling } from '../hooks'
import type { ExtractionJob, TenderParameter } from '../types'

type DialogState = {
  mode: CorrectionMode
  parameter?: TenderParameter
  mergeIds?: string[]
}

export function ExtractionDetail({ api, id }: { api: ApiClient; id: string }) {
  useDocumentTitle('确认提取结果')
  const [job, setJob] = useState<ExtractionJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [checked, setChecked] = useState(false)
  const [starting, setStarting] = useState(false)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [noticeError, setNoticeError] = useState(false)
  const [dialog, setDialog] = useState<DialogState | null>(null)
  const [selected, setSelected] = useState<string[]>([])

  const load = useCallback(async () => {
    try {
      const data = await api.getExtraction(id)
      setJob(data)
      setSelected((current) => current.filter((item) => data.parameters.some((row) => row.id === item)))
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

  const working = job ? job.status === 'queued' || job.status === 'running' : false
  usePolling(() => void load(), working, 1500)

  function showNotice(message: string, failed = false) {
    setNotice(message)
    setNoticeError(failed)
  }

  async function confirmParameter(parameter: TenderParameter) {
    if (!job) return
    setConfirming(parameter.id)
    setNotice('')
    try {
      await api.confirmParameter(job.job_id, parameter.id)
      showNotice('参数已确认。页面已重新读取服务端结果。')
      await load()
    } catch (err) {
      showNotice(err instanceof ApiError ? err.message : '确认参数失败', true)
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
      navigate('/evaluations/' + result.job_id)
    } catch (err) {
      showNotice(err instanceof ApiError ? err.message : '启动评估失败', true)
    } finally {
      setStarting(false)
    }
  }

  function toggleSelected(parameterId: string) {
    setSelected((current) =>
      current.includes(parameterId)
        ? current.filter((item) => item !== parameterId)
        : [...current, parameterId],
    )
  }

  function openMerge() {
    if (!job) return
    const mergeParameters = job.parameters.filter((item) => selected.includes(item.id))
    if (mergeParameters.length < 2) {
      showNotice('请至少选择两条参数再合并。', true)
      return
    }
    setDialog({ mode: 'merge', mergeIds: mergeParameters.map((item) => item.id) })
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

  const editable = !working && job.status !== 'failed'
  const unconfirmed = job.parameters.filter((item) => item.status !== 'confirmed').length
  const reviewRequired = job.parameters.filter((item) => item.review_required).length
  const corrected = job.parameters.filter((item) => item.status === 'corrected').length
  const mergeParameters = job.parameters.filter((item) => selected.includes(item.id))

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
            请先核对参数名称、原文和原子要求。如提取有误，可人工修改、拆分、合并或新增遗漏参数；保存后会读取服务端重建结果。
          </p>
        </div>
        <Badge tone={statusTone(job.status)} dot>{statusLabel(job.status)}</Badge>
      </header>

      {notice && (
        <Alert tone={noticeError ? 'danger' : 'success'} title={noticeError ? '操作未完成' : '操作已保存'}>
          {notice}
        </Alert>
      )}
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
          覆盖状态为 {job.coverage_status}，可能存在未识别条款。请对照招标原文核对，可用“新增遗漏参数”补齐后再启动评估。
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
      {corrected > 0 && (
        <Alert tone="warn" title="参数已人工修正">
          人工修正会改变后续 P2.2 评估的输入；历史评估结论不会自动更新，需要重新启动评估。修正前后内容已写入审计。
        </Alert>
      )}

      <section className="stat-strip">
        <div className="stat"><span>提取参数</span><strong>{job.parameters.length}</strong></div>
        <div className="stat"><span>原子要求</span><strong>{job.parameters.reduce((sum, item) => sum + item.atomic_requirements.length, 0)}</strong></div>
        <div className="stat"><span>跳过区域</span><strong>{job.skipped_count}</strong></div>
        <div className="stat"><span>未确认参数</span><strong>{unconfirmed}</strong></div>
      </section>

      <div className="meta-grid card">
        <div><span>来源文件</span><strong>{job.original_filename || '粘贴文本'}</strong></div>
        <div><span>原始文件哈希</span><strong>{job.original_file_hash ? shortHash(job.original_file_hash, 16) : '未提供'}</strong></div>
        <div><span>提取任务</span><strong>{shortHash(job.job_id, 12)}</strong></div>
        <div><span>创建时间</span><strong>{formatDate(job.created_at)}</strong></div>
      </div>

      {job.parameters.length === 0 ? (
        <EmptyState
          title="没有提取到技术参数"
          description="请检查文件是否包含技术参数表，或返回新建评估粘贴技术参数文本。"
          action={
            <div className="button-row">
              {editable && (
                <button
                  type="button"
                  className="button button-primary"
                  onClick={() => setDialog({ mode: 'add' })}
                >
                  新增遗漏参数
                </button>
              )}
              <button type="button" className="button button-secondary" onClick={() => navigate('/new')}>
                重新提交
              </button>
            </div>
          }
        />
      ) : (
        <section className="card">
          <div className="section-head">
            <div>
              <h2>提取参数清单</h2>
              <p>参数原文用于评估；原子要求是逐条判断的最小单位。人工修正会写回服务端并保留修正记录。</p>
            </div>
            <div className="section-tools">
              <span className="section-count">{job.parameters.length} 条</span>
              {editable && (
                <>
                  <button
                    type="button"
                    className="button button-secondary button-small"
                    onClick={() => setDialog({ mode: 'add' })}
                  >
                    <Icon name="plus" size={15} /> 新增遗漏参数
                  </button>
                  <button
                    type="button"
                    className="button button-ghost button-small"
                    disabled={mergeParameters.length < 2}
                    onClick={openMerge}
                  >
                    合并选中（{mergeParameters.length}）
                  </button>
                </>
              )}
            </div>
          </div>
          <div className="extraction-list">
            {job.parameters.map((parameter) => {
              const confirmed = parameter.status === 'confirmed'
              const isCorrected = parameter.status === 'corrected'
              const anchorStatus = parameter.source_locator
                ? String(parameter.source_locator.anchor_status || '')
                : ''
              return (
                <article key={parameter.id} className="extraction-row">
                  <div className="extraction-index">
                    {editable ? (
                      <label className="merge-select">
                        <input
                          type="checkbox"
                          checked={selected.includes(parameter.id)}
                          onChange={() => toggleSelected(parameter.id)}
                          aria-label={'选择参数 ' + parameter.order_index}
                        />
                        <span>{parameter.order_index}</span>
                      </label>
                    ) : (
                      parameter.order_index
                    )}
                  </div>
                  <div className="extraction-main">
                    <div className="extraction-title">
                      <h3>{parameter.parameter_name}</h3>
                      {parameter.review_required && <Badge tone="warn">需人工确认</Badge>}
                      {isCorrected && <Badge tone="warn">已人工修正</Badge>}
                      <Badge tone={confirmed ? 'ok' : 'muted'}>{confirmed ? '已确认' : '待确认'}</Badge>
                    </div>
                    <p className="parameter-text">{parameter.original_text}</p>
                    {anchorStatus && (
                      <p className="form-hint">
                        来源定位已标记为 {anchorStatus}，原文件行号仅保留在修正记录中，不再作为当前文本锚点。
                      </p>
                    )}
                    <details className="atom-details">
                      <summary>
                        <Icon name="chevron" size={15} />
                        {parameter.atomic_requirements.length} 条原子要求
                      </summary>
                      <ul>
                        {parameter.atomic_requirements.map((atom, index) => (
                          <li key={atom.requirement_id || parameter.id + '-' + index}>
                            <span>{index + 1}</span>
                            {atom.requirement_text || atom.original_text || '原子要求原文缺失'}
                          </li>
                        ))}
                      </ul>
                    </details>
                  </div>
                  <div className="extraction-actions">
                    {editable && (
                      <>
                        <button
                          type="button"
                          className="button button-ghost button-small"
                          onClick={() => setDialog({ mode: 'edit', parameter })}
                        >
                          编辑 / 修正记录
                        </button>
                        <button
                          type="button"
                          className="button button-ghost button-small"
                          onClick={() => setDialog({ mode: 'split', parameter })}
                        >
                          拆分
                        </button>
                      </>
                    )}
                    {!confirmed && editable && (
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
            <span>我已核对提取参数与原子要求（含人工修正），确认启动评估</span>
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

      {dialog && (
        <CorrectionDialog
          api={api}
          jobId={job.job_id}
          mode={dialog.mode}
          parameter={dialog.parameter}
          mergeParameters={
            dialog.mergeIds
              ? job.parameters.filter((item) => dialog.mergeIds?.includes(item.id))
              : []
          }
          onClose={() => setDialog(null)}
          onSaved={(message) => {
            showNotice(message)
            setSelected([])
            setChecked(false)
            void load()
          }}
        />
      )}
    </div>
  )
}
