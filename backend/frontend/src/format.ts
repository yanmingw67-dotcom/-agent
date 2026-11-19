import type { TaskStatus, Verdict } from './types'

export const STATUS_LABELS: Record<TaskStatus, string> = {
  queued: '排队中',
  running: '评估中',
  succeeded: '评估完成',
  partial: '部分完成',
  failed: '评估失败',
  cancelled: '已取消',
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[status as TaskStatus] ?? status
}

export function statusTone(status: string): string {
  if (status === 'succeeded') return 'ok'
  if (status === 'partial') return 'warn'
  if (status === 'failed') return 'danger'
  if (status === 'queued' || status === 'running') return 'info'
  return 'muted'
}

export function verdictTone(verdict: string | null | undefined): string {
  if (verdict === '支持') return 'ok'
  if (verdict === '不支持') return 'danger'
  if (verdict === '无法确认') return 'warn'
  return 'muted'
}

export function verdictLabel(verdict: Verdict | null | undefined): string {
  if (verdict === '支持' || verdict === '不支持' || verdict === '无法确认') return verdict
  return '未填写'
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

export function shortHash(value: string | null | undefined, length = 12): string {
  if (!value) return '—'
  return value.length > length ? `${value.slice(0, length)}…` : value
}

export function percent(done: number, total: number): number {
  if (!total) return 0
  return Math.min(100, Math.max(0, Math.round((done / total) * 100)))
}

export function countLabel(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : String(value)
}
