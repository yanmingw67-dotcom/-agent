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

const STORAGE_KEY = 'p23.workbench.token'

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
  const route = useHashRoute()

  useEffect(() => {
    if (!window.location.hash) window.location.hash = '/tasks'
  }, [])

  const api = useMemo(() => {
    if (!token) return null
    return new ApiClient(token, () => {
      setToken('')
      setAuthMessage('登录已失效，请重新输入访问令牌。')
    })
  }, [token])

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
    try {
      window.localStorage.removeItem(STORAGE_KEY)
    } catch {
      // 忽略存储不可用。
    }
    setToken('')
    setAuthMessage('')
  }

  if (!api) {
    return <Login onLogin={login} initialError={authMessage} />
  }

  let content
  if (route.name === 'new') {
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
    <Layout routeName={route.name} token={token} onLogout={logout}>
      {content}
    </Layout>
  )
}
