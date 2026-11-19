import { useEffect, useRef, useState } from 'react'

export type Route = { name: string; id?: string }

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#/, '') || '/tasks'
  const parts = path.split('/').filter(Boolean)
  if (parts.length === 0) return { name: 'tasks' }
  if (parts[0] === 'new') return { name: 'new' }
  if (parts[0] === 'tasks' && parts[1]) return { name: 'result', id: parts[1] }
  if (parts[0] === 'results' && parts[1]) return { name: 'result', id: parts[1] }
  if (parts[0] === 'extractions' && parts[1]) return { name: 'extraction', id: parts[1] }
  if (parts[0] === 'evaluations' && parts[1]) return { name: 'evaluation', id: parts[1] }
  return { name: 'tasks' }
}

export function navigate(path: string): void {
  window.location.hash = path
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function usePolling(callback: () => void, active: boolean, interval = 2000): void {
  const saved = useRef(callback)
  saved.current = callback
  useEffect(() => {
    if (!active) return undefined
    const timer = window.setInterval(() => saved.current(), interval)
    return () => window.clearInterval(timer)
  }, [active, interval])
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · 产品参数评估工作台`
  }, [title])
}
