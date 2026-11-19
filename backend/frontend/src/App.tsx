import { useEffect, useMemo, useState } from 'react'
import { ApiClient } from './api'
import { Layout } from './components/Layout'
import { useHashRoute } from './hooks'
import { EvaluationProgress } from './pages/EvaluationProgress'
import { ExtractionDetail } from './pages/ExtractionDetail'
import { Login } from './pages/Login'
import { NewTask } from './pages/NewTask'
import { ResultDetail } from './pages/ResultDetail'
import { TaskList } from './pages/TaskList'
import { AdminCategories } from './pages/AdminCategories'
import { AdminOverview } from './pages/AdminOverview'
import { AdminWorkspace } from './pages/AdminWorkspace'
import { Alert, Spinner } from './components/ui'
import type { Identity } from './types'

const STORAGE_KEY = 'p23.workbench.token'

function clearStoredToken() {
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 忽略存储不可用。
  }
}

function storedToken(): string {
  try {
    return window.localStorage.getItem(STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

export default function App() {
  const [token, setToken] = useState(storedToken)
  const [authMessage, setAuthMessage] = useState('')
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [identityLoading, setIdentityLoading] = useState(false)
  const [identityError, setIdentityError] = useState('')
  const route = useHashRoute()

  useEffect(() => {
    if (!window.location.hash) window.location.hash = '/tasks'
  }, [])

  const api = useMemo(() => {
    if (!token) return null
    return new ApiClient(token, () => {
      // 认证失败时同时清除内存态与持久化凭证，避免刷新页面后继续复用失效令牌。
      clearStoredToken()
      setToken('')
      setAuthMessage('登录已失效，请重新输入访问令牌。')
    })
  }, [token])

  useEffect(() => {
    if (!api) {
      setIdentity(null)
      return undefined
    }
    let active = true
    setIdentityLoading(true)
    setIdentityError('')
    api
      .me()
      .then((value) => {
        if (active) setIdentity(value)
      })
      .catch(() => {
        // 普通售前使用旧后端或仅开放检索模式时，身份接口不可用不应阻断评估工作台。
        if (active) {
          setIdentity(null)
          setIdentityError('无法确认管理员身份，请确认后端已启用 /api/v1/auth/me。')
        }
      })
      .finally(() => {
        if (active) setIdentityLoading(false)
      })
    return () => {
      active = false
    }
  }, [api])

  function login(next: string) {
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // 浏览器禁用存储时仅保留当前会话内存状态。
    }
    setAuthMessage('')
    setToken(next)
    if (!window.location.hash || window.location.hash === '#/') {
      window.location.hash = '/tasks'
    }
  }

  function logout() {
    clearStoredToken()
    setToken('')
    setAuthMessage('')
  }

  if (!api) {
    return <Login onLogin={login} initialError={authMessage} />
  }

  const adminRoute = route.name === 'admin' || route.name.startsWith('admin-')
  if (adminRoute && !identityLoading && !identity?.is_admin) {
    return (
      <Layout routeName={route.name} token={token} onLogout={logout} isAdmin={false}>
        <div className="page">
          <header className="page-head">
            <div>
              <span className="eyebrow">管理员工作台</span>
              <h1>需要管理员权限</h1>
              <p>管理员页面由服务端权限校验；普通售前账号只能查看已发布知识。</p>
            </div>
          </header>
          <Alert tone="danger" title="访问被拒绝">
            {identityError || '当前令牌不是管理员令牌，无法进入产品知识维护工作台。'}
          </Alert>
        </div>
      </Layout>
    )
  }

  let content
  if (adminRoute && identityLoading) {
    content = (
      <div className="page">
        <div className="loading-panel">
          <Spinner label="正在校验管理员身份…" />
        </div>
      </div>
    )
  } else if (route.name === 'admin') {
    content = <AdminOverview api={api} />
  } else if (route.name === 'admin-categories') {
    content = <AdminCategories api={api} />
  } else if (route.name === 'admin-category' && route.id) {
    content = <AdminWorkspace api={api} id={route.id} initialTab={route.tab} />
  } else if (route.name === 'new') {
    content = <NewTask api={api} />
  } else if (route.name === 'extraction' && route.id) {
    content = <ExtractionDetail api={api} id={route.id} />
  } else if (route.name === 'evaluation' && route.id) {
    content = <EvaluationProgress api={api} id={route.id} />
  } else if (route.name === 'result' && route.id) {
    content = <ResultDetail api={api} id={route.id} />
  } else {
    content = <TaskList api={api} />
  }

  return (
    <Layout
      routeName={route.name}
      token={token}
      onLogout={logout}
      isAdmin={Boolean(identity?.is_admin)}
    >
      {content}
    </Layout>
  )
}
