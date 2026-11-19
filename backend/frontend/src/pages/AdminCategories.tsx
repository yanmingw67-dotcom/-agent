import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { Alert, Badge, Icon, Spinner } from '../components/ui'
import { formatDate, statusLabel, statusTone } from '../format'
import { navigate, useDocumentTitle } from '../hooks'
import type { AdminCategoryOverview } from '../types'

export function AdminCategories({ api }: { api: ApiClient }) {
  useDocumentTitle('产品品类 · 管理员')
  const [items, setItems] = useState<AdminCategoryOverview[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await api.adminCategories()
      setItems(result.items)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '品类列表加载失败')
    } finally {
      setLoading(false)
    }
  }, [api])

  useEffect(() => {
    void load()
  }, [load])

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase()
    if (!term) return items
    return items.filter(
      (item) =>
        item.name.toLowerCase().includes(term) || item.code.toLowerCase().includes(term),
    )
  }, [items, query])

  return (
    <div className="page admin-page">
      <header className="page-head">
        <div>
          <span className="eyebrow">管理员工作台</span>
          <h1>产品品类</h1>
          <p>选择一个品类进入资料、构建、审核与发布工作区。</p>
        </div>
        <button type="button" className="button button-ghost" onClick={() => navigate('/admin')}>
          <Icon name="arrow-left" size={17} />
          返回概览
        </button>
      </header>

      <section className="toolbar">
        <div className="filters">
          <label className="filter filter-grow">
            <span>搜索</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="按名称或编码筛选"
            />
          </label>
        </div>
        <button type="button" className="button button-ghost" onClick={() => void load()}>
          <Icon name="refresh" size={16} />
          刷新
        </button>
      </section>

      {error && <Alert tone="danger">{error}</Alert>}
      {loading ? (
        <div className="loading-panel">
          <Spinner label="正在加载产品品类…" />
        </div>
      ) : filtered.length === 0 ? (
        <Alert tone="info" title="没有匹配的品类">
          返回概览后可新建产品品类。
        </Alert>
      ) : (
        <div className="admin-category-grid">
          {filtered.map((item) => (
            <article key={item.id} className="admin-category-card">
              <div className="admin-category-card-head">
                <div>
                  <h2>{item.name}</h2>
                  <code>{item.code}</code>
                </div>
                {item.knowledge_ready ? (
                  <Badge tone="ok" dot>
                    已发布
                  </Badge>
                ) : (
                  <Badge tone="warn">未发布</Badge>
                )}
              </div>
              <dl className="admin-kv compact">
                <div>
                  <dt>当前快照</dt>
                  <dd>{item.active_snapshot_id ? item.active_snapshot_id.slice(0, 12) : '—'}</dd>
                </div>
                <div>
                  <dt>生效能力</dt>
                  <dd>{item.published_capability_count}</dd>
                </div>
                <div>
                  <dt>文档</dt>
                  <dd>{item.document_count}</dd>
                </div>
                <div>
                  <dt>待审核</dt>
                  <dd>{item.pending_review_count}</dd>
                </div>
                <div>
                  <dt>最近发布</dt>
                  <dd>{formatDate(item.published_at)}</dd>
                </div>
                <div>
                  <dt>最近任务</dt>
                  <dd>
                    {item.latest_job ? (
                      <Badge tone={statusTone(item.latest_job.status)}>
                        {statusLabel(item.latest_job.status)}
                      </Badge>
                    ) : (
                      '—'
                    )}
                  </dd>
                </div>
              </dl>
              <button
                type="button"
                className="button button-secondary"
                onClick={() => navigate(`/admin/categories/${item.id}`)}
              >
                进入工作区
              </button>
            </article>
          ))}
        </div>
      )}
    </div>
  )
}
