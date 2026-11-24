import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApiClient } from '../api'
import type { AdminDocument, AdminSnapshot, DerivationChain, MaterialProvenance } from '../types'
import { MaterialConfirmation, ProvenanceOverview, ProvenanceProvider, ProvenanceRecords } from './MaterialProvenance'

const root: AdminSnapshot = {
  id: 'root', category_id: 'cat', status: 'extracted', index_status: 'indexed',
  source_sha256: 'a'.repeat(64), derived_from_snapshot_id: null, derivation_type: null,
  data_classification: 'enterprise', created_at: null,
}
const derived = { ...root, id: 'derived', derived_from_snapshot_id: root.id, derivation_type: 'document_update' }
const document: AdminDocument = {
  id: 'doc', category_id: 'cat', snapshot_id: 'derived', filename: '合成测试手册.docx',
  sha256: 'b'.repeat(64), size: 100, data_classification: 'enterprise', parse_status: 'parsed',
  segment_count: 1, error_code: null, parser_version: 'test', parse_details: {}, origin: {},
}
const record: MaterialProvenance = {
  id: 'rec', category_id: 'cat', snapshot_id: 'root', document_id: null, material_type: 'source_code',
  content_sha256: root.source_sha256!, data_classification: 'enterprise', declared_origin: '测试来源',
  usage_scope: '仅用于本地合成组件测试', confirmation_reference: '测试依据',
  confirmed_by: '测试管理员', created_at: '2026-01-01T00:00:00Z', status: 'confirmed', revoked_at: null, revoked_by: null,
}
function harness(options: {
  doc?: AdminDocument; initial?: MaterialProvenance[]; invalid?: string; response?: number; network?: boolean
  chainCategory?: string; sourcePending?: boolean; sourceSynthetic?: boolean
} = {}) {
  const selected = options.doc ? derived : { ...root,
    index_status: options.sourcePending ? 'pending' : 'indexed',
    data_classification: options.sourceSynthetic ? 'synthetic_public' : 'enterprise',
  }
  const chain: DerivationChain = {
    snapshot_id: selected.id, category_id: options.chainCategory || 'cat', root_snapshot_id: root.id,
    valid: !options.invalid, error_code: options.invalid || null,
    chain: options.doc ? [root, selected] : [selected],
  }
  let rows = options.initial || []
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (options.network) throw new TypeError('network unavailable')
    if (init?.method === 'POST') {
      if (options.response) return new Response(JSON.stringify({
        error_code: options.response === 409 ? 'PROVENANCE_ALREADY_CONFIRMED' : 'TEST_ERROR',
        message: options.response === 409 ? '该资料已有生效确认记录' : '测试拒绝',
      }), { status: options.response })
      if (path.endsWith('/revoke')) {
        rows = rows.map(r => ({ ...r, status: 'revoked', revoked_by: '测试管理员', revoked_at: '2026-01-02T00:00:00Z' }))
      } else {
        const body = JSON.parse(String(init.body))
        rows = [{ ...record, ...body, document_id: body.document_id || null,
          content_sha256: body.material_type === 'technical_document' ? options.doc!.sha256 : root.source_sha256! }]
      }
      return Response.json({ id: 'rec', status: rows[0].status })
    }
    if (path.endsWith('/derivation-chain')) return Response.json(chain)
    return Response.json({ items: rows })
  })
  vi.stubGlobal('fetch', fetcher)
  const api = new ApiClient('synthetic-test-token')
  const element = <ProvenanceProvider api={api} categoryId="cat" categoryName="测试品类" snapshot={selected}>
    <ProvenanceOverview />
    {options.doc && <MaterialConfirmation document={options.doc} />}
    <ProvenanceRecords />
  </ProvenanceProvider>
  const view = render(element)
  return { ...view, element, fetcher }
}
function posts(fetcher: ReturnType<typeof vi.fn>) {
  return fetcher.mock.calls.filter(call => call[1]?.method === 'POST')
}
async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('来源说明'), '人工填写的测试来源')
  await user.type(screen.getByLabelText('适用范围'), '仅用于本地合成测试验证')
  await user.type(screen.getByLabelText('确认依据'), '人工填写的测试依据')
  await user.click(screen.getByRole('button', { name: '提交来源确认' }))
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('管理员资料来源页面', () => {
  it('确认根源码、使用管理员鉴权并在重新挂载后读取后端记录', async () => {
    const user = userEvent.setup()
    const h = harness()
    await user.click(await screen.findByRole('button', { name: '确认源码来源' }))
    expect((screen.getByLabelText('来源说明') as HTMLTextAreaElement).value).toBe('')
    expect(screen.queryByLabelText('快照 ID')).toBeNull()
    await fillAndSubmit(user)
    await screen.findByText('来源确认状态：已确认')
    expect(posts(h.fetcher)).toHaveLength(1)
    const [url, init] = posts(h.fetcher)[0]
    expect(url).toContain('/categories/cat/material-provenance')
    expect(init.headers.Authorization).toBe('Bearer synthetic-test-token')
    expect(JSON.parse(init.body)).toMatchObject({ snapshot_id: 'root', material_type: 'source_code' })
    expect(JSON.parse(init.body)).not.toHaveProperty('document_id')
    h.unmount()
    render(h.element)
    await screen.findByText('来源确认状态：已确认')
    expect(screen.getByText('确认依据：人工填写的测试依据')).toBeTruthy()
  })
  it('从派生快照确认源码时绑定根 ID，文档确认绑定其所属快照', async () => {
    const user = userEvent.setup()
    const h = harness({ doc: document })
    await user.click(await screen.findByRole('button', { name: '确认源码来源' }))
    await fillAndSubmit(user)
    await screen.findByText('来源确认状态：已确认')
    await user.click(screen.getByRole('button', { name: '确认来源' }))
    await fillAndSubmit(user)
    await waitFor(() => expect(posts(h.fetcher)).toHaveLength(2))
    expect(JSON.parse(posts(h.fetcher)[0][1].body).snapshot_id).toBe('root')
    expect(JSON.parse(posts(h.fetcher)[1][1].body)).toMatchObject({
      snapshot_id: 'derived', document_id: 'doc', material_type: 'technical_document',
    })
  })
  it.each([
    [{ ...document, parse_status: 'pending' }, '文档尚未成功解析'],
    [{ ...document, parse_status: 'failed', error_code: 'PARSE_ERROR' }, 'PARSE_ERROR'],
    [{ ...document, segment_count: 0 }, '文档没有可核验的有效分段'],
    [{ ...document, category_id: 'other' }, '资料与当前品类或快照不匹配'],
    [{ ...document, snapshot_id: 'other' }, '资料与当前品类或快照不匹配'],
    [{ ...document, data_classification: 'synthetic_public' }, '仅正式企业资料允许确认'],
  ])('不允许不满足条件的文档提交：%j', async (doc, reason) => {
    const h = harness({ doc })
    await screen.findByText(new RegExp(reason))
    expect((screen.getByRole('button', { name: '确认来源' }) as HTMLButtonElement).disabled).toBe(true)
    expect(posts(h.fetcher)).toHaveLength(0)
  })
  it.each([403, 404, 409, 422])('明确展示 HTTP %i 且不伪造成功', async response => {
    const user = userEvent.setup()
    const h = harness({ response })
    await user.click(await screen.findByRole('button', { name: '确认源码来源' }))
    await fillAndSubmit(user)
    await screen.findByText(response === 409 ? /PROVENANCE_ALREADY_CONFIRMED/ : /TEST_ERROR/)
    await screen.findByText('来源确认状态：未确认')
    expect(screen.getByText(response === 409 ? /PROVENANCE_ALREADY_CONFIRMED/ : /TEST_ERROR/)).toBeTruthy()
    expect(screen.queryByText('来源确认状态：已确认')).toBeNull()
    expect(posts(h.fetcher)).toHaveLength(1)
  })
  it('撤销必须二次确认，提交后重新查询后端而非仅更新本地状态', async () => {
    const user = userEvent.setup()
    const confirmation = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const h = harness({ initial: [record] })
    const button = await screen.findByRole('button', { name: '撤销确认' })
    await user.click(button)
    expect(confirmation).toHaveBeenCalledTimes(1)
    expect(posts(h.fetcher)).toHaveLength(0)
    confirmation.mockReturnValue(true)
    await user.click(button)
    await screen.findByText('来源确认状态：已撤销')
    expect(screen.getByText(/撤销人：测试管理员/)).toBeTruthy()
    const calls = h.fetcher.mock.calls
    const postIndex = calls.findIndex(c => c[1]?.method === 'POST')
    expect(String(calls[postIndex][0])).toContain('/rec/revoke')
    expect(calls.slice(postIndex + 1).some(c => String(c[0]).endsWith('/material-provenance') && c[1]?.method === 'GET')).toBe(true)
  })
  it('派生链无效时保留后端错误码，不以相同哈希显示通过', async () => {
    const h = harness({ invalid: 'DERIVATION_PARENT_MISSING' })
    await screen.findByText(/DERIVATION_PARENT_MISSING/)
    expect(screen.queryByText('派生链已通过服务端核验')).toBeNull()
    expect(screen.queryByRole('button', { name: '确认源码来源' })).toBeNull()
    expect(posts(h.fetcher)).toHaveLength(0)
  })
  it('跨品类链响应被拒绝', async () => {
    harness({ chainCategory: 'other' })
    expect((await screen.findAllByText(/PROVENANCE_SCOPE_MISMATCH/)).length).toBeGreaterThan(0)
    expect(screen.queryByText('派生链已通过服务端核验')).toBeNull()
  })
  it('网络异常显示中文并禁止确认', async () => {
    harness({ network: true })
    expect((await screen.findAllByText(/网络异常/)).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: '确认源码来源' })).toBeNull()
  })
  it('快照未就绪或合成资料不能确认', async () => {
    harness({ sourcePending: true })
    await screen.findByText(/快照尚未就绪/)
    expect((screen.getByRole('button', { name: '确认源码来源' }) as HTMLButtonElement).disabled).toBe(true)
  })
  it('拒绝仅空白或不满足后端长度的字段', async () => {
    const user = userEvent.setup()
    const h = harness()
    await user.click(await screen.findByRole('button', { name: '确认源码来源' }))
    await user.type(screen.getByLabelText('来源说明'), '   ')
    await user.type(screen.getByLabelText('适用范围'), '测试用途')
    await user.type(screen.getByLabelText('确认依据'), '测试依据')
    await user.click(screen.getByRole('button', { name: '提交来源确认' }))
    await screen.findByText(/来源说明去除首尾空白/)
    expect(posts(h.fetcher)).toHaveLength(0)
  })
  it('复制完整 SHA-256', async () => {
    const user = userEvent.setup()
    harness()
    const copy = vi.spyOn(navigator.clipboard, 'writeText')
    await user.click(await screen.findByRole('button', { name: '复制完整哈希' }))
    expect(copy).toHaveBeenCalledWith(root.source_sha256)
    await screen.findByText('已复制完整 SHA-256')
  })
  it('刷新查询失败时不保留已确认假象', async () => {
    const user = userEvent.setup()
    const h = harness({ initial: [record] })
    await screen.findByText('来源确认状态：已确认')
    h.fetcher.mockRejectedValueOnce(new TypeError('offline'))
    await user.click(screen.getByRole('button', { name: '刷新确认记录' }))
    expect((await screen.findAllByText(/网络异常/)).length).toBeGreaterThan(0)
    expect(screen.queryByText('来源确认状态：已确认')).toBeNull()
    expect(screen.queryByRole('button', { name: '撤销确认' })).toBeNull()
  })
  it('已有生效记录不可重复确认，完整记录可展开', async () => {
    const user = userEvent.setup()
    const h = harness({ initial: [record] })
    await screen.findByText('来源确认状态：已确认')
    expect((screen.getByRole('button', { name: '确认源码来源' }) as HTMLButtonElement).disabled).toBe(true)
    await user.click(screen.getByText('查看完整记录'))
    expect(screen.getByText('content_sha256')).toBeTruthy()
    expect(posts(h.fetcher)).toHaveLength(0)
  })
})
