import { useEffect, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { WECHAT_HINT } from './wechatHint'

/**
 * 全屏登录/注册页。App 顶层 auth gate 在未登录时整屏渲染本组件(不进 NAV/PAGES 体系,
 * 不搅动 hash 路由)。注册/登录成功后带用户信息回调 onAuthed —— cookie 由后端种下,
 * 之后所有同源请求(含 9 处裸 fetch)自动携带。
 *
 * 登录: 用户名或邮箱 + 密码。注册: 用户名 + 邮箱 + 邮箱验证码 + 密码
 * (验证码走 /api/auth/email-code, 60s 重发冷却由后端限频兜底)。
 * 微信登录/注册为预留入口 —— 后端 wechat_openid 列已建, WECHAT_APP_ID 未配置前按钮置灰。
 */
export default function AuthPage({ onAuthed }) {
  const [mode, setMode] = useState('login')          // login | register
  const [identifier, setIdentifier] = useState('')
  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState({ text: '', ok: null })
  // 验证码倒计时 + 发送态(与主提交 busy 分开, 避免发送按钮被提交逻辑锁住)
  const [countdown, setCountdown] = useState(0)
  const [sending, setSending] = useState(false)

  useEffect(() => {
    if (countdown <= 0) return
    const t = setInterval(() => setCountdown(c => c - 1), 1000)
    return () => clearInterval(t)
  }, [countdown > 0])   // eslint-disable-line react-hooks/exhaustive-deps

  const sendCode = async () => {
    if (sending || countdown > 0) return
    if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/.test(email.trim())) {
      setStatus({ text: '请先填正确的邮箱', ok: false })
      return
    }
    setSending(true)
    setStatus({ text: '', ok: null })
    try {
      await fetchJSON('/api/auth/email-code', {
        method: 'POST',
        body: JSON.stringify({ email: email.trim(), purpose: 'register' }),
      })
      setStatus({ text: '验证码已发送, 请查收邮箱(留意垃圾箱)', ok: true })
      setCountdown(60)
    } catch (err) {
      setStatus({ text: err.message || '发送失败, 请重试', ok: false })
    } finally {
      setSending(false)
    }
  }

  const submit = async (e) => {
    e.preventDefault()
    if (busy) return
    if (mode === 'register') {
      if (password !== password2) return setStatus({ text: '两次输入的密码不一致', ok: false })
      if (!code.trim()) return setStatus({ text: '请填邮箱验证码', ok: false })
    }
    setBusy(true)
    setStatus({ text: '', ok: null })
    try {
      const path = mode === 'login' ? '/api/auth/login' : '/api/auth/register'
      const body = mode === 'login'
        ? { username: identifier.trim(), password }
        : { username: username.trim(), email: email.trim(), code: code.trim(), password }
      const user = await fetchJSON(path, { method: 'POST', body: JSON.stringify(body) })
      setStatus({ text: '成功, 进入中…', ok: true })
      onAuthed(user)
    } catch (err) {
      setStatus({ text: err.message || '失败, 请重试', ok: false })
    } finally {
      setBusy(false)
    }
  }

  const switchMode = (m) => {
    setMode(m)
    setStatus({ text: '', ok: null })
  }

  const inputCls = 'w-full bg-bg border border-border rounded-lg px-3 py-2.5 text-[0.9286rem] text-text outline-none focus:border-accent transition-colors'
  const tabCls = (active) => `flex-1 py-2 text-[0.9286rem] rounded-md transition-colors cursor-pointer ${
    active ? 'bg-surface text-text font-medium shadow-sm' : 'text-text-dim hover:text-text'}`

  return (
    <div className="min-h-screen flex items-center justify-center px-4 py-10 relative">
      {/* 与主页一致的氛围背景(网格 + 顶部暖光, 见 index.css) */}
      <div className="w-full max-w-[400px] relative z-10">
        <div className="text-center mb-6 fade-up">
          {/* 圆标 logo: 参考站式品牌位, 头像上传前的占位也同款(见 AccountPage) */}
          <div className="w-14 h-14 mx-auto rounded-full bg-accent/15 border border-accent/30
                          flex items-center justify-center text-accent text-[1.5714rem] font-semibold mb-3">
            理
          </div>
          <h1 className="text-[1.5714rem] font-semibold tracking-tight text-text-bright">理财助手</h1>
          <p className="text-[0.8571rem] text-text-dim mt-1.5">
            {mode === 'login' ? '登录后查看你的持仓与市场数据' : '创建账号 · 首个注册用户看到独立空账本'}
          </p>
        </div>

        <div className="rounded-2xl border border-border bg-surface/80 backdrop-blur-xl p-5 space-y-4 fade-up shadow-lg">
          <div className="flex gap-1 bg-bg rounded-lg p-1">
            <button type="button" className={tabCls(mode === 'login')} onClick={() => switchMode('login')}>
              登录
            </button>
            <button type="button" className={tabCls(mode === 'register')} onClick={() => switchMode('register')}>
              注册
            </button>
          </div>

          <form onSubmit={submit} className="space-y-3">
            {mode === 'login' ? (
              <input
                className={inputCls}
                placeholder="用户名或邮箱"
                value={identifier}
                onChange={e => setIdentifier(e.target.value)}
                autoComplete="username"
                autoFocus
              />
            ) : (
              <>
                <input
                  className={inputCls}
                  placeholder="用户名 (2-32 字符)"
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  autoComplete="username"
                />
                <div className="flex gap-2">
                  <input
                    className={`${inputCls} flex-1 min-w-0`}
                    type="email"
                    placeholder="邮箱"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    autoComplete="email"
                  />
                  <button type="button" onClick={sendCode}
                    disabled={sending || countdown > 0 || !email.trim()}
                    className="shrink-0 px-3 rounded-lg border border-accent/40 bg-accent/10 text-accent
                               text-[0.8571rem] whitespace-nowrap hover:bg-accent/20 disabled:opacity-40
                               disabled:cursor-not-allowed transition-opacity cursor-pointer">
                    {sending ? '发送中…' : countdown > 0 ? `${countdown}s` : '发送验证码'}
                  </button>
                </div>
                <input
                  className={inputCls}
                  placeholder="邮箱验证码 (10 分钟内有效)"
                  value={code}
                  onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                />
              </>
            )}

            <input
              className={inputCls}
              type="password"
              placeholder={mode === 'login' ? '密码' : '密码 (6 位以上)'}
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
              <p className={`text-[0.8571rem] ${status.ok === true ? 'text-bull' : 'text-bear'}`}>{status.text}</p>
            )}

            <button
              type="submit"
              disabled={busy || (mode === 'login'
                ? !identifier.trim() || !password
                : !username.trim() || !email.trim() || !code.trim() || !password)}
              className="w-full py-2.5 rounded-lg bg-accent text-bg-deeper text-[0.9286rem] font-medium
                         hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity cursor-pointer"
            >
              {busy ? '请稍候…' : (mode === 'login' ? '登录' : '注册并登录')}
            </button>
          </form>

          {/* 分隔线 + 微信预留入口: wechat_openid 列已建, WECHAT_APP_ID 配置后接真实流程 */}
          <div className="flex items-center gap-3 pt-1">
            <div className="flex-1 border-t border-border-subtle" />
            <span className="text-[0.7857rem] text-text-muted">其他方式</span>
            <div className="flex-1 border-t border-border-subtle" />
          </div>
          <button type="button" onClick={() => setStatus({ text: WECHAT_HINT, ok: null })}
            className="w-full py-2.5 rounded-lg border border-border bg-surface-2/60 text-[0.9286rem] text-text-dim
                       hover:text-text hover:border-border-med transition-colors cursor-pointer flex items-center justify-center gap-2">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
              {/* 微信气泡双圆(简化): 主气泡 + 次气泡 */}
              <path d="M9.5 4C5.9 4 3 6.4 3 9.4c0 1.7 1 3.2 2.5 4.2l-.6 2.1 2.3-1.2c.7.2 1.5.3 2.3.3h.4a5 5 0 01-.2-1.4c0-2.9 2.7-5.2 6-5.2h.3C15.4 5.9 12.7 4 9.5 4zm-2 3.1a.9.9 0 110 1.8.9.9 0 010-1.8zm4.5 0a.9.9 0 110 1.8.9.9 0 010-1.8z" />
              <path d="M15.7 9.5c-2.9 0-5.2 1.9-5.2 4.3s2.3 4.3 5.2 4.3c.6 0 1.2-.1 1.7-.2l1.9 1-.5-1.8c1.3-.8 2.1-2 2.1-3.3 0-2.4-2.3-4.3-5.2-4.3zm-1.7 2.4a.8.8 0 110 1.6.8.8 0 010-1.6zm3.4 0a.8.8 0 110 1.6.8.8 0 010-1.6z" />
            </svg>
            {mode === 'login' ? '微信登录' : '微信注册'}
            <span className="text-[0.75rem] text-text-muted">暂未开通</span>
          </button>
        </div>

        <p className="text-center text-[0.7857rem] text-text-muted mt-4">
          本机/局域网部署 · 数据存本地 PostgreSQL
        </p>
      </div>
    </div>
  )
}
