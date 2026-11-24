import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode, FormEvent } from 'react'
import { ApiError } from '../api'
import type { ApiClient } from '../api'
import type { AdminDocument, AdminSnapshot, DerivationChain, MaterialProvenance } from '../types'
import { Alert, Badge, Modal } from '../components/ui'
import { formatDate } from '../format'

export function provenanceError(error: unknown): string {
  if (!(error instanceof ApiError)) return '网络异常，操作结果可能尚未确认。请刷新来源记录核对后再决定是否重试。'
  const descriptions: Record<number, string> = {
    403: '无管理员权限，不能确认或撤销资料来源。',
    404: '资料不存在或不属于当前品类、快照，请刷新后检查关联。',
    409: '记录状态冲突，可能已确认或已撤销，请刷新后核对。',
    422: '资料或字段未满足确认条件，请检查解析、分段、快照状态及填写长度。',
  }
  return `${descriptions[error.status] || '请求未完成。'} ${error.message}（${error.code}）`
}

type ContextValue = {
  api: ApiClient; categoryId: string; categoryName: string; snapshot: AdminSnapshot | null
  records: MaterialProvenance[]; chain: DerivationChain | null; loading: boolean
  error: string; refresh: () => Promise<void>
  reportFailure: (message: string) => void
}
const Context = createContext<ContextValue | null>(null)
function useProvenance() {
  const value = useContext(Context)
  if (!value) throw new Error('资料来源上下文缺失')
  return value
}

export function ProvenanceProvider({ api, categoryId, categoryName, snapshot, children }: {
  api: ApiClient; categoryId: string; categoryName: string; snapshot: AdminSnapshot | null; children: ReactNode
}) {
  const [records, setRecords] = useState<MaterialProvenance[]>([])
  const [chain, setChain] = useState<DerivationChain | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const generation = useRef(0)
  const sid = snapshot?.id
  const refresh = useCallback(async () => {
    const version = ++generation.current
    setLoading(true)
    setError('')
    setChain(null)
    setRecords([])
    try {
      const [result, lineage] = await Promise.all([
        api.materialProvenance(categoryId), sid ? api.derivationChain(sid) : Promise.resolve(null),
      ])
      if (version !== generation.current) return
      if (result.items.some(r => r.category_id !== categoryId) ||
        (lineage && (lineage.category_id !== categoryId || lineage.snapshot_id !== sid ||
          lineage.chain.some(s => s.category_id !== categoryId)))) {
        throw new ApiError(409, 'PROVENANCE_SCOPE_MISMATCH', '返回的资料归属与当前工作区不匹配')
      }
      setRecords(result.items)
      setChain(lineage)
    } catch (err) {
      if (version === generation.current) setError(provenanceError(err))
    } finally {
      if (version === generation.current) setLoading(false)
    }
  }, [api, categoryId, sid])
  useEffect(() => { void refresh(); return () => { generation.current++ } }, [refresh])
  return <Context.Provider value={{ api, categoryId, categoryName, snapshot, records, chain, loading, error, refresh, reportFailure: setActionError }}>
    {actionError && <Alert tone="danger" title="来源操作未完成">{actionError}</Alert>}
    {children}
  </Context.Provider>
}

export function FullHash({ value }: { value: string | null }) {
  const [message, setMessage] = useState('')
  return <div className="mono-wrap">
    <span>SHA-256：{value || '缺失'}</span>{' '}
    <button type="button" className="button button-ghost button-small" disabled={!value} onClick={async () => {
      try {
        await navigator.clipboard.writeText(value!)
        setMessage('已复制完整 SHA-256')
      } catch { setMessage('无法访问剪贴板，请手动选择并复制完整 SHA-256。') }
    }}>复制完整哈希</button>
    {message && <span role="status">{message}</span>}
  </div>
}

function verifiedRoot(chain: DerivationChain | null) {
  if (!chain?.valid || chain.error_code) return null
  const root = chain.chain[0]
  if (!root || root.id !== chain.root_snapshot_id || root.derived_from_snapshot_id ||
      root.derivation_type || chain.chain.at(-1)?.id !== chain.snapshot_id) return null
  return root
}

