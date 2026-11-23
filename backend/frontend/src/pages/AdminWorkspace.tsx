import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { Alert, Badge, Icon, Modal, Spinner } from '../components/ui'
import {
  formatBytes,
  formatDate,
  shortHash,
  statusLabel,
  statusTone,
  uploadErrorMessage,
} from '../format'
import { navigate, useDocumentTitle, usePolling } from '../hooks'
import type {
  AdminDocument,
  AdminReleaseCheck,
  AdminSnapshot,
  AdminWorkspace as Workspace,
  RetrievalItem,
} from '../types'
import { AdminReviewPanel } from './AdminReviewPanel'

const TABS = [
  { key: 'overview', label: '概览与构建' },
  { key: 'documents', label: '技术文档' },
  { key: 'review', label: '能力审核' },
  { key: 'publish', label: '发布与回滚' },
  { key: 'audit', label: '审计日志' },
]

type SourceStage =
  | 'idle'
  | 'uploading'
  | 'uploaded'
  | 'extracting'
  | 'extracted'
  | 'indexing'
  | 'indexed'
  | 'failed'

export function AdminWorkspace({
  api,
  id,
  initialTab,
}: {
  api: ApiClient
  id: string
  initialTab?: string
}) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [selectedSnapshotId, setSelectedSnapshotId] = useState('')
  const [tab, setTab] = useState(
    initialTab && TABS.some((item) => item.key === initialTab) ? initialTab : 'overview',
  )
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [investigateOpen, setInvestigateOpen] = useState(false)
  const [sourceStage, setSourceStage] = useState<SourceStage>('idle')
  const [sourceProgress, setSourceProgress] = useState(0)
  const [sourceSnapshotId, setSourceSnapshotId] = useState('')
  const [sourceFileInfo, setSourceFileInfo] = useState<{ name: string; size: number } | null>(null)

  useDocumentTitle(workspace ? `${workspace.category.name} · 管理员` : '产品知识工作区')

  const load = useCallback(
    async (snapshotId = selectedSnapshotId) => {
      setLoading(true)
      setError('')
      try {
        const result = await api.adminWorkspace(id, snapshotId || undefined)
        setWorkspace(result)
        if (!snapshotId && result.working_snapshot) {
          setSelectedSnapshotId(result.working_snapshot.id)
        }
        if (snapshotId && !result.working_snapshot) {
          setSelectedSnapshotId('')
        }
      } catch (err) {
        setError(err instanceof ApiError ? err.message : '工作区加载失败')
      } finally {
        setLoading(false)
      }
    },
    [api, id, selectedSnapshotId],
  )

  useEffect(() => {
    void load(selectedSnapshotId)
    // 快照选择变化时才重新拉取，避免与轮询互相放大。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, id, selectedSnapshotId])

  const activeJobs = useMemo(
    () =>
      workspace?.jobs.some((job) => job.status === 'queued' || job.status === 'running') ?? false,
    [workspace],
  )
  usePolling(
    () => {
      if (selectedSnapshotId) void load(selectedSnapshotId)
    },
    activeJobs,
    3000,
  )

  const working = workspace?.working_snapshot ?? null
  const derivedEditable =
    Boolean(working) &&
    working?.status === 'extracted' &&
    working?.derivation_type === 'document_update'

  useEffect(() => {
    if (!sourceSnapshotId || !working || working.id !== sourceSnapshotId) return
    if (working.status === 'failed') {
      setSourceStage('failed')
    } else if (working.index_status === 'indexed') {
      setSourceStage('indexed')
    } else if (working.index_status === 'queued' || working.index_status === 'running') {
      setSourceStage('indexing')
    } else if (working.status === 'extracted') {
      setSourceStage('extracted')
    } else if (
      working.status === 'running' ||
      working.status === 'validating' ||
      working.status === 'uploaded'
    ) {
      setSourceStage('extracting')
    }
  }, [sourceSnapshotId, working])

  async function buildIndex() {
    if (!working) return
    setBusy('build')
    setError('')
    setNotice('')
    try {
      const result = await api.buildSnapshot(working.id)
      setNotice(`已创建索引任务 ${shortHash(result.job_id, 10)}；索引完成后才能发起调查。`)
      await load(working.id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '构建索引失败')
    } finally {
      setBusy('')
    }
  }

  async function deriveDocuments() {
    setBusy('derive')
    setError('')
    setNotice('')
    try {
      const result = await api.deriveDocumentStaging(id)
      setSelectedSnapshotId(result.snapshot_id)
      setNotice(
        `已从源码快照 ${shortHash(result.derived_from_snapshot_id, 10)} 派生文档暂存快照，复制 ${result.documents_copied} 份文档。已发布快照未被修改。`,
      )
      setTab('documents')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '派生文档暂存快照失败')
    } finally {
      setBusy('')
    }
  }

  async function uploadSourceFile(file: File) {
    setBusy('source')
    setError('')
    setNotice('')
    setSourceStage('uploading')
    setSourceProgress(0)
    setSourceFileInfo({ name: file.name, size: file.size })
    try {
      const result = await api.uploadSourceWithProgress(id, file, setSourceProgress)
      setSourceProgress(100)
      setSourceStage('uploaded')
      setSourceSnapshotId(result.snapshot_id)
      setNotice(
        `上传成功：源码包已入库，快照 ${shortHash(result.snapshot_id, 10)} 正在等待后台解压。` +
          '解压完成后状态会变为“解压成功”，再构建索引。',
      )
      setSelectedSnapshotId(result.snapshot_id)
      await load(result.snapshot_id)
    } catch (err) {
      setSourceStage('failed')
      setError(
        err instanceof ApiError
          ? uploadErrorMessage(err.code, err.status, err.message)
          : '源码上传失败',
      )
    } finally {
      setBusy('')
    }
  }

  if (loading && !workspace) {
    return (
      <div className="page">
        <div className="loading-panel">
          <Spinner label="正在加载产品知识工作区…" />
        </div>
      </div>
    )
  }

  if (!workspace) {
    return (
      <div className="page">
        <Alert tone="danger" title="无法打开工作区">
          {error || '品类不存在或当前账号无管理员权限。'}
        </Alert>
        <button type="button" className="button button-ghost" onClick={() => navigate('/admin')}>
          返回管理员概览
        </button>
      </div>
    )
  }

  return (
    <div className="page admin-page admin-workspace">
      <header className="page-head">
        <div>
          <button type="button" className="back-link" onClick={() => navigate('/admin/categories')}>
            <Icon name="arrow-left" size={15} />
            产品品类
          </button>
          <span className="eyebrow">{workspace.category.code} · 管理员工作区</span>
          <h1>{workspace.category.name}</h1>
          <div className="admin-title-badges">
            {workspace.category.knowledge_ready ? (
              <Badge tone="ok" dot>
                当前已发布
              </Badge>
            ) : (
              <Badge tone="warn">尚无发布</Badge>
            )}
            <span>生效快照 {shortHash(workspace.category.active_snapshot_id, 12)}</span>
            <span>生效发布 {shortHash(workspace.category.active_publication_id, 12)}</span>
          </div>
        </div>
        <button type="button" className="button button-ghost" onClick={() => void load(selectedSnapshotId)}>
          <Icon name="refresh" size={16} />
          刷新
        </button>
      </header>

      {error && (
        <Alert tone="danger" title="操作未完成">
          {error}
        </Alert>
      )}
      {notice && <Alert tone="success">{notice}</Alert>}

      <div className="admin-tabs" role="tablist" aria-label="管理员工作区">
        {TABS.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            className={tab === item.key ? 'tab-button tab-active' : 'tab-button'}
            onClick={() => setTab(item.key)}
          >
            {item.label}
            {item.key === 'review' && workspace.review.pending > 0 ? (
              <span className="tab-count">{workspace.review.pending}</span>
            ) : null}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <OverviewTab
          workspace={workspace}
          working={working}
          selectedSnapshotId={selectedSnapshotId}
          onSelectSnapshot={setSelectedSnapshotId}
          onUploadSource={uploadSourceFile}
          onBuild={buildIndex}
          onDerive={deriveDocuments}
          onInvestigate={() => setInvestigateOpen(true)}
          busy={busy}
          derivedEditable={derivedEditable}
          onOpenDocuments={() => setTab('documents')}
          sourceStage={sourceStage}
          sourceProgress={sourceProgress}
          sourceFileInfo={sourceFileInfo}
        />
      )}

      {tab === 'documents' && working && (
        <DocumentsTab
          api={api}
          categoryId={workspace.category.id}
          working={working}
          editable={derivedEditable}
          onChanged={() => void load(working.id)}
          onDerive={deriveDocuments}
          deriveBlocked={!workspace.capabilities.can_derive_documents}
          deriveBlocker={workspace.capabilities.derive_blocker}
        />
      )}

      {tab === 'review' && working && (
        <AdminReviewPanel
          api={api}
          categoryId={workspace.category.id}
          snapshotId={working.id}
          snapshotStatus={working.status}
          onChanged={() => void load(working.id)}
        />
      )}

      {tab === 'publish' && working && (
        <ReleaseTab
          api={api}
          workspace={workspace}
          working={working}
          onChanged={() => void load(working.id)}
        />
      )}

      {tab === 'audit' && <AuditTab api={api} categoryId={workspace.category.id} />}

      {investigateOpen && working && (
        <InvestigateModal
          api={api}
          categoryId={workspace.category.id}
          snapshot={working}
          onClose={() => setInvestigateOpen(false)}
          onStarted={(jobId, count) => {
            setInvestigateOpen(false)
            setNotice(`已提交能力调查任务 ${shortHash(jobId, 10)}，覆盖 ${count} 个模块；模型调用受服务端授权约束。`)
            void load(working.id)
          }}
        />
      )}
    </div>
  )
}

