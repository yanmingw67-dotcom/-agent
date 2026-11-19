import { useCallback, useEffect, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { Alert, Badge, Icon, ProgressBar, Spinner } from '../components/ui'
import {
  countLabel,
  formatDate,
  percent,
  shortHash,
  statusLabel,
  statusTone,
} from '../format'
import { navigate, useDocumentTitle, usePolling } from '../hooks'
import type { EvaluationJob } from '../types'

export function EvaluationProgress({ api, id }: { api: ApiClient; id: string }) {
  useDocumentTitle('评估进度')
  const [job, setJob] = useState<EvaluationJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retrying, setRetrying] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api.getEvaluation(id)
      setJob(data)
      setError('')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '评估进度加载失败')
    } finally {
      setLoading(false)
    }
  }, [api, id])

  useEffect(() => {
    void load()
  }, [load])

  const active = job?.status === 'queued' || job?.status === 'running'
  usePolling(() => void load(), Boolean(active), 1800)

  async function retry() {
    setRetrying(true)
    setError('')
    try {
      await api.retryEvaluation(id)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '重试失败')
    } finally {
      setRetrying(false)
    }
  }

  if (loading) {
    return <div className="page"><div className="loading-panel"><Spinner label="正在加载评估进度…" /></div></div>
  }

  if (error || !job) {
    return (
      <div className="page">
        <Alert tone="danger" title="无法加载评估任务">{error || '评估任务不存在'}</Alert>
        <button type="button" className="button button-secondary" onClick={() => navigate('/tasks')}>
          <Icon name="arrow-left" size={16} /> 返回任务
        </button>
      </div>
    )
  }

  const completed = job.checkpoint?.completed ?? 0
  const total = job.checkpoint?.total ?? job.parameter_count
  const progress = percent(completed, total)
  const done = job.status === 'succeeded' || job.status === 'partial'

  return (
    <div className="page page-narrow">
      <button type="button" className="back-link" onClick={() => navigate('/tasks')}>
        <Icon name="arrow-left" size={16} /> 返回任务
      </button>

      <header className="page-head">
        <div>
          <span className="eyebrow">逐条评估</span>
          <h1>{done ? '评估已完成' : '正在评估招标参数'}</h1>
          <p>
            {done
              ? '系统自动结论已保存，可逐条查看理由与固定发布版本中的证据，再进行人工复核和导出。'
              : '系统正在按参数逐条判断，并绑定任务创建时固定的知识发布版本。离开页面前结果会持续保存。'}
          </p>
        </div>
        <Badge tone={statusTone(job.status)} dot>{statusLabel(job.status)}</Badge>
      </header>

      {job.status === 'failed' && (
        <Alert tone="danger" title="评估失败关闭">
          错误码 {job.error_code || '未知'}。失败参数不会被当作支持；可检查固定知识版本后重试。
        </Alert>
      )}
      {job.status === 'partial' && (
        <Alert tone="warn" title="部分参数评估完成">
          部分参数执行失败并按“无法确认”关闭。请查看失败原因，失败参数不得改为支持。
        </Alert>
      )}
      {job.error_code && job.status !== 'failed' && (
        <Alert tone="warn" title="任务存在错误码">{job.error_code}</Alert>
      )}

      <section className="card progress-card">
        <div className="progress-head">
          <div>
            <span className="muted">评估进度</span>
            <strong>{completed} / {total} 条参数</strong>
          </div>
          <span className="progress-percent">{progress}%</span>
        </div>
        <ProgressBar value={progress} />
        {active && (
          <div className="progress-live">
            <Spinner label="评估进行中，页面会自动刷新" />
          </div>
        )}
        <div className="timeline">
          <div className="timeline-item timeline-done"><span><Icon name="check" size={13} /></span>提取确认</div>
          <div className="timeline-item timeline-done"><span><Icon name="check" size={13} /></span>固定知识版本</div>
          <div className={`timeline-item ${done ? 'timeline-done' : 'timeline-active'}`}><span>{done ? <Icon name="check" size={13} /> : '3'}</span>逐条评估</div>
          <div className="timeline-item"><span>4</span>人工复核</div>
          <div className="timeline-item"><span>5</span>导出 Word / Excel</div>
        </div>
      </section>

      <section className="stat-strip">
        <div className="stat"><span>支持</span><strong className="text-ok">{countLabel(job.counts?.支持)}</strong></div>
        <div className="stat"><span>不支持</span><strong className="text-danger">{countLabel(job.counts?.不支持)}</strong></div>
        <div className="stat"><span>无法确认</span><strong className="text-warn">{countLabel(job.counts?.无法确认)}</strong></div>
        <div className="stat"><span>参数总数</span><strong>{job.parameter_count}</strong></div>
      </section>

      <div className="meta-grid card">
        <div><span>任务编号</span><strong>{shortHash(job.job_id, 14)}</strong></div>
        <div><span>产品品类</span><strong>{job.category_name || '—'}</strong></div>
        <div><span>固定发布版本</span><strong>{shortHash(job.publication_id, 12)}</strong></div>
        <div><span>创建时间</span><strong>{formatDate(job.created_at)}</strong></div>
      </div>

      <div className="form-actions">
        <div className="form-hint">
          <Icon name="alert" size={16} />
          <span>评估结果来自已持久化记录；页面不会在加载时重新判定。</span>
        </div>
        {done ? (
          <button type="button" className="button button-primary" onClick={() => navigate(`/tasks/${job.job_id}`)}>
            查看逐条结果
          </button>
        ) : job.status === 'failed' ? (
          <button type="button" className="button button-secondary" onClick={() => void retry()} disabled={retrying}>
            {retrying ? <Spinner label="正在重试…" /> : '重试评估'}
          </button>
        ) : (
          <button type="button" className="button button-ghost" onClick={() => void load()}>
            <Icon name="refresh" size={16} /> 刷新进度
          </button>
        )}
      </div>
    </div>
  )
}