export function ProvenanceOverview() {
  const { snapshot, chain, error, loading, refresh } = useProvenance()
  const root = verifiedRoot(chain)
  return <section className="panel">
    <div className="panel-head"><h2>资料来源与派生链</h2>
      <button className="button button-ghost" disabled={loading} onClick={() => void refresh()}>刷新来源状态</button>
    </div>
    {loading ? <p>正在核验来源与派生链…</p> : error ? <Alert tone="danger">{error}</Alert> :
      !snapshot ? <p>尚无工作快照。</p> : !root ?
        <Alert tone="danger">派生链未通过核验：{chain?.error_code || 'DERIVATION_CHAIN_UNVERIFIED'}。不能依据哈希相同认定合法。</Alert> :
        <>
          <Badge tone="ok">派生链已通过服务端核验</Badge>
          <ol>{chain!.chain.map(s => <li className="mono-wrap" key={s.id}>
            {s.id} · {s.derivation_type || '源码根快照'} · {s.status}/{s.index_status}
            {s.derived_from_snapshot_id && <span> · 父快照 {s.derived_from_snapshot_id}</span>}
          </li>)}</ol>
          <h3>源码根快照信息</h3>
          <p className="mono-wrap">快照 ID：{root.id} · 状态：{root.status}/{root.index_status}</p>
          <MaterialConfirmation source={root} />
        </>}
    <p>来源确认不等于模型外发授权，不会触发调查、发布或源码读取。</p>
  </section>
}

type SnapshotInfo = Omit<AdminSnapshot, 'created_at'>
export function MaterialConfirmation({ source, document }: { source?: SnapshotInfo; document?: AdminDocument }) {
  const context = useProvenance()
  const { api, categoryId, categoryName, snapshot, records, chain, loading, error, refresh, reportFailure } = context
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState('')
  const [fields, setFields] = useState({ declared_origin: '', usage_scope: '', confirmation_reference: '' })
  const target = document ? snapshot : source
  const materialType = document ? 'technical_document' : 'source_code'
  const hash = document?.sha256 || source?.source_sha256 || null
  const matches = records.filter(r => r.material_type === materialType && r.snapshot_id === target?.id &&
    (document ? r.document_id === document.id : r.document_id === null))
  const active = matches.find(r => r.status === 'confirmed')
  let reason = ''
  if (loading) reason = '正在读取服务端来源状态。'
  else if (error) reason = error
  else if (!verifiedRoot(chain)) reason = `派生链未通过核验：${chain?.error_code || 'DERIVATION_CHAIN_UNVERIFIED'}`
  else if (!target || target.category_id !== categoryId ||
    (document && (document.category_id !== categoryId || document.snapshot_id !== target.id)) ||
    (!document && target.id !== verifiedRoot(chain)?.id)) reason = '资料与当前品类或快照不匹配。'
  else if (!['extracted', 'published'].includes(target.status) || target.index_status !== 'indexed') reason = '快照尚未就绪：需要 extracted/published 且 indexed。'
  else if (target.data_classification !== 'enterprise' || (document && document.data_classification !== 'enterprise')) reason = '仅正式企业资料允许确认，合成或未标记资料不可确认。'
  else if (document && document.parse_status !== 'parsed') reason = `文档尚未成功解析：${document.parse_status}${document.error_code ? '（' + document.error_code + '）' : ''}。`
  else if (document && document.segment_count <= 0) reason = '文档没有可核验的有效分段。'
  else if (!hash) reason = '资料 SHA-256 缺失。'
  else if (active) reason = active.content_sha256 === hash ? '该资料已有生效确认记录。' : '生效记录哈希与资料不匹配，请核对来源记录。'
  const rules = [
    ['declared_origin', '来源说明', 3, 500],
    ['usage_scope', '适用范围', 8, 1000],
    ['confirmation_reference', '确认依据', 3, 200],
  ] as const
  function fieldError() {
    for (const [key, label, min, max] of rules) {
      const length = Array.from(fields[key].trim()).length
      if (length < min || length > max) return `${label}去除首尾空白后须为 ${min}–${max} 个字符。`
    }
    return ''
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    const invalid = reason || fieldError()
    if (invalid || !target) { setFailure(invalid); return }
    setBusy(true)
    setFailure('')
    reportFailure('')
    try {
      await api.confirmMaterial(categoryId, {
        snapshot_id: target.id, material_type: materialType,
        ...(document ? { document_id: document.id } : {}),
        declared_origin: fields.declared_origin.trim(), usage_scope: fields.usage_scope.trim(),
        confirmation_reference: fields.confirmation_reference.trim(),
      })
      setOpen(false)
      setFields({ declared_origin: '', usage_scope: '', confirmation_reference: '' })
      await refresh()
    } catch (err) { reportFailure(provenanceError(err)); await refresh() }
    finally { setBusy(false) }
  }
  return <div>
    <p>产品品类：{categoryName} · 资料类型：{materialType}</p>
    <FullHash value={hash} />
    <p>来源确认状态：{loading ? '读取中' : error ? '读取失败' : active ? (active.content_sha256 === hash ? '已确认' : '哈希不匹配') : matches.length ? '已撤销' : '未确认'}</p>
    {reason && <p>{reason}</p>}
    <button type="button" className="button button-secondary button-small" disabled={Boolean(reason) || busy}
      onClick={() => { setFailure(''); setOpen(true) }}>{document ? '确认来源' : '确认源码来源'}</button>
    {open && <Modal title={document ? '确认技术文档来源' : '确认源码来源'} onClose={() => { if (!busy) setOpen(false) }}>
      <p>{categoryName} · {materialType} · {document?.filename || '源码根快照'}</p>
      <p className="mono-wrap">快照 ID：{target?.id}</p>
      {document && <p className="mono-wrap">文档 ID：{document.id} · 解析：{document.parse_status} · 分段：{document.segment_count}</p>}
      <p>快照状态：{target?.status}/{target?.index_status}</p>
      <FullHash value={hash} />
      <p>请填写真实来源及依据。此操作不授予模型外发权限。</p>
      {reason && <Alert tone="warn">{reason}</Alert>}
      {failure && <Alert tone="danger">{failure}</Alert>}
      <form onSubmit={event => void submit(event)}>
        {rules.map(([key, label, min, max]) => <label className="field" key={key}>
          <span>{label}（{min}–{max} 字符）</span>
          <textarea aria-label={label} required value={fields[key]} disabled={busy}
            onChange={event => setFields({ ...fields, [key]: event.target.value })} />
        </label>)}
        <button className="button button-primary" type="submit" disabled={busy || Boolean(reason)}>{busy ? '提交中…' : '提交来源确认'}</button>
      </form>
    </Modal>}
  </div>
}