function OverviewTab({
  workspace,
  working,
  selectedSnapshotId,
  onSelectSnapshot,
  onUploadSource,
  onBuild,
  onDerive,
  onInvestigate,
  busy,
  derivedEditable,
  onOpenDocuments,
  sourceStage,
  sourceProgress,
  sourceFileInfo,
}: {
  workspace: Workspace
  working: AdminSnapshot | null
  selectedSnapshotId: string
  onSelectSnapshot: (id: string) => void
  onUploadSource: (file: File) => Promise<void>
  onBuild: () => Promise<void>
  onDerive: () => Promise<void>
  onInvestigate: () => void
  busy: string
  derivedEditable: boolean
  onOpenDocuments: () => void
  sourceStage: SourceStage
  sourceProgress: number
  sourceFileInfo: { name: string; size: number } | null
}) {
  const [sourceFile, setSourceFile] = useState<File | null>(null)
  const [showAllSnapshots, setShowAllSnapshots] = useState(false)
  const snapshots = showAllSnapshots ? workspace.snapshots : workspace.snapshots.slice(0, 5)
  const indexed = working?.index_status === 'indexed'
  const building = working?.index_status === 'queued' || working?.index_status === 'running'
  return (
    <div className="admin-tab-body">
      <section className="stat-strip admin-stat-strip">
        <div className="stat">
          <span>当前工作快照</span>
          <strong className="stat-text">{shortHash(working?.id, 12)}</strong>
        </div>
        <div className="stat">
          <span>快照状态</span>
          <strong className="stat-text">{working ? working.status : '—'}</strong>
        </div>
        <div className="stat">
          <span>索引状态</span>
          <strong className="stat-text">{working?.index_status || '—'}</strong>
        </div>
        <div className="stat">
          <span>文档</span>
          <strong>{workspace.documents.length}</strong>
        </div>
        <div className="stat">
          <span>待审核</span>
          <strong>{workspace.review.pending}</strong>
        </div>
        <div className="stat">
          <span>已接受</span>
          <strong>{workspace.review.accepted}</strong>
        </div>
      </section>

      <div className="admin-overview-grid">
        <div className="admin-side-stack">
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>当前工作快照</h2>
                <p>选择只读查看历史快照；所有构建与审核只对 extracted 暂存快照生效。</p>
              </div>
            </div>
            <label className="field">
              <span>快照</span>
              <select value={selectedSnapshotId} onChange={(event) => onSelectSnapshot(event.target.value)}>
                <option value="">自动选择最近暂存快照</option>
                {workspace.snapshots.map((snapshot) => (
                  <option key={snapshot.id} value={snapshot.id}>
                    {snapshot.id.slice(0, 12)} · {snapshot.status}/{snapshot.index_status}
                    {snapshot.derivation_type ? ` · ${snapshot.derivation_type}` : ''}
                  </option>
                ))}
              </select>
            </label>
            {working && (
              <dl className="admin-kv">
                <div>
                  <dt>状态</dt>
                  <dd>
                    <Badge tone={snapshotTone(working.status, working.index_status)}>
                      {working.status} / {working.index_status}
                    </Badge>
                  </dd>
                </div>
                <div>
                  <dt>派生来源</dt>
                  <dd>{working.derived_from_snapshot_id ? shortHash(working.derived_from_snapshot_id, 12) : '基础源码快照'}</dd>
                </div>
                <div>
                  <dt>来源 SHA-256</dt>
                  <dd className="mono-wrap">{working.source_sha256 || '—'}</dd>
                </div>
                <div>
                  <dt>数据分类</dt>
                  <dd>{working.data_classification || '未标记'}</dd>
                </div>
              </dl>
            )}
            <div className="admin-action-row">
              {working?.status === 'extracted' && !indexed && !building && (
                <button
                  type="button"
                  className="button button-primary"
                  disabled={Boolean(busy)}
                  onClick={() => void onBuild()}
                >
                  <Icon name="refresh" size={16} />
                  {busy === 'build' ? '提交中…' : '构建 / 重建索引'}
                </button>
              )}
              {indexed && (
                <button type="button" className="button button-secondary" onClick={onInvestigate}>
                  <Icon name="eye" size={16} />
                  启动能力调查
                </button>
              )}
              {building && <Badge tone="info">索引任务进行中，页面会自动刷新</Badge>}
              {working?.status === 'published' && <Badge tone="ok">已发布快照只读</Badge>}
            </div>
            {indexed && (
              <p className="admin-fineprint">
                发起调查会创建模型作业；是否允许调用模型由服务端配置决定，前端不能覆盖。
              </p>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>源码包</h2>
                <p>
                  上传受控源码 ZIP 只创建待解压快照，不会自动调用外部模型。单次上限 1 GiB，
                  解压总量上限 4 GiB，单文件上限 128 MiB。
                </p>
              </div>
            </div>
            <label className="field">
              <span>受控源码 ZIP</span>
              <input
                type="file"
                accept=".zip,application/zip"
                onChange={(event) => setSourceFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <button
              type="button"
              className="button button-secondary"
              disabled={!sourceFile || Boolean(busy)}
              onClick={() => {
                if (sourceFile) {
                  void onUploadSource(sourceFile).then(() => setSourceFile(null))
                }
              }}
            >
              <Icon name="upload" size={16} />
              {busy === 'source' ? '上传中…' : '上传为新快照'}
            </button>
            <SourceUploadPipeline
              stage={sourceStage}
              progress={sourceProgress}
              fileInfo={sourceFileInfo}
            />
            <p className="admin-fineprint">
              上传成功、解压成功与索引成功是不同状态；只有构建索引并人工审核后，发布才会影响售前自动评估。
            </p>
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>文档暂存</h2>
                <p>从当前已索引源码派生独立暂存快照，再上传或替换技术文档。</p>
              </div>
            </div>
            {!workspace.capabilities.can_derive_documents ? (
              <Alert tone="warn" title="不能派生文档快照">
                {workspace.capabilities.derive_blocker}
              </Alert>
            ) : (
              <button
                type="button"
                className="button button-secondary"
                disabled={Boolean(busy)}
                onClick={() => void onDerive()}
              >
                <Icon name="file" size={16} />
                {busy === 'derive' ? '派生中…' : '派生文档暂存快照'}
              </button>
            )}
            {derivedEditable && (
              <button type="button" className="button button-ghost" onClick={onOpenDocuments}>
                管理当前暂存文档
              </button>
            )}
            <p className="admin-fineprint">
              当前不支持“只有技术文档、没有源码”的建库；系统不会伪造源码快照。
            </p>
          </section>
        </div>

        <div className="admin-side-stack">
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>审核与发布状态</h2>
                <p>未审核候选不能发布；暂存快照与已发布知识严格分离。</p>
              </div>
            </div>
            <div className="admin-review-summary compact">
              <div>
                <span>候选</span>
                <strong>{workspace.review.total}</strong>
              </div>
              <div>
                <span>已接受</span>
                <strong>{workspace.review.accepted}</strong>
              </div>
              <div>
                <span>待审</span>
                <strong>{workspace.review.pending}</strong>
              </div>
              <div>
                <span>退回</span>
                <strong>{workspace.review.returned}</strong>
              </div>
            </div>
            {workspace.active_publication ? (
              <dl className="admin-kv">
                <div>
                  <dt>生效发布</dt>
                  <dd>{shortHash(workspace.active_publication.publication_id, 14)}</dd>
                </div>
                <div>
                  <dt>能力数量</dt>
                  <dd>{workspace.active_publication.capability_count}</dd>
                </div>
                <div>
                  <dt>发布时间</dt>
                  <dd>{formatDate(workspace.active_publication.published_at)}</dd>
                </div>
                <div>
                  <dt>发布人</dt>
                  <dd>{workspace.active_publication.published_by}</dd>
                </div>
              </dl>
            ) : (
              <p className="admin-fineprint">品类尚无生效发布。</p>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>快照历史</h2>
                <p>历史快照用于审计、任务固定与回滚，不作为可选产品版本。</p>
              </div>
              {workspace.snapshots.length > 5 && (
                <button type="button" className="link-button" onClick={() => setShowAllSnapshots((prev) => !prev)}>
                  {showAllSnapshots ? '收起' : '查看全部'}
                </button>
              )}
            </div>
            <div className="admin-snapshot-list">
              {snapshots.map((snapshot) => (
                <button
                  key={snapshot.id}
                  type="button"
                  className={snapshot.id === working?.id ? 'admin-snapshot-row active' : 'admin-snapshot-row'}
                  onClick={() => onSelectSnapshot(snapshot.id)}
                >
                  <span className="mono-wrap">{shortHash(snapshot.id, 14)}</span>
                  <span>
                    <Badge tone={snapshotTone(snapshot.status, snapshot.index_status)}>
                      {snapshot.status}/{snapshot.index_status}
                    </Badge>
                  </span>
                  <span>{snapshot.derivation_type || 'source'}</span>
                  <span>{formatDate(snapshot.created_at)}</span>
                </button>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>最近任务</h2>
                <p>只展示状态，不提供跳过队列或直接执行按钮。</p>
              </div>
            </div>
            <div className="admin-job-list">
              {workspace.jobs.slice(0, 8).map((job) => (
                <div key={job.id} className="admin-job-row">
                  <div>
                    <strong>{job.job_type}</strong>
                    <code>{shortHash(job.id, 10)}</code>
                  </div>
                  <Badge tone={statusTone(job.status)}>{statusLabel(job.status)}</Badge>
                  <span>{job.stage}</span>
                  <span>{formatDate(job.created_at)}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2>模型授权</h2>
                <p>服务端配置决定是否允许调查。</p>
              </div>
              <Badge tone="ok">前端不可覆盖</Badge>
            </div>
            <dl className="admin-kv compact">
              <div>
                <dt>构建模型</dt>
                <dd>{workspace.model_authorization.builder.model_configured ? '已配置' : '未配置'}</dd>
              </div>
              <div>
                <dt>状态</dt>
                <dd>{workspace.model_authorization.builder.status}</dd>
              </div>
              <div>
                <dt>文档发现</dt>
                <dd>{workspace.model_authorization.builder.document_discovery_mode}</dd>
              </div>
              <div>
                <dt>数据分类</dt>
                <dd>
                  {workspace.model_authorization.builder.allow_synthetic ? '合成公开' : '—'} /{' '}
                  {workspace.model_authorization.builder.allow_enterprise ? '企业' : '—'}
                </dd>
              </div>
            </dl>
          </section>
        </div>
      </div>
    </div>
  )
}

const SOURCE_STAGE_RANK: Record<SourceStage, number> = {
  idle: 0,
  uploading: 1,
  uploaded: 2,
  extracting: 3,
  extracted: 4,
  indexing: 5,
  indexed: 6,
  failed: -1,
}

function SourceUploadPipeline({
  stage,
  progress,
  fileInfo,
}: {
  stage: SourceStage
  progress: number
  fileInfo: { name: string; size: number } | null
}) {
  if (stage === 'idle') return null
  const rank = SOURCE_STAGE_RANK[stage]
  const failed = stage === 'failed'
  const steps = [
    { key: 'upload', label: '上传', current: rank === 1, done: rank > 1 },
    { key: 'uploaded', label: '上传成功', current: rank === 2, done: rank > 2 },
    {
      key: 'extract',
      label: rank === 3 ? '后台解压中…' : '解压成功',
      current: rank === 3,
      done: rank > 3,
    },
    {
      key: 'index',
      label: rank === 5 ? '索引中…' : '索引成功',
      current: rank === 5,
      done: rank === 6,
    },
  ]
  return (
    <div className="upload-pipeline" role="status" aria-live="polite">
      {fileInfo && (
        <div className="upload-pipeline-file">
          <span className="mono-wrap">{fileInfo.name}</span>
          <span>{formatBytes(fileInfo.size)}</span>
          {failed ? <Badge tone="danger">失败</Badge> : null}
        </div>
      )}
      {rank === 1 && (
        <div className="progress" aria-label="上传进度">
          <div className="progress-value" style={{ width: `${progress}%` }} />
        </div>
      )}
      <ol className="upload-steps">
        {steps.map((step, index) => (
          <li
            key={step.key}
            className={
              step.done
                ? 'upload-step done'
                : step.current
                  ? 'upload-step current'
                  : 'upload-step'
            }
          >
            <span className="upload-step-index">{index + 1}</span>
            {step.label}
            {step.key === 'upload' && rank === 1 ? ` ${progress}%` : ''}
          </li>
        ))}
      </ol>
      {failed && (
        <p className="admin-fineprint">
          上传或解压失败；请根据上方错误说明修正后重新打包，已拒绝的文件不会进入索引。
        </p>
      )}
    </div>
  )
}

function snapshotTone(status: string, index: string): string {
  if (status === 'published') return 'ok'
  if (index === 'failed' || status === 'failed') return 'danger'
  if (index === 'indexed') return 'info'
  if (index === 'queued' || index === 'running') return 'warn'
  return 'muted'
}

function DocumentsTab({
  api,
  categoryId,
  working,
  editable,
  onChanged,
  onDerive,
  deriveBlocked,
  deriveBlocker,
}: {
  api: ApiClient
  categoryId: string
  working: AdminSnapshot
  editable: boolean
  onChanged: () => void
  onDerive: () => void
  deriveBlocked: boolean
  deriveBlocker: string | null
}) {
  const [documents, setDocuments] = useState<AdminDocument[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const workspace = await api.adminWorkspace(categoryId, working.id)
      setDocuments(workspace.documents)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '文档列表加载失败')
    } finally {
      setLoading(false)
    }
  }, [api, categoryId, working.id])

  useEffect(() => {
    void load()
  }, [load])

  function validate(fileToCheck: File): boolean {
    const suffix = fileToCheck.name.toLowerCase().split('.').pop()
    if (!suffix || !['docx', 'pdf', 'xlsx'].includes(suffix)) {
      setError('管理员技术文档只接受 DOCX、PDF 或 XLSX；不支持其他扩展名。')
      return false
    }
    return true
  }

  async function upload() {
    if (!file || !validate(file)) return
    setBusy('upload')
    setError('')
    try {
      await api.uploadAdminDocument(categoryId, working.id, file)
      setNotice('文档已上传并进入解析队列；解析结果会在此列表更新。')
      setFile(null)
      await load()
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '文档上传失败')
    } finally {
      setBusy('')
    }
  }

  async function replace(document: AdminDocument, replacement: File) {
    if (!validate(replacement)) return
    setBusy(document.id)
    setError('')
    try {
      await api.replaceAdminDocument(working.id, document.id, replacement)
      setNotice('文档已替换并重新进入解析队列；原分类与来源记录保留在审计中。')
      await load()
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '替换文档失败')
    } finally {
      setBusy('')
    }
  }

  async function remove(document: AdminDocument) {
    if (!window.confirm(`确认删除暂存文档“${document.filename}”？已发布的同名文档与历史证据不会被修改。`)) {
      return
    }
    setBusy(document.id)
    setError('')
    try {
      await api.deleteAdminDocument(working.id, document.id)
      setNotice('文档已从当前暂存快照移除。')
      await load()
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '删除文档失败')
    } finally {
      setBusy('')
    }
  }

  async function reparse(document: AdminDocument) {
    setBusy(document.id)
    setError('')
    try {
      await api.reparseAdminDocument(working.id, document.id)
      setNotice('已创建文档重解析任务；已引用证据的文档会被服务端拒绝。')
      await load()
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '重解析失败')
    } finally {
      setBusy('')
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>技术文档</h2>
          <p>
            快照 {shortHash(working.id, 12)} · {working.status}/{working.index_status} ·{' '}
            {working.derivation_type || '基础源码快照'}
          </p>
        </div>
        <div className="admin-title-badges">
          <button
            type="button"
            className="button button-ghost button-small"
            disabled={loading}
            onClick={() => void load()}
          >
            <Icon name="refresh" size={14} />
            刷新文档
          </button>
          <Badge tone={editable ? 'ok' : 'muted'}>{editable ? '可编辑暂存' : '只读'}</Badge>
        </div>
      </div>

      {error && <Alert tone="danger">{error}</Alert>}
      {notice && <Alert tone="success">{notice}</Alert>}

      {!editable && (
        <Alert tone="warn" title="当前快照不可直接改文档">
          {working.status === 'published'
            ? '已发布快照不可原位覆盖。请从当前生效源码派生新的文档暂存快照后再修改，发布新版本不会改写历史任务引用。'
            : '文档修改只允许发生在 extracted 状态的 document_update 派生暂存快照中。'}
          {deriveBlocked ? (
            <p>{deriveBlocker}</p>
          ) : (
            <button type="button" className="button button-secondary button-small" onClick={onDerive}>
              派生文档暂存快照
            </button>
          )}
        </Alert>
      )}

      {editable && (
        <div className="admin-upload-box">
          <label className="field">
            <span>上传技术文档</span>
            <input
              type="file"
              accept=".docx,.pdf,.xlsx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <small>仅支持 DOCX / PDF / XLSX；服务端会复核扩展名、MIME 与文件签名。</small>
          </label>
          <button
            type="button"
            className="button button-primary"
            disabled={!file || Boolean(busy)}
            onClick={() => void upload()}
          >
            <Icon name="upload" size={16} />
            {busy === 'upload' ? '上传中…' : '上传到暂存快照'}
          </button>
        </div>
      )}

      {loading ? (
        <div className="loading-panel">
          <Spinner label="正在加载文档…" />
        </div>
      ) : documents.length === 0 ? (
        <Alert tone="info" title="暂无技术文档">
          可在派生暂存快照中上传 DOCX、PDF 或 XLSX。文档解析失败会保留失败状态与错误码，不会被静默忽略。
        </Alert>
      ) : (
        <div className="admin-document-list">
          {documents.map((document) => (
            <article key={document.id} className="admin-document-card">
              <div className="admin-document-head">
                <div>
                  <strong>{document.filename}</strong>
                  <span className="admin-meta-line">
                    {formatBytes(document.size)} · {shortHash(document.sha256, 16)} ·{' '}
                    {document.parser_version || '解析器待运行'}
                  </span>
                </div>
                <Badge tone={parseTone(document.parse_status)}>{document.parse_status}</Badge>
              </div>
              <div className="admin-meta-line">
                <span>分段 {document.segment_count}</span>
                <span>错误 {document.error_code || '—'}</span>
                <span>来源 {String(document.origin?.derived_from_snapshot_id || document.origin?.source || '—')}</span>
              </div>
              {document.error_code && (
                <Alert tone="danger" title="解析失败">
                  {document.error_code}；可检查文件后替换或重解析，已发布知识不会因此改变。
                </Alert>
              )}
              {editable && (
                <div className="admin-document-actions">
                  <label className="button button-ghost button-small file-button">
                    替换文件
                    <input
                      type="file"
                      accept={acceptFor(document.filename)}
                      disabled={Boolean(busy)}
                      onChange={(event) => {
                        const replacement = event.target.files?.[0]
                        event.target.value = ''
                        if (replacement) void replace(document, replacement)
                      }}
                    />
                  </label>
                  <button
                    type="button"
                    className="button button-ghost button-small"
                    disabled={Boolean(busy)}
                    onClick={() => void reparse(document)}
                  >
                    <Icon name="refresh" size={14} />
                    重解析
                  </button>
                  <button
                    type="button"
                    className="button button-danger button-small"
                    disabled={Boolean(busy)}
                    onClick={() => void remove(document)}
                  >
                    删除
                  </button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
      <p className="admin-fineprint">
        已发布快照不可原位覆盖；已引用为审核证据的文档禁止替换、删除或重解析。历史任务固定其发布版本与证据，不受新文档影响。
      </p>
    </section>
  )
}

function acceptFor(filename: string): string {
  if (filename.toLowerCase().endsWith('.pdf')) return '.pdf,application/pdf'
  if (filename.toLowerCase().endsWith('.xlsx'))
    return '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  return '.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document'
}

function parseTone(status: string): string {
  if (status === 'parsed') return 'ok'
  if (status === 'partial' || status === 'ocr_required') return 'warn'
  if (status === 'failed' || status === 'rejected') return 'danger'
  if (status === 'pending' || status === 'queued' || status === 'running') return 'info'
  return 'muted'
}

function ReleaseTab({
  api,
  workspace,
  working,
  onChanged,
}: {
  api: ApiClient
  workspace: Workspace
  working: AdminSnapshot
  onChanged: () => void
}) {
  const [check, setCheck] = useState<AdminReleaseCheck | null>(null)
  const [publications, setPublications] = useState<
    Array<{
      publication_id: string
      previous_publication_id: string | null
      action: string
      actor: string
      comment: string
    }>
  >([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [acknowledge, setAcknowledge] = useState(false)
  const [comment, setComment] = useState('')
  const [rollbackTarget, setRollbackTarget] = useState('')
  const [rollbackComment, setRollbackComment] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [release, history] = await Promise.all([
        api.adminReleaseCheck(working.id),
        api.publications(workspace.category.id),
      ])
      setCheck(release)
      setPublications(history)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '发布检查加载失败')
    } finally {
      setLoading(false)
    }
  }, [api, working.id, workspace.category.id])

  useEffect(() => {
    void load()
  }, [load])

  async function publish() {
    if (!check) return
    if (!comment.trim()) {
      setError('发布必须填写说明')
      return
    }
    if (!acknowledge) {
      setError('必须显式确认覆盖缺口')
      return
    }
    if (!window.confirm('确认按服务端发布检查结果发布当前暂存知识？发布后售前评估将固定引用新清单。')) {
      return
    }
    setBusy('publish')
    setError('')
    setNotice('')
    try {
      const result = await api.publishSnapshot(working.id, {
        revision_ids: check.accepted_revision_ids,
        expected_active_publication_id: check.expected_active_publication_id,
        acknowledge_coverage_gaps: true,
        comment: comment.trim(),
      })
      setNotice(`已发布 ${result.publication_id}，清单哈希 ${shortHash(result.payload_hash, 16)}。`)
      setComment('')
      setAcknowledge(false)
      await load()
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '发布失败')
    } finally {
      setBusy('')
    }
  }

  async function rollback() {
    if (!rollbackTarget) {
      setError('请选择要回滚到的历史发布')
      return
    }
    if (!rollbackComment.trim()) {
      setError('回滚必须填写原因')
      return
    }
    if (!window.confirm('确认回滚到所选历史发布？历史任务仍固定原发布版本，不会被改写。')) {
      return
    }
    setBusy('rollback')
    setError('')
    try {
      const result = await api.rollbackPublication(workspace.category.id, {
        publication_id: rollbackTarget,
        expected_active_publication_id: workspace.category.active_publication_id,
        comment: rollbackComment.trim(),
      })
      setNotice(`已切换生效发布为 ${result.publication_id}（快照 ${shortHash(result.snapshot_id, 12)}）。`)
      setRollbackTarget('')
      setRollbackComment('')
      await load()
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '回滚失败')
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="admin-tab-body">
      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>发布前检查</h2>
            <p>
              快照 {shortHash(working.id, 12)} · 只使用服务端重新校验过的已接受修订；未审核候选不会进入清单。
            </p>
          </div>
          <button type="button" className="button button-ghost button-small" onClick={() => void load()}>
            <Icon name="refresh" size={15} />
            重新检查
          </button>
        </div>
        {error && <Alert tone="danger">{error}</Alert>}
        {notice && <Alert tone="success">{notice}</Alert>}
        {loading ? (
          <div className="loading-panel">
            <Spinner label="正在校验发布清单…" />
          </div>
        ) : check ? (
          <>
            <div className="admin-release-banner">
              <div>
                <span>发布结论</span>
                <strong className={check.publishable ? 'text-ok' : 'text-danger'}>
                  {check.publishable ? '可以发布' : '存在阻断项，不能发布'}
                </strong>
              </div>
              <div>
                <span>已接受修订</span>
                <strong>{check.accepted_revision_ids.length}</strong>
              </div>
              <div>
                <span>阻断项</span>
                <strong>{check.blocking.length}</strong>
              </div>
              <div>
                <span>生效发布</span>
                <strong className="stat-text">{shortHash(check.expected_active_publication_id, 12)}</strong>
              </div>
            </div>

            {check.blocking.length > 0 && (
              <Alert tone="danger" title="发布阻断">
                <div className="admin-chip-row">
                  {check.blocking.map((item, index) => (
                    <code key={index} className="admin-error-chip">
                      {item.name || item.claim_id || item.revision_id || '全局'}：{item.errors.join('、')}
                    </code>
                  ))}
                </div>
              </Alert>
            )}

            {check.items.length === 0 ? (
              <Alert tone="warn" title="没有已审核修订">
                需先完成人工接受；退回、拒绝、修改或未审核候选均不能发布。
              </Alert>
            ) : (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>能力</th>
                      <th>决定</th>
                      <th>声明</th>
                      <th>门槛错误</th>
                      <th>发布阻断</th>
                      <th>已在清单</th>
                    </tr>
                  </thead>
                  <tbody>
                    {check.items.map((item) => (
                      <tr key={item.revision_id}>
                        <td>{item.name || shortHash(item.claim_id, 10)}</td>
                        <td>{item.decision}</td>
                        <td>{item.declaration_type}</td>
                        <td>{item.gate_errors.length ? item.gate_errors.join('、') : '—'}</td>
                        <td>{item.blockers.length ? item.blockers.join('、') : '—'}</td>
                        <td>{item.published ? '是' : '否'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="admin-publish-box">
              <label className="admin-check">
                <input
                  type="checkbox"
                  checked={acknowledge}
                  onChange={(event) => setAcknowledge(event.target.checked)}
                />
                <span>我已核对覆盖缺口与发布范围，并确认本次发布只包含已审核能力。</span>
              </label>
              <label className="field">
                <span>发布说明（必填）</span>
                <textarea
                  rows={3}
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                  placeholder="记录本次发布范围、审核结论与已知限制。"
                />
              </label>
              <button
                type="button"
                className="button button-primary"
                disabled={!check.publishable || busy === 'publish'}
                onClick={() => void publish()}
              >
                {busy === 'publish' ? '发布中…' : '发布当前审核清单'}
              </button>
              {!check.publishable && (
                <p className="admin-fineprint">服务端返回不可发布时，前端不会提供绕过按钮。</p>
              )}
            </div>

            <details className="admin-details">
              <summary>覆盖信息</summary>
              <pre>{JSON.stringify(check.coverage, null, 2)}</pre>
            </details>
          </>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-head">
          <div>
            <h2>发布与回滚记录</h2>
            <p>回滚只切换生效指针；历史任务仍固定其创建时的发布版本。</p>
          </div>
        </div>
        {publications.length === 0 ? (
          <Alert tone="info">尚无发布记录。</Alert>
        ) : (
          <div className="admin-publication-list">
            {publications
              .slice()
              .reverse()
              .map((publication) => (
                <div key={publication.publication_id} className="admin-publication-row">
                  <div>
                    <strong>{shortHash(publication.publication_id, 16)}</strong>
                    <span className="admin-meta-line">
                      {publication.action} · {publication.actor} · {publication.comment || '无说明'}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="button button-ghost button-small"
                    disabled={publication.publication_id === workspace.category.active_publication_id}
                    onClick={() => setRollbackTarget(publication.publication_id)}
                  >
                    选为回滚目标
                  </button>
                </div>
              ))}
          </div>
        )}
        {rollbackTarget && (
          <div className="admin-publish-box">
            <Alert tone="warn" title="回滚操作">
              目标发布 {shortHash(rollbackTarget, 16)}。回滚会生成新的发布切换审计记录，不修改历史任务。
            </Alert>
            <label className="field">
              <span>回滚原因（必填）</span>
              <textarea
                rows={2}
                value={rollbackComment}
                onChange={(event) => setRollbackComment(event.target.value)}
              />
            </label>
            <div className="modal-actions">
              <button type="button" className="button button-ghost" onClick={() => setRollbackTarget('')}>
                取消
              </button>
              <button
                type="button"
                className="button button-danger"
                disabled={busy === 'rollback'}
                onClick={() => void rollback()}
              >
                {busy === 'rollback' ? '回滚中…' : '确认回滚'}
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}

function AuditTab({ api, categoryId }: { api: ApiClient; categoryId: string }) {
  const [items, setItems] = useState<Array<Record<string, unknown>>>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const result = await api.adminAudit(categoryId)
      setItems(result.items)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '审计日志加载失败')
    } finally {
      setLoading(false)
    }
  }, [api, categoryId])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <section className="panel">
      <div className="panel-head">
        <div>
          <h2>审计日志</h2>
          <p>资料上传、文档变更、调查、审核与发布均由服务端记录操作者与关联资源。</p>
        </div>
        <button type="button" className="button button-ghost button-small" onClick={() => void load()}>
          <Icon name="refresh" size={15} />
          刷新
        </button>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {loading ? (
        <div className="loading-panel">
          <Spinner label="正在加载审计事件…" />
        </div>
      ) : items.length === 0 ? (
        <Alert tone="info">当前品类尚无审计事件。</Alert>
      ) : (
        <div className="admin-audit-list">
          {items.map((event) => (
            <div key={String(event.id)} className="admin-audit-row">
              <div className="admin-audit-head">
                <Badge tone="muted">{String(event.action)}</Badge>
                <strong>{String(event.principal_id)}</strong>
                <span>{formatDate(String(event.created_at ?? ''))}</span>
              </div>
              <div className="admin-meta-line">
                <span>资源 {shortHash(String(event.resource_id ?? ''), 16)}</span>
                <span>关联 {shortHash(String(event.correlation_id ?? ''), 12)}</span>
              </div>
              <pre>{JSON.stringify(event.details ?? {}, null, 2)}</pre>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

function InvestigateModal({
  api,
  categoryId,
  snapshot,
  onClose,
  onStarted,
}: {
  api: ApiClient
  categoryId: string
  snapshot: AdminSnapshot
  onClose: () => void
  onStarted: (jobId: string, count: number) => void
}) {
  const [modules, setModules] = useState<RetrievalItem[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [classification, setClassification] = useState<'synthetic_public' | 'enterprise'>(
    snapshot.data_classification === 'synthetic_public' ? 'synthetic_public' : 'enterprise',
  )
  const [mode, setMode] = useState<'bounded' | 'explore' | 'autonomous'>('bounded')
  const [capabilityLimit, setCapabilityLimit] = useState(1)
  const [includeEquivalence, setIncludeEquivalence] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api
      .retrieval(categoryId, snapshot.id, { tool: 'get_project_map', query: '', limit: 100 })
      .then((envelope) => {
        if (!active) return
        setModules(envelope.results)
        setSelected(
          envelope.results
            .map((item) => item.id)
            .filter((value): value is string => Boolean(value))
            .slice(0, 24),
        )
      })
      .catch((err) => {
        if (active) setError(err instanceof ApiError ? err.message : '模块列表加载失败')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [api, categoryId, snapshot.id])

  async function submit() {
    if (selected.length === 0) {
      setError('至少选择一个调查模块')
      return
    }
    setBusy(true)
    setError('')
    try {
      const result = await api.investigate(snapshot.id, {
        module_ids: selected.slice(0, 24),
        data_classification: classification,
        investigation_mode: mode,
        capability_limit: capabilityLimit,
        include_equivalence: includeEquivalence,
      })
      onStarted(result.job_id, result.module_count)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '调查任务提交失败')
      setBusy(false)
    }
  }

  return (
    <Modal
      title="启动能力调查"
      subtitle="调查任务由后台 worker 执行；模型是否可用、允许的数据分类均由服务端配置决定。"
      onClose={onClose}
      wide
    >
      <div className="form-stack">
        {error && <Alert tone="danger">{error}</Alert>}
        <Alert tone="warn" title="模型调用边界">
          本操作会创建可能调用外部模型的知识构建任务。页面上的数据分类只作为请求参数，
          服务端会以受信分类与全局授权复核；前端没有任何绕过开关。
        </Alert>
        {loading ? (
          <Spinner label="正在读取模块…" />
        ) : modules.length === 0 ? (
          <Alert tone="warn">当前快照没有可调查模块；请确认源码索引已完成。</Alert>
        ) : (
          <div className="admin-module-grid">
            {modules.map((module) => {
              const moduleId = module.id || ''
              return (
                <label key={moduleId} className="admin-check">
                  <input
                    type="checkbox"
                    checked={selected.includes(moduleId)}
                    onChange={(event) =>
                      setSelected((prev) =>
                        event.target.checked
                          ? [...prev, moduleId]
                          : prev.filter((item) => item !== moduleId),
                      )
                    }
                  />
                  <span>
                    <strong>{module.name || moduleId}</strong>
                    {module.snippet && <small>{module.snippet.slice(0, 120)}</small>}
                  </span>
                </label>
              )
            })}
          </div>
        )}
        <div className="admin-form-grid">
          <label className="field">
            <span>数据分类</span>
            <select
              value={classification}
              onChange={(event) => setClassification(event.target.value as typeof classification)}
            >
              <option value="synthetic_public">合成公开资料</option>
              <option value="enterprise">企业资料</option>
            </select>
          </label>
          <label className="field">
            <span>调查模式</span>
            <select value={mode} onChange={(event) => setMode(event.target.value as typeof mode)}>
              <option value="bounded">有界</option>
              <option value="explore">探索</option>
              <option value="autonomous">自主</option>
            </select>
          </label>
          <label className="field">
            <span>单模块能力上限</span>
            <select
              value={capabilityLimit}
              onChange={(event) => setCapabilityLimit(Number(event.target.value))}
            >
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
            </select>
          </label>
          <label className="admin-check admin-check-inline">
            <input
              type="checkbox"
              checked={includeEquivalence}
              onChange={(event) => setIncludeEquivalence(event.target.checked)}
            />
            <span>包含可能等价关系假设</span>
          </label>
        </div>
        <p className="admin-fineprint">已选择 {selected.length} / 24 个模块。</p>
        <div className="modal-actions">
          <button type="button" className="button button-ghost" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className="button button-primary"
            disabled={busy || selected.length === 0 || loading}
            onClick={() => void submit()}
          >
            {busy ? '提交中…' : '提交调查任务'}
          </button>
        </div>
      </div>
    </Modal>
  )
}
