import type { ModelAuthorization } from '../types'

const reasons: Record<string, string> = {
  MODEL_NOT_CONFIGURED: '模型、网关地址或密钥尚未配置完整',
  MODEL_DATA_NOT_AUTHORIZED: '企业数据运行时开关已关闭',
  MODEL_AUTHORIZATION_RECORD_REQUIRED: '没有匹配当前模型、完整端点和分类的有效审批',
}

export function ModelGateFacts({ builder }: { builder: ModelAuthorization['builder'] }) {
  return <>
    <div><dt>模型名称</dt><dd>{builder.model_name || '未设置'}</dd></div>
    <div><dt>审批分类</dt><dd>{builder.data_classification}</dd></div>
    <div><dt>企业运行时开关</dt><dd>{builder.allow_enterprise ? '开' : '关'}</dd></div>
    <div><dt>匹配的正式授权</dt><dd>{builder.active_authorization ? '有效，至 ' + builder.active_authorization.expires_at : '无有效记录'}</dd></div>
    <div><dt>禁止原因</dt><dd>{builder.reason_code ? reasons[builder.reason_code] || builder.reason_code : '无'}</dd></div>
  </>
}
