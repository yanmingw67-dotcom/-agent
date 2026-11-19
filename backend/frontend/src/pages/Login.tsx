import { useState, type FormEvent } from 'react'
import { Icon } from '../components/ui'

export function Login({
  onLogin,
  initialError = '',
}: {
  onLogin: (token: string) => void
  initialError?: string
}) {
  const [token, setToken] = useState('')
  const [error, setError] = useState(initialError)

  function submit(event: FormEvent) {
    event.preventDefault()
    const value = token.trim()
    if (!value) {
      setError('请输入访问令牌')
      return
    }
    if (/\s/.test(value)) {
      setError('令牌不能包含空格')
      return
    }
    setError('')
    onLogin(value)
  }

  return (
    <div className="login-page">
      <section className="login-intro">
        <div className="brand-lg">
          <span className="brand-mark">
            <Icon name="logo" size={24} />
          </span>
          <span>产品参数评估工作台</span>
        </div>
        <h1>把招标技术参数逐条落到可核验证据上。</h1>
        <p className="login-lead">
          面向售前用户的招标参数符合性评估：选择产品品类、提交招标参数、确认提取结果，
          再逐条查看系统自动结论与固定知识版本中的证据，最后导出可编辑的 Word / Excel。
        </p>
        <ul className="login-points">
          <li><span>01</span>只选产品品类，不选择版本，也不上传源码或产品文档</li>
          <li><span>02</span>自动结论与人工复核分列保存，复核不会改写原始结论</li>
          <li><span>03</span>证据来自任务固定的知识发布版本，来源不可用时失败关闭</li>
          <li><span>04</span>导出与页面一致，Word 为可编辑表格，Excel 防公式注入</li>
        </ul>
      </section>
      <section className="login-panel">
        <div className="login-card">
          <div className="login-card-head">
            <span className="eyebrow">售前工作台</span>
            <h2>进入评估工作台</h2>
            <p>使用管理员分配的访问令牌登录。令牌只保存在当前浏览器，不会发送到其他服务。</p>
          </div>
          <form onSubmit={submit} className="stack">
            <label className="field">
              <span>访问令牌</span>
              <input
                type="password"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                placeholder="粘贴 READ_TOKEN"
                autoComplete="off"
                autoFocus
              />
            </label>
            {error && <div className="form-error">{error}</div>}
            <button type="submit" className="button button-primary button-block">
              进入工作台
            </button>
          </form>
          <div className="login-note">
            <Icon name="alert" size={16} />
            <span>本工作台不保存招标文件到浏览器；文件仅提交给当前后端服务。</span>
          </div>
        </div>
        <p className="login-foot">普通售前用户入口 · 产品知识由管理员统一维护</p>
      </section>
    </div>
  )
}
