import type { ReactNode } from 'react'
import { navigate } from '../hooks'
import { Icon } from './ui'

export function Layout({
  routeName,
  token,
  onLogout,
  isAdmin = false,
  children,
}: {
  routeName: string
  token: string
  onLogout: () => void
  isAdmin?: boolean
  children: ReactNode
}) {
  const active = routeName.startsWith('admin')
    ? 'admin'
    : routeName === 'new'
      ? 'new'
      : 'tasks'
  const masked = token.length > 12 ? `${token.slice(0, 4)}…${token.slice(-4)}` : '已登录'
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Icon name="logo" size={22} />
          </span>
          <div>
            <strong>参数评估工作台</strong>
            <span>P2.3 · 售前评估</span>
          </div>
        </div>
        <nav className="side-nav" aria-label="主导航">
          <button
            type="button"
            className={active === 'tasks' ? 'nav-item nav-item-active' : 'nav-item'}
            onClick={() => navigate('/tasks')}
          >
            <Icon name="tasks" size={18} />
            评估任务
          </button>
          <button
            type="button"
            className={active === 'new' ? 'nav-item nav-item-active' : 'nav-item'}
            onClick={() => navigate('/new')}
          >
            <Icon name="plus" size={18} />
            新建评估
          </button>
          {isAdmin && (
            <button
              type="button"
              className={active === 'admin' ? 'nav-item nav-item-active' : 'nav-item'}
              onClick={() => navigate('/admin')}
            >
              <Icon name="logo" size={18} />
              管理员工作台
            </button>
          )}
        </nav>
        <div className="side-note">
          <strong>评估边界</strong>
          <p>
            {isAdmin
              ? '管理员工作台用于受控资料与知识维护；发布必须复用审核与发布服务，前端不能直接切换生效指针。'
              : '系统自动结论只来自固定发布版本中的已审核产品能力；证据缺失时保持“无法确认”。'}
          </p>
        </div>
        <div className="side-foot">
          <div className="account">
            <span className="account-dot" />
            <div>
              <span>当前登录</span>
              <strong>{masked}</strong>
            </div>
          </div>
          <button type="button" className="link-button" onClick={onLogout}>
            退出登录
          </button>
        </div>
      </aside>
      <main className="main-area">{children}</main>
    </div>
  )
}