export function ProvenanceRecords() {
  const { api, categoryName, records, loading, error, refresh } = useProvenance()
  const [busy, setBusy] = useState('')
  const [failure, setFailure] = useState('')
  async function revoke(record: MaterialProvenance) {
    if (busy || !window.confirm(`确认撤销这条资料来源确认？\n资料类型：${record.material_type}\n快照：${record.snapshot_id}\n记录：${record.id}\n历史记录会保留，相关资料可能不再满足后续核验条件。`)) return
    setBusy(record.id)
    setFailure('')
    try { await api.revokeMaterial(record.id); await refresh() }
    catch (err) { setFailure(provenanceError(err)); await refresh() }
    finally { setBusy('') }
  }
  return <section className="panel">
    <div className="panel-head"><h2>资料来源确认记录</h2>
      <button className="button button-ghost" disabled={loading || Boolean(busy)} onClick={() => void refresh()}>刷新确认记录</button>
    </div>
    <p>{categoryName} · 当前品类全部记录（跨工作快照）</p>
    {error && <Alert tone="danger">{error}</Alert>}
    {failure && <Alert tone="danger">{failure}</Alert>}
    {loading ? <p>正在读取确认记录…</p> : !error && !records.length ? <p>暂无来源确认记录。</p> :
      records.map(record => <article className="admin-document-card" key={record.id}>
        <strong>{record.material_type} · {record.status === 'confirmed' ? '生效' : record.status === 'revoked' ? '已撤销' : record.status}</strong>
        <p>来源说明：{record.declared_origin}</p><p>适用范围：{record.usage_scope}</p>
        <p>确认依据：{record.confirmation_reference}</p>
        <p>确认人：{record.confirmed_by} · 确认时间：{formatDate(record.created_at)}</p>
        <p>撤销人：{record.revoked_by || '—'} · 撤销时间：{formatDate(record.revoked_at)}</p>
        <details><summary>查看完整记录</summary>
          <dl>{Object.entries(record).map(([key, value]) => <div className="mono-wrap" key={key}><dt>{key}</dt><dd>{value ?? '—'}</dd></div>)}</dl>
        </details>
        {record.status === 'confirmed' && <button className="button button-danger button-small"
          disabled={loading || Boolean(busy)} onClick={() => void revoke(record)}>撤销确认</button>}
      </article>)}
  </section>
}
