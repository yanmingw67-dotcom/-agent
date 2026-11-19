import { useEffect, useState } from 'react'
import type { ApiClient } from '../api'
import { ApiError } from '../api'
import { formatDate, verdictTone } from '../format'
import type { HumanReview, ParameterResult, ReviewAction, Verdict } from '../types'
import { Alert, Badge, Modal, Spinner } from './ui'

const ACTIONS: Array<{ value: ReviewAction; label: string; help: string }> = [
  { value: 'accept', label: '认可自动结论', help: '记录人工已核对，最终应答与系统自动结论一致。' },
  { value: 'adjust', label: '人工调整最终应答', help: '必须选择与自动结论不同的最终应答，并填写调整理由。' },
  { value: 'reject', label: '不认可自动结论', help: '记录不认可自动结论；仍需给出人工最终应答与理由。' },
  { value: 'reassess', label: '需要重新评估', help: '记录需重新评估，不填写最终应答；自动结论与证据保持不变。' },
]

function otherVerdict(verdict: Verdict): Verdict {
  if (verdict === '支持') return '不支持'
  if (verdict === '不支持') return '支持'
  return '支持'
}

export function ReviewDialog({
  api,
  jobId,
  parameter,
  onClose,
  onSaved,
}: {
  api: ApiClient
  jobId: string
  parameter: ParameterResult
  onClose: () => void
  onSaved: () => void
}) {
  const [action, setAction] = useState<ReviewAction>('accept')
  const [finalVerdict, setFinalVerdict] = useState<Verdict>(otherVerdict(parameter.verdict))
  const [comment, setComment] = useState('')
  const [reason, setReason] = useState('')
  const [requires, setRequires] = useState(false)
  const [history, setHistory] = useState<HumanReview[]>([])
  const [historyError, setHistoryError] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .listReviews(jobId, parameter.parameter_id)
      .then((data) => {
        if (!cancelled) setHistory(data.items)
      })
      .catch((err: unknown) => {
        if (!cancelled) setHistoryError(err instanceof ApiError ? err.message : '复核历史加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [api, jobId, parameter.parameter_id])

  useEffect(() => {
    if (action === 'adjust' || action === 'reject') {
      if (finalVerdict === parameter.verdict) setFinalVerdict(otherVerdict(parameter.verdict))
    }
    if (action === 'reassess') setRequires(true)
    if (action === 'accept') setRequires(false)
  }, [action, finalVerdict, parameter.verdict])

  const needsFinal = action === 'adjust' || action === 'reject'
  const canSubmit =
    !saving &&
    (!needsFinal || (finalVerdict !== parameter.verdict && reason.trim().length > 0)) &&
    (action !== 'reassess' || comment.trim().length > 0 || reason.trim().length > 0)

  async function submit() {
    if (!canSubmit) return
    setSaving(true)
    setError('')
    try {
      await api.submitReview(jobId, parameter.parameter_id, {
        action,
        final_verdict: needsFinal ? finalVerdict : undefined,
        comment: comment.trim(),
        adjust_reason: reason.trim(),
        requires_reevaluation: requires,
      })
      onSaved()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '复核提交失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title="人工复核"
      subtitle={`参数 ${parameter.order_index} · ${parameter.parameter_name}`}
      onClose={onClose}
      wide
    >
      <div className="review-locked">
        <div>
          <span>系统自动结论</span>
          <Badge tone={verdictTone(parameter.verdict)}>{parameter.verdict}</Badge>
        </div>
        <p>自动结论与证据来自已持久化的评估结果，人工复核不会改写它们，只会新增一条复核记录。</p>
      </div>

      {history.length > 0 && (
        <div className="review-history">
          <h3>复核历史</h3>
          {history.map((item) => (
            <div key={item.review_id} className="history-row">
              <Badge tone={item.review_status === 'adjusted' ? 'warn' : item.review_status === 'rejected' ? 'danger' : 'ok'}>
                {item.review_status_label}
              </Badge>
              <span>自动：{item.auto_verdict}</span>
              <span>人工应答：{item.final_verdict || '未填写'}</span>
              <span>{formatDate(item.created_at)}</span>
              {item.adjust_reason && <p>{item.adjust_reason}</p>}
            </div>
          ))}
        </div>
      )}
      {historyError && <Alert tone="warn" title="复核历史不可用">{historyError}</Alert>}

      <div className="review-actions">
        {ACTIONS.map((item) => (
          <button
            key={item.value}
            type="button"
            className={action === item.value ? 'choice choice-active' : 'choice'}
            onClick={() => setAction(item.value)}
          >
            <strong>{item.label}</strong>
            <span>{item.help}</span>
          </button>
        ))}
      </div>

      {needsFinal && (
        <label className="field">
          <span>人工最终应答</span>
          <select
            value={finalVerdict}
            onChange={(event) => setFinalVerdict(event.target.value as Verdict)}
          >
            {(['支持', '不支持', '无法确认'] as Verdict[]).map((verdict) => (
              <option key={verdict} value={verdict} disabled={verdict === parameter.verdict}>
                {verdict}{verdict === parameter.verdict ? '（与自动结论相同，不可选）' : ''}
              </option>
            ))}
          </select>
        </label>
      )}

      <label className="field">
        <span>复核意见{action === 'reassess' ? '（与调整理由至少填写一项）' : ''}</span>
        <textarea
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          rows={3}
          maxLength={2000}
          placeholder="例如：已对照招标原文核对，自动结论与证据可接受。"
        />
      </label>

      {needsFinal && (
        <label className="field">
          <span>人工调整理由（必填）</span>
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            rows={3}
            maxLength={2000}
            placeholder="说明为什么人工应答与系统自动结论不同；不得仅写“人工判断”。"
          />
        </label>
      )}

      {action === 'reassess' && (
        <label className="check-line">
          <input
            type="checkbox"
            checked={requires}
            onChange={(event) => setRequires(event.target.checked)}
          />
          <span>标记需要重新评估</span>
        </label>
      )}

      {error && <Alert tone="danger" title="复核未保存">{error}</Alert>}

      <div className="modal-actions">
        <button type="button" className="button button-ghost" onClick={onClose}>取消</button>
        <button type="button" className="button button-primary" disabled={!canSubmit} onClick={() => void submit()}>
          {saving ? <Spinner label="正在保存…" /> : '保存复核记录'}
        </button>
      </div>
    </Modal>
  )
}
