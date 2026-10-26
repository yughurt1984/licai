import { useState } from 'react'
import { fetchJSON } from '../hooks/useApi'

/**
 * 全屏登录/注册页。App 顶层 auth gate 在未登录时整屏渲染本组件(不进 NAV/PAGES 体系,
 * 不搅动 hash 路由)。注册/登录成功后带用户信息回调 onAuthed —— cookie 由后端种下,
 * 之后所有同源请求(含 9 处裸 fetch)自动携带。
 */
export default function AuthPage({ onAuthed }) {
  const [mode, setMode] = useState('login')          // login | register
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState({ text: '', ok: null })

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    if (mode === 'register' && password !== password2) {
      setStatus({ text: '两次输入的密码不一致', ok: false })
      return
    }
    setBusy(true)
    setStatus({ text: '', ok: null })
    try {
      const path = mode === 'login' ? '/api/auth/login' : '/api/auth/register'
      const user = await fetchJSON(path, {
        method: 'POST',
        body: JSON.stringify({ username: username.trim(), password }),
      })
      setStatus({ text: '成功, 进入中…', ok: true })
      onAuthed(user)
    } catch (err) {
      setStatus({ text: err.message || '失败, 请重试', ok: false })
    } finally {
      setBusy(false)
    }
  }

  const inputCls = 'w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text outline-none focus:border-accent transition-colors'
  const tabCls = (active) => `flex-1 py-2 text-[13px] rounded-md transition-colors cursor-pointer ${
    active ? 'bg-surface text-text font-medium' : 'text-text-dim hover:text-text'}`

  return (
    <div className="min-h-screen flex items-center justify-center px-4 relative">
      {/* 与主页一致的氛围背景(网格 + 顶部暖光, 见 index.css) */}
      <div className="w-full max-w-[360px] relative z-10">
        <div className="text-center mb-6">
          <h1 className="text-[22px] font-semibold tracking-tight text-accent">理财助手</h1>
          <p className="text-[12px] text-text-dim mt-1">
            {mode === 'login' ? '登录以查看你的持仓与市场数据' : '创建账号(首个注册用户看到独立空账本)'}
          </p>
        </div>

        <div className="rounded-xl border border-border bg-surface/80 backdrop-blur-xl p-5 space-y-4 fade-up">
          <div className="flex gap-1 bg-bg rounded-lg p-1">
            <button type="button" className={tabCls(mode === 'login')} onClick={() => { setMode('login'); setStatus({ text: '', ok: null }) }}>
              登录
            </button>
            <button type="button" className={tabCls(mode === 'register')} onClick={() => { setMode('register'); setStatus({ text: '', ok: null }) }}>
              注册
            </button>
          </div>

          <form onSubmit={submit} className="space-y-3">
            <input
              className={inputCls}
              placeholder="用户名 (2-32 字符)"
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
            />
            <input
              className={inputCls}
              type="password"
              placeholder="密码 (6 位以上)"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            />
            {mode === 'register' && (
              <input
                className={inputCls}
                type="password"
                placeholder="再输一遍密码"
                value={password2}
                onChange={e => setPassword2(e.target.value)}
                autoComplete="new-password"
              />
            )}

            {status.text && (
              <p className={`text-[12px] ${status.ok === true ? 'text-bull' : 'text-bear'}`}>{status.text}</p>
            )}

            <button
              type="submit"
              disabled={busy || !username.trim() || !password}
              className="w-full py-2 rounded-lg bg-accent text-bg-deeper text-[13px] font-medium
                         hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity cursor-pointer"
            >
              {busy ? '请稍候…' : (mode === 'login' ? '登录' : '注册并登录')}
            </button>
          </form>
        </div>

        <p className="text-center text-[11px] text-text-muted mt-4">
          本机/局域网部署 · 数据存本地 PostgreSQL
        </p>
      </div>
    </div>
  )
}
