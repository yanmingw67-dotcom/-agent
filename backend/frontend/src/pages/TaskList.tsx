import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { Alert, Badge, EmptyState, Icon, Spinner } from '../components/ui'
import {
  countLabel,
  formatDate,
  percent,
  shortHash,
  statusLabel,
  statusTone,
} from '../format'
import { navigate, useDocumentTitle } from '../hooks'
import type { Category, EvaluationJob } from '../types'

const STATUS_OPTIONS = [
  { value: '', label: '全部状态' },
  { value: 'succeeded', label: '评估完成' },
  { value: 'partial', label: '部分完成' },
  { value: 'running', label: '评估中' },
  { value: 'queued', label: '排队中' },
  { value: 'failed', label: '评估失败' },
]

export function TaskList({ api }: { api: ApiClient }) {
  useDocumentTitle('评估任务')
  const [tasks, setTasks] = useState<EvaluationJob[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [status, setStatus] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [list, categoryList] = await Promise.all([
        api.listTasks({ status: status || undefined, categoryId: categoryId || undefined }),
        api.listCategories(),
      ])
      setTasks(list.items)
      setCategories(categoryList)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '任务列表加载失败')
    } finally {
      setLoading(false)
    }
  }, [api, status, categoryId])

  useEffect(() => {
    void load()
  }, [load])

  const stats = useMemo(() => {
    const total = tasks.length
    const done = tasks.filter((item) => item.status === 'succeeded').length
    const reviewPending = tasks.reduce(
      (sum, item) => sum + (item.pending_review_count ?? 0),
      0,
    )
    return { total, done, reviewPending }
  }, [tasks])

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <span className="eyebrow">评估任务</span>
          <h1>我的评估任务</h1>
          <p>查看自动结论、逐条证据与人工复核状态，或从新建评估开始一次招标参数评估。</p>
        </div>
        <button type="button" className="button button-primary" onClick={() => navigate('/new')}>
          <Icon name="plus" size={17} />
          新建评估
        </button>
      </header>

      <section className="stat-strip">
        <div className="stat">
          <span>当前列表任务</span>
          <strong>{stats.total}</strong>
        </div>
        <div className="stat">
          <span>评估完成</span>
          <strong>{stats.done}</strong>
        </div>
        <div className="stat">
          <span>待人工复核</span>
          <strong>{stats.reviewPending}</strong>
        </div>
        <div className="stat stat-note">
          <span>说明</span>
          <strong className="stat-text">自动结论与人工应答分列保存</strong>
        </div>
      </section>

      <section className="toolbar">
        <div className="filters">
          <label className="filter">
            <span>状态</span>
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="filter">
            <span>产品品类</span>
            <select value={categoryId} onChange={(event) => setCategoryId(event.target.value)}>
              <option value="">全部品类</option>
              {categories.map((category) => (
                <option key={category.id} value={category.id}>
                  {category.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <button type="button" className="button button-ghost" onClick={() => void load()}>
          <Icon name="refresh" size={16} />
          刷新
        </button>
      </section>

      {error && <Alert tone="danger" title="无法加载任务">{error}</Alert>}

      {loading ? (
        <div className="loading-panel">
          <Spinner label="正在加载评估任务…" />
        </div>
      ) : tasks.length === 0 ? (
        <EmptyState
          title="还没有评估任务"
          description="选择一个产品品类并提交招标技术参数，系统会先生成提取结果，确认后再启动逐条评估。"
          action={
            <button type="button" className="button button-primary" onClick={() => navigate('/new')}>
              <Icon name="plus" size={17} />
              新建评估
            </button>
          }
        />
      ) : (
        <div className="task-list">
          {tasks.map((task) => {
            const reviewed = task.reviewed_count ?? 0
            const pending = task.pending_review_count ?? 0
            const total = reviewed + pending
            const progress = percent(reviewed, total)
            const done = task.status === 'succeeded' || task.status === 'partial'
            return (
              <article key={task.job_id} className={`task-card task-${statusTone(task.status)}`}>
                <div className="task-main">
                  <div className="task-title-row">
                    <h2>{task.category_name || '未命名品类'}</h2>
                    <Badge tone={statusTone(task.status)} dot>
                      {statusLabel(task.status)}
                    </Badge>
                    {task.failed_count ? (
                      <Badge tone="danger">失败 {task.failed_count} 条</Badge>
                    ) : null}
                  </div>
                  <div className="task-meta">
                    <span>任务 {shortHash(task.job_id, 10)}</span>
                    <span>创建 {formatDate(task.created_at)}</span>
                    <span>固定发布 {shortHash(task.publication_id, 10)}</span>
                  </div>
                  <div className="task-counts">
                    <span className="count count-ok">支持 <strong>{countLabel(task.counts?.支持)}</strong></span>
                    <span className="count count-danger">不支持 <strong>{countLabel(task.counts?.不支持)}</strong></span>
                    <span className="count count-warn">无法确认 <strong>{countLabel(task.counts?.无法确认)}</strong></span>
                    <span className="count count-muted">参数 <strong>{task.parameter_count}</strong></span>
                  </div>
                  {done ? (
                    <div className="review-inline">
                      <span>人工复核</span>
                      <div className="mini-progress">
                        <span style={{ width: `${progress}%` }} />
                      </div>
                      <span>
                        {reviewed} / {total} 已复核{pending > 0 ? `，剩余 ${pending} 条` : ''}
                      </span>
                    </div>
                  ) : (
                    <div className="review-inline">
                      <Icon name="clock" size={15} />
                      <span>
                        进度 {task.checkpoint?.completed ?? 0} / {task.checkpoint?.total ?? task.parameter_count}
                      </span>
                    </div>
                  )}
                </div>
                <div className="task-actions">
                  <button
                    type="button"
                    className="button button-secondary"
                    onClick={() =>
                      navigate(done ? `/tasks/${task.job_id}` : `/evaluations/${task.job_id}`)
                    }
                  >
                    {done ? '查看结果' : '查看进度'}
                  </button>
                  <div className="task-flags">
                    <span className={`flag ${task.counts?.支持 ? 'flag-ok' : ''}`}>
                      支持 {countLabel(task.counts?.支持)}
                    </span>
                    <span className={`flag ${pending > 0 ? 'flag-danger' : 'flag-ok'}`}>
                      待复核 {pending}
                    </span>
                  </div>
                </div>
              </article>
            )
          })}
        </div>
      )}

      <footer className="page-foot">
        <span>共 {tasks.length} 条任务</span>
        <span>产品知识由管理员维护；本页不提供源码或产品文档上传。</span>
      </footer>
    </div>
  )
}
