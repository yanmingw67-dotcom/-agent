import { useEffect, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { Alert, Badge, Icon, Spinner } from '../components/ui'
import { formatDate, shortHash, statusLabel, statusTone } from '../format'
import { navigate, useDocumentTitle } from '../hooks'
import type { Category, ExtractionJob } from '../types'

const ACCEPT = '.docx,.pdf,.xlsx'
const MAX_DISPLAY_MB = 20

export function NewTask({ api }: { api: ApiClient }) {
  useDocumentTitle('新建评估')
  const [categories, setCategories] = useState<Category[]>([])
  const [categoryId, setCategoryId] = useState('')
  const [mode, setMode] = useState<'upload' | 'text'>('upload')
  const [file, setFile] = useState<File | null>(null)
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [dragging, setDragging] = useState(false)
  const [recent, setRecent] = useState<ExtractionJob[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const [categoryList, extractionList] = await Promise.all([
          api.listCategories(),
          api.listExtractions(10),
        ])
        if (cancelled) return
        setCategories(categoryList)
        const ready = categoryList.find((item) => item.knowledge_ready)
        if (ready) setCategoryId(ready.id)
        setRecent(extractionList.jobs)
      } catch (err) {
        if (!cancelled) setError(err instanceof ApiError ? err.message : '品类加载失败')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [api])

  const selected = categories.find((item) => item.id === categoryId)
  const canSubmit =
    Boolean(categoryId) &&
    Boolean(selected?.knowledge_ready) &&
    !submitting &&
    (mode === 'upload' ? Boolean(file) : text.trim().length > 0)

  function pickFile(next: File | null) {
    setError('')
    if (!next) {
      setFile(null)
      return
    }
    const lower = next.name.toLowerCase()
    if (!['.docx', '.pdf', '.xlsx'].some((suffix) => lower.endsWith(suffix))) {
      setError('仅支持 DOCX、PDF、XLSX 格式的招标参数文件')
      return
    }
    setFile(next)
  }

  function onFileInput(event: ChangeEvent<HTMLInputElement>) {
    pickFile(event.target.files?.[0] ?? null)
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    pickFile(event.dataTransfer.files?.[0] ?? null)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!canSubmit) return
    setSubmitting(true)
    setError('')
    try {
      const result =
        mode === 'upload' && file
          ? await api.uploadExtraction(categoryId, file)
          : await api.createTextExtraction({
              category_id: categoryId,
              text: text.trim(),
              title: title.trim() || undefined,
            })
      navigate(`/extractions/${result.job_id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '提交失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page page-narrow">
      <header className="page-head">
        <div>
          <span className="eyebrow">新建评估</span>
          <h1>提交招标技术参数</h1>
          <p>只选择产品品类，系统使用该品类由管理员维护的固定知识发布版本。普通售前用户无需选择版本或上传产品资料。</p>
        </div>
      </header>

      <ol className="steps">
        <li className="step step-active"><span>1</span>选择产品品类</li>
        <li className={`step ${categoryId ? 'step-active' : ''}`}><span>2</span>提交招标参数</li>
        <li className="step"><span>3</span>确认提取结果</li>
        <li className="step"><span>4</span>逐条评估与导出</li>
      </ol>

      {error && <Alert tone="danger" title="提交未完成">{error}</Alert>}

      {loading ? (
        <div className="loading-panel"><Spinner label="正在加载产品品类…" /></div>
      ) : (
        <form onSubmit={submit} className="card form-card">
          <section className="form-section">
            <div className="section-head">
              <div>
                <h2>产品品类</h2>
                <p>只能选择已完成知识准备的产品品类，不显示也不要求选择具体版本。</p>
              </div>
              <span className="section-count">{categories.filter((item) => item.knowledge_ready).length} 个可用品类</span>
            </div>
            {categories.length === 0 ? (
              <Alert tone="warn" title="暂无产品品类">
                请先由管理员创建产品品类并发布产品知识；售前用户不能在此处上传源码或产品文档。
              </Alert>
            ) : (
              <div className="category-grid">
                {categories.map((category) => {
                  const disabled = !category.knowledge_ready
                  return (
                    <button
                      key={category.id}
                      type="button"
                      disabled={disabled}
                      className={`category-option ${categoryId === category.id ? 'category-selected' : ''}`}
                      onClick={() => setCategoryId(category.id)}
                    >
                      <span className="category-check">
                        {categoryId === category.id ? <Icon name="check" size={14} /> : null}
                      </span>
                      <strong>{category.name}</strong>
                      <span className="category-code">{category.code}</span>
                      <Badge tone={category.knowledge_ready ? 'ok' : 'muted'}>
                        {category.knowledge_ready ? '知识已就绪' : '知识未就绪'}
                      </Badge>
                    </button>
                  )
                })}
              </div>
            )}
          </section>

          <section className="form-section">
            <div className="section-head">
              <div>
                <h2>招标参数来源</h2>
                <p>上传含技术参数表的招标文件，或直接粘贴技术参数文本。系统会先提取，不会直接给出最终结论。</p>
              </div>
            </div>
            <div className="segmented">
              <button
                type="button"
                className={mode === 'upload' ? 'segment segment-active' : 'segment'}
                onClick={() => setMode('upload')}
              >
                <Icon name="upload" size={16} /> 上传文件
              </button>
              <button
                type="button"
                className={mode === 'text' ? 'segment segment-active' : 'segment'}
                onClick={() => setMode('text')}
              >
                <Icon name="file" size={16} /> 粘贴文本
              </button>
            </div>

            {mode === 'upload' ? (
              <div
                className={`dropzone ${dragging ? 'dropzone-active' : ''} ${file ? 'dropzone-filled' : ''}`}
                onDragOver={(event) => {
                  event.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                onClick={() => inputRef.current?.click()}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') inputRef.current?.click()
                }}
              >
                <input
                  ref={inputRef}
                  type="file"
                  accept={ACCEPT}
                  onChange={onFileInput}
                  hidden
                />
                <span className="dropzone-icon"><Icon name={file ? 'file' : 'upload'} size={26} /></span>
                {file ? (
                  <>
                    <strong>{file.name}</strong>
                    <span className="muted">{(file.size / 1024 / 1024).toFixed(2)} MB · 点击可重新选择</span>
                  </>
                ) : (
                  <>
                    <strong>拖拽文件到此处，或点击选择</strong>
                    <span className="muted">支持 DOCX、PDF、XLSX；建议不超过 {MAX_DISPLAY_MB} MB</span>
                  </>
                )}
              </div>
            ) : (
              <div className="stack">
                <label className="field">
                  <span>文件名称（可选）</span>
                  <input
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                    maxLength={200}
                    placeholder="例如：某项目 WAF 招标技术参数"
                  />
                </label>
                <label className="field">
                  <span>技术参数文本</span>
                  <textarea
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    rows={12}
                    placeholder="粘贴招标文件中的技术参数章节，例如：&#10;1、支持 HTTP/1.1、HTTP/2 和 WebSocket 协议解析。&#10;2、支持 SQL 注入、XSS、命令注入等攻击检测与阻断。"
                  />
                </label>
              </div>
            )}
          </section>

          <div className="form-actions">
            <div className="form-hint">
              <Icon name="alert" size={16} />
              <span>提交后先生成提取结果，需人工确认才会启动评估。</span>
            </div>
            <button type="submit" className="button button-primary" disabled={!canSubmit}>
              {submitting ? <Spinner label="正在提交…" /> : '提交并提取参数'}
            </button>
          </div>
        </form>
      )}

      {recent.length > 0 && (
        <section className="card recent-card">
          <div className="section-head">
            <div>
              <h2>最近的招标提取</h2>
              <p>提取完成后可继续核对参数并启动评估。</p>
            </div>
          </div>
          <div className="recent-list">
            {recent.map((job) => (
              <button
                key={job.job_id}
                type="button"
                className="recent-row"
                onClick={() => navigate(`/extractions/${job.job_id}`)}
              >
                <span className="recent-file"><Icon name="file" size={17} /></span>
                <span className="recent-main">
                  <strong>{job.original_filename || shortHash(job.job_id, 12)}</strong>
                  <span>{formatDate(job.created_at)} · 提取 {job.extracted_count} 条</span>
                </span>
                <Badge tone={statusTone(job.status)}>{statusLabel(job.status)}</Badge>
                <Icon name="chevron" size={16} />
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
