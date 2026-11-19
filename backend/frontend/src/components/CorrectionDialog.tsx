import { useEffect, useMemo, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { formatDate } from '../format'
import type { CorrectionRecord, TenderParameter } from '../types'
import { Alert, Badge, Modal, Spinner } from './ui'

export type CorrectionMode = 'edit' | 'split' | 'merge' | 'add'

const ACTION_LABELS: Record<string, string> = {
  edit: '修改参数',
  split: '拆分参数',
  merge: '合并参数',
  add: '新增参数',
  confirm: '确认参数',
}

function splitGuess(text: string): string[] {
  const lines = text
    .split(/\n+/)
    .map((item) => item.trim())
    .filter(Boolean)
  if (lines.length >= 2) return lines
  const clauses = text
    .split(/[；;]/)
    .map((item) => item.trim())
    .filter(Boolean)
  return clauses.length >= 2 ? clauses : [text.trim(), '']
}

function correctionBefore(item: CorrectionRecord): string {
  if (item.before.parameter_name || item.before.original_text) {
    return [item.before.parameter_name, item.before.original_text].filter(Boolean).join('\n')
  }
  if (item.before.parameter_ids && item.before.parameter_ids.length) {
    return item.before.parameter_ids.join('、')
  }
  return '无（新增参数）'
}

function correctionAfter(item: CorrectionRecord): string {
  if (item.after.parameter_name || item.after.original_text) {
    return [item.after.parameter_name, item.after.original_text].filter(Boolean).join('\n')
  }
  if (item.after.parameter_ids && item.after.parameter_ids.length) {
    return item.after.parameter_ids.join('、')
  }
  return '无'
}

function CorrectionHistory({ items }: { items: CorrectionRecord[] }) {
  if (items.length === 0) {
    return <p className="form-hint">该任务尚无人工修正记录；保存后将自动写入不可变审计。</p>
  }
  return (
    <div className="correction-history">
      {items.slice(0, 12).map((item) => (
        <div key={item.correction_id} className="correction-history-row">
          <div className="correction-history-head">
            <Badge tone={item.action === 'confirm' ? 'ok' : 'warn'}>
              {ACTION_LABELS[item.action] || item.action}
            </Badge>
            <span>{formatDate(item.created_at)}</span>
            <span>操作人 {item.actor}</span>
          </div>
          <div className="correction-diff">
            <div>
              <span>修正前</span>
              <pre>{correctionBefore(item)}</pre>
            </div>
            <div>
              <span>修正后</span>
              <pre>{correctionAfter(item)}</pre>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

export function CorrectionDialog({
  api,
  jobId,
  mode,
  parameter,
  mergeParameters = [],
  onClose,
  onSaved,
}: {
  api: ApiClient
  jobId: string
  mode: CorrectionMode
  parameter?: TenderParameter
  mergeParameters?: TenderParameter[]
  onClose: () => void
  onSaved: (message: string) => void
}) {
  const [name, setName] = useState(parameter ? parameter.parameter_name : '')
  const [text, setText] = useState(parameter ? parameter.original_text : '')
  const [parts, setParts] = useState<Array<{ parameter_name: string; original_text: string }>>(
    parameter
      ? splitGuess(parameter.original_text).map((value, index) => ({
          parameter_name: index === 0 ? parameter.parameter_name : '',
          original_text: value,
        }))
      : [{ parameter_name: '', original_text: '' }],
  )
  const [mergeName, setMergeName] = useState(
    mergeParameters.length ? mergeParameters[0].parameter_name : '',
  )
  const [history, setHistory] = useState<CorrectionRecord[]>([])
  const [historyError, setHistoryError] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .listCorrections(jobId, parameter ? parameter.id : undefined)
      .then((data) => {
        if (!cancelled) setHistory(data.items)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setHistoryError(err instanceof ApiError ? err.message : '修正历史加载失败')
        }
      })
    return () => {
      cancelled = true
    }
  }, [api, jobId, parameter])

  const title = useMemo(() => {
    if (mode === 'edit') return '人工修正参数'
    if (mode === 'split') return '拆分被合并的参数'
    if (mode === 'merge') return '合并被拆开的参数'
    return '新增遗漏参数'
  }, [mode])

  const subtitle = parameter
    ? '参数 ' + parameter.order_index + ' · ' + parameter.parameter_name
    : '已选择 ' + mergeParameters.length + ' 条参数'

  const mergedText = mergeParameters.map((item) => item.original_text).join('\n')
  const mergedName = mergeName.trim() || (mergeParameters.length ? mergeParameters[0].parameter_name : '')
  const splitValues = parts.filter((item) => item.original_text.trim())
  const canSubmit =
    !saving &&
    (mode === 'edit'
      ? Boolean(name.trim()) &&
        Boolean(text.trim()) &&
        Boolean(
          parameter &&
            (name.trim() !== parameter.parameter_name || text.trim() !== parameter.original_text),
        )
      : mode === 'split'
        ? splitValues.length >= 2 &&
          parts.every((item) => !item.original_text.trim() || Boolean(item.original_text.trim()))
        : mode === 'merge'
          ? mergeParameters.length >= 2 && Boolean(mergedName)
          : Boolean(text.trim()))

  async function submit() {
    if (!canSubmit) return
    setSaving(true)
    setError('')
    try {
      if (mode === 'edit' && parameter) {
        const payload: { parameter_name?: string; original_text?: string } = {}
        if (name.trim() !== parameter.parameter_name) payload.parameter_name = name.trim()
        if (text.trim() !== parameter.original_text) payload.original_text = text.trim()
        await api.editParameter(jobId, parameter.id, payload)
        onSaved('参数修正已保存，服务端已重建原子要求与逻辑树；请重新核对后再评估。')
      } else if (mode === 'split' && parameter) {
        await api.splitParameter(
          jobId,
          parameter.id,
          parts
            .filter((item) => item.original_text.trim())
            .map((item) => ({
              parameter_name: item.parameter_name.trim() || undefined,
              original_text: item.original_text.trim(),
            })),
        )
        onSaved('参数已拆分；原参数保留在审计中，新参数已生成独立原子要求。')
      } else if (mode === 'merge') {
        await api.mergeParameters(
          jobId,
          mergeParameters.map((item) => item.id),
          mergedName,
        )
        onSaved('参数已合并；被合并参数保留为失效记录，合并后原子要求已重建。')
      } else {
        await api.addParameter(jobId, {
          parameter_name: name.trim() || undefined,
          original_text: text.trim(),
        })
        onSaved('遗漏参数已新增；请核对原子要求与来源说明后再评估。')
      }
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.code + '：' + err.message : '修正保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title={title} subtitle={subtitle} onClose={onClose} wide>
      <Alert tone="warn" title="会影响后续评估">
        人工修正会改变后续 P2.2 评估使用的参数正文与原子要求。已经生成的评估结果不会自动更新；
        如需按修正后的参数结论，必须重新启动评估。原提取内容与本次修正会写入不可变审计。
      </Alert>

      {mode === 'edit' && parameter && (
        <>
          <label className="field">
            <span>参数名称</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={500}
            />
          </label>
          <label className="field">
            <span>参数正文</span>
            <textarea value={text} onChange={(event) => setText(event.target.value)} rows={6} />
          </label>
          {parameter.source_locator && parameter.source_locator.anchor_status && (
            <p className="form-hint">
              该参数来自人工修正，原始文件定位已标记为仅审计用途，不再作为有效锚点。
            </p>
          )}
          <div className="correction-preview">
            <div>
              <span>修正前</span>
              <pre>{parameter.original_text}</pre>
            </div>
            <div>
              <span>修正后</span>
              <pre>{text || '（参数正文不能为空）'}</pre>
            </div>
          </div>
        </>
      )}

      {mode === 'split' && parameter && (
        <>
          <div className="correction-preview">
            <div>
              <span>拆分前原文</span>
              <pre>{parameter.original_text}</pre>
            </div>
            <div>
              <span>拆分后 {splitValues.length} 条</span>
              <pre>
                {splitValues
                  .map((item, index) => String(index + 1) + '. ' + item.original_text)
                  .join('\n') || '至少填写两个非空部分'}
              </pre>
            </div>
          </div>
          <p className="form-hint">
            每一部分会成为独立参数并重新解析原子要求；原参数不会再出现在当前清单中，但审计仍可追溯。
          </p>
          <div className="split-parts">
            {parts.map((item, index) => (
              <div className="split-part" key={index}>
                <label className="field">
                  <span>第 {index + 1} 部分名称（可选）</span>
                  <input
                    value={item.parameter_name}
                    onChange={(event) =>
                      setParts((current) =>
                        current.map((row, rowIndex) =>
                          rowIndex === index
                            ? { ...row, parameter_name: event.target.value }
                            : row,
                        ),
                      )
                    }
                    maxLength={500}
                  />
                </label>
                <label className="field">
                  <span>第 {index + 1} 部分正文</span>
                  <textarea
                    rows={3}
                    value={item.original_text}
                    onChange={(event) =>
                      setParts((current) =>
                        current.map((row, rowIndex) =>
                          rowIndex === index
                            ? { ...row, original_text: event.target.value }
                            : row,
                        ),
                      )
                    }
                  />
                </label>
                <button
                  type="button"
                  className="button button-ghost button-small"
                  disabled={parts.length <= 2}
                  onClick={() => setParts((current) => current.filter((_, row) => row !== index))}
                >
                  删除此部分
                </button>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="button button-secondary button-small"
            onClick={() =>
              setParts((current) => [...current, { parameter_name: '', original_text: '' }])
            }
          >
            增加一部分
          </button>
        </>
      )}

      {mode === 'merge' && (
        <>
          <div className="correction-preview">
            <div>
              <span>合并前（{mergeParameters.length} 条）</span>
              <pre>
                {mergeParameters
                  .map(
                    (item, index) =>
                      String(index + 1) + '. ' + item.parameter_name + '\n' + item.original_text,
                  )
                  .join('\n\n')}
              </pre>
            </div>
            <div>
              <span>合并后</span>
              <pre>{mergedName + '\n' + mergedText}</pre>
            </div>
          </div>
          <label className="field">
            <span>合并后的参数名称</span>
            <input
              value={mergeName}
              onChange={(event) => setMergeName(event.target.value)}
              maxLength={500}
            />
          </label>
          <p className="form-hint">
            合并正文按所选参数的当前顺序拼接；原参数标记为失效但仍保留，合并结果会重新生成原子要求与逻辑树。
          </p>
        </>
      )}

      {mode === 'add' && (
        <>
          <label className="field">
            <span>参数名称（留空则从正文自动提取）</span>
            <input value={name} onChange={(event) => setName(event.target.value)} maxLength={500} />
          </label>
          <label className="field">
            <span>遗漏参数正文</span>
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={6}
              placeholder="粘贴招标文件中遗漏的技术参数原文"
            />
          </label>
        </>
      )}

      {parameter && parameter.atomic_requirements.length > 0 && mode !== 'add' && (
        <p className="form-hint">
          当前参数有 {parameter.atomic_requirements.length} 条原子要求；保存修改后将以服务端重建结果为准。
        </p>
      )}

      <div>
        <h3 className="correction-history-title">修正记录与审计</h3>
        <CorrectionHistory items={history} />
      </div>
      {historyError && (
        <Alert tone="warn" title="修正记录不可用">
          {historyError}
        </Alert>
      )}
      {error && (
        <Alert tone="danger" title="修正未保存">
          {error}
        </Alert>
      )}

      <div className="modal-actions">
        <button type="button" className="button button-ghost" onClick={onClose}>
          取消
        </button>
        <button
          type="button"
          className="button button-primary"
          disabled={!canSubmit}
          onClick={() => void submit()}
        >
          {saving ? <Spinner label="正在保存…" /> : '保存并重新核对'}
        </button>
      </div>
    </Modal>
  )
}
