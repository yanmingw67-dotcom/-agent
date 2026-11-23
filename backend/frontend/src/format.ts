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
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`
  return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`
}

// 服务端错误码到管理员可读说明的映射；不得把 413 或解压失败统一显示成“上传失败”。
export const UPLOAD_ERROR_MESSAGES: Record<string, string> = {
  REQUEST_TOO_LARGE: '文件超过上传限制（HTTP 413）：源码 ZIP 单次最大 1 GiB，请压缩或拆分后重试。',
  UPLOAD_SIZE_EXCEEDED: '源码 ZIP 超过 1 GiB 上传上限。',
  UPLOAD_TOO_LARGE: '源码 ZIP 超过 1 GiB 上传上限。',
  FILE_SIZE_EXCEEDED: 'ZIP 内单个文件超过 128 MiB 限制，请拆分该文件后重新打包。',
  TOTAL_SIZE_EXCEEDED: '解压总大小超过 4 GiB 限制，请精简源码或排除依赖/构建产物。',
  ACTUAL_SIZE_EXCEEDED: '解压实际字节数超过安全预算（单文件 128 MiB 或总量 4 GiB）。',
  ENTRY_LIMIT_EXCEEDED: 'ZIP 条目过多（上限 20000），请排除 node_modules、构建产物和缓存目录。',
  COMPRESSION_RATIO_EXCEEDED: 'ZIP 压缩膨胀比超过 100 倍，已按 ZIP Bomb 风险拒绝。',
  ZIP64_UNSUPPORTED: '当前 ZIP 格式不受支持：ZIP64 结构损坏、缺失定位器或多卷归档。',
  STORAGE_QUOTA_EXCEEDED: '受控存储配额不足，请管理员检查存储配额与历史快照后再上传。',
  STORAGE_LOW_SPACE: '磁盘剩余空间不足，上传或解压已安全终止，请清理空间后重试。',
  ARCHIVE_HASH_MISMATCH: '源码归档哈希校验失败，文件可能在上传后被修改。',
  EXTRACTED_HASH_MISMATCH: '解压文件哈希校验失败，快照未提交。',
  INVALID_ZIP: 'ZIP 结构不合法或已损坏，请重新打包。',
  INVALID_ZIP_SIGNATURE: '文件不是有效的 ZIP 归档。',
  EMPTY_UPLOAD: '上传文件为空。',
  ENCRYPTED_ARCHIVE: '不支持加密 ZIP，请使用未加密归档。',
  MULTIDISK_UNSUPPORTED: '不支持多卷 ZIP，请合并为单卷归档。',
  LINK_OR_SPECIAL_FILE: 'ZIP 包含符号链接或特殊文件，已拒绝。',
  PATH_COLLISION: 'ZIP 存在大小写或路径冲突，请修正后重新打包。',
  UNSAFE_PATH: 'ZIP 包含不安全路径（如 ..、绝对路径或非法字符）。',
  IMPORT_INTERNAL_ERROR: '解压任务内部错误，请查看任务错误码与 worker 日志。',
}

export function uploadErrorMessage(
  code: string | null | undefined,
  status: number,
  fallback: string,
): string {
  if (code && UPLOAD_ERROR_MESSAGES[code]) return UPLOAD_ERROR_MESSAGES[code]
  if (status === 413) return UPLOAD_ERROR_MESSAGES.REQUEST_TOO_LARGE
  return fallback
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
