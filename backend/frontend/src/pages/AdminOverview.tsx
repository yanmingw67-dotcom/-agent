import { useCallback, useEffect, useState } from 'react'
import type { ApiClient } from '../api'
import { ModelGateFacts } from './ModelGateFacts'
import { ApiError } from '../api'
import { Alert, Badge, Icon, Modal, Spinner } from '../components/ui'
import { formatDate, shortHash, statusLabel, statusTone } from '../format'
import { navigate, useDocumentTitle } from '../hooks'
import type { AdminOverview as OverviewData } from '../types'

export function AdminOverview({ api }: { api: ApiClient }) {
  useDocumentTitle('管理员工作台')
  const [data, setData] = useState<OverviewData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showCreate, setShowCreate] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await api.adminOverview())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '管理员概览加载失败')
    } finally {
      setLoading(false)
    }
  }, [api])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <div className="page admin-page">
      <header className="page-head">
        <div>
          <span className="eyebrow">P3.1-A · 管理员</span>
          <h1>产品知识维护工作台</h1>
          <p>
            上传受控源码与技术文档，构建待审知识，逐条人工审核后再通过既有发布服务生效。
            所有写操作都由服务端管理员权限校验并写入审计日志。
          </p>
        </div>
        <div className="head-actions">
          <button type="button" className="button button-ghost" onClick={() => void load()}>
            <Icon name="refresh" size={16} />
            刷新
          </button>
          <button type="button" className="button button-primary" onClick={() => setShowCreate(true)}>
            <Icon name="plus" size={17} />
            新建产品品类
          </button>
        </div>
      </header>

      {error && (
        <Alert tone="danger" title="无法加载管理员概览">
          {error}
        </Alert>
      )}

      {loading ? (
        <div className="loading-panel">
          <Spinner label="正在加载产品知识状态…" />
        </div>
      ) : data ? (
        <>
          <section className="stat-strip admin-stat-strip">
            <div className="stat">
              <span>产品品类</span>
              <strong>{data.summary.category_count}</strong>
            </div>
            <div className="stat">
              <span>已有生效知识</span>
              <strong>{data.summary.ready_category_count}</strong>
            </div>
            <div className="stat">
              <span>待审核候选</span>
              <strong>{data.summary.pending_review_count}</strong>
            </div>
            <div className="stat">
              <span>未发布暂存快照</span>
              <strong>{data.summary.unpublished_snapshot_count}</strong>
            </div>
            <div className="stat">
              <span>失败任务</span>
              <strong className={data.summary.failed_job_count ? 'text-danger' : ''}>
                {data.summary.failed_job_count}
              </strong>
            </div>
          </section>

          <div className="admin-overview-grid">
            <section className="panel">
              <div className="panel-head">
                <div>
                  <h2>产品品类</h2>
                  <p>每品类只有一套当前生效知识；历史快照仅用于审计、任务固定与回滚。</p>
                </div>
                <button
                  type="button"
                  className="button button-ghost button-small"
                  onClick={() => navigate('/admin/categories')}
                >
                  查看全部
                </button>
              </div>
              {data.categories.length === 0 ? (
                <div className="admin-empty-inline">
                  还没有产品品类。新建品类后先上传受控源码 ZIP，再构建索引。
                </div>
              ) : (
                <div className="admin-category-list">
                  {data.categories.map((category) => (
                    <button
                      key={category.id}
                      type="button"
                      className="admin-category-row"
                      onClick={() => navigate(`/admin/categories/${category.id}`)}
                    >
                      <div className="admin-category-main">
                        <div className="admin-category-title">
                          <strong>{category.name}</strong>
                          <code>{category.code}</code>
                          {category.knowledge_ready ? (
                            <Badge tone="ok" dot>
                              已发布
                            </Badge>
                          ) : (
                            <Badge tone="warn">未发布</Badge>
                          )}
                        </div>
                        <div className="admin-meta-line">
                          <span>文档 {category.document_count}</span>
                          <span>待审 {category.pending_review_count}</span>
                          <span>生效能力 {category.published_capability_count}</span>
                          <span>发布 {formatDate(category.published_at)}</span>
                        </div>
                      </div>
                      <div className="admin-category-side">
                        {category.latest_job ? (
                          <Badge tone={statusTone(category.latest_job.status)}>
                            {category.latest_job.job_type} · {statusLabel(category.latest_job.status)}
                          </Badge>
                        ) : (
                          <Badge tone="muted">无任务</Badge>
                        )}
                        <Icon name="chevron" size={16} />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </section>

            <aside className="admin-side-stack">
              <section className="panel">
                <div className="panel-head">
                  <div>
                    <h2>模型授权</h2>
                    <p>授权只在服务端配置中生效。</p>
                  </div>
                  <Badge tone="ok">前端不可覆盖</Badge>
                </div>
                <dl className="admin-kv">
                  <div>
                    <dt>知识构建模型</dt>
                    <dd>
                      {data.model_authorization.builder.model_configured ? '已配置' : '未配置'} ·{' '}
                      {data.model_authorization.builder.status}
                    </dd>
                  </div>
                  <ModelGateFacts builder={data.model_authorization.builder} />
                  <div>
                    <dt>已开启的运行时分类（不等于审批）</dt>
                    <dd>
                      {data.model_authorization.builder.allow_synthetic ? '合成公开' : '—'}
                      {' / '}
                      {data.model_authorization.builder.allow_enterprise ? '企业资料' : '—'}
                    </dd>
                  </div>
                  <div>
                    <dt>招标提取</dt>
                    <dd>{data.model_authorization.tender.model_mode}</dd>
                  </div>
                  <div>
                    <dt>参数评估</dt>
                    <dd>{data.model_authorization.evaluation.model_mode}</dd>
                  </div>
                </dl>
                <p className="admin-fineprint">
                  上传与索引不会自动调用外部模型；只有管理员显式发起调查后，服务端才按已配置授权执行。
                </p>
              </section>

              <section className="panel">
                <div className="panel-head">
                  <div>
                    <h2>建库边界</h2>
                    <p>当前产品知识的来源要求。</p>
                  </div>
                </div>
                <Alert tone="warn" title="仅文档建库当前不支持">
                  {data.limits.document_only_note}
                </Alert>
                <p className="admin-fineprint">
                  管理员技术文档支持 {data.limits.document_types.join('、')}，必须挂载在由受控源码派生的暂存快照中。
                </p>
              </section>
            </aside>
          </div>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>最近任务</h2>
                <p>任务状态由后台 worker 推进；页面不会绕过队列直接执行。</p>
              </div>
            </div>
            {data.recent_jobs.length === 0 ? (
              <div className="admin-empty-inline">尚无知识构建任务。</div>
            ) : (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>品类</th>
                      <th>任务</th>
                      <th>阶段</th>
                      <th>状态</th>
                      <th>错误</th>
                      <th>创建时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recent_jobs.map((job) => (
                      <tr key={job.id}>
                        <td>{job.category_name || shortHash(job.category_id, 8)}</td>
                        <td>
                          <code>{job.job_type}</code> {shortHash(job.id, 10)}
                        </td>
                        <td>{job.stage}</td>
                        <td>
                          <Badge tone={statusTone(job.status)}>{statusLabel(job.status)}</Badge>
                        </td>
                        <td>{job.error_code || '—'}</td>
                        <td>{formatDate(job.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>最近发布</h2>
                <p>发布记录只用于审计与回滚；新发布不改变历史评估任务的固定版本引用。</p>
              </div>
            </div>
            {data.recent_publications.length === 0 ? (
              <div className="admin-empty-inline">尚无发布记录。</div>
            ) : (
              <div className="admin-publication-list">
                {data.recent_publications.map((publication) => (
                  <div key={publication.publication_id} className="admin-publication-row">
                    <div>
                      <strong>{publication.category_name || shortHash(publication.category_id, 8)}</strong>
                      <span className="admin-meta-line">
                        发布 {shortHash(publication.publication_id, 12)} · 快照{' '}
                        {shortHash(publication.snapshot_id, 12)}
                      </span>
                    </div>
                    <div className="admin-row-right">
                      <Badge tone="ok">{publication.capability_count} 条能力</Badge>
                      <span>{formatDate(publication.created_at)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      ) : null}

      {showCreate && (
        <CreateCategoryModal
          api={api}
          onClose={() => setShowCreate(false)}
          onCreated={(id) => {
            setShowCreate(false)
            navigate(`/admin/categories/${id}`)
          }}
        />
      )}
    </div>
  )
}

function CreateCategoryModal({
  api,
  onClose,
  onCreated,
}: {
  api: ApiClient
  onClose: () => void
  onCreated: (id: string) => void
}) {
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit() {
    setBusy(true)
    setError('')
    try {
      const category = await api.createCategory({ code: code.trim(), name: name.trim() })
      onCreated(category.id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '创建品类失败')
      setBusy(false)
    }
  }
  return (
    <Modal
      title="新建产品品类"
      subtitle="品类编码用于服务端作用域，创建后不可与已有编码重复。"
      onClose={onClose}
    >
      <div className="form-stack">
        {error && <Alert tone="danger">{error}</Alert>}
        <label className="field">
          <span>品类编码</span>
          <input
            value={code}
            onChange={(event) => setCode(event.target.value)}
            placeholder="例如 waf"
            pattern="[a-z][a-z0-9_-]{0,63}"
          />
          <small>小写字母开头，可含数字、下划线和连字符。</small>
        </label>
        <label className="field">
          <span>品类名称</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如 Web 应用防火墙"
          />
        </label>
        <div className="modal-actions">
          <button type="button" className="button button-ghost" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={busy || !code.trim() || !name.trim()}
            onClick={() => void submit()}
          >
            {busy ? '创建中…' : '创建并进入工作台'}
          </button>
        </div>
      </div>
    </Modal>
  )
}
