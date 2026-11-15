import { useEffect, useRef, useState } from 'react'
import { fetchJSON } from '../hooks/useApi'
import { setPrefs, usePrefs } from '../prefs'
import { WECHAT_HINT } from './wechatHint'
import BrokerSection from './account/BrokerSection'

/**
 * 账户中心(替代原「个人中心」): 左侧节导航(账户/显示设置/券商设置) + 右侧内容,
 * 形态对齐参考站 rich.luyaolab.cn/settings。
 * - 账户: 资料(头像/昵称/ID/注册信息) + 登录方式(绑定邮箱/微信预留/改密) + 退出/注销
 * - 显示设置: 外观(浅色/深色/跟随系统) + 显示偏好(金额万单位/红涨绿跌) + 语言(预留置灰)
 * - 券商设置: 原 ProfilePage 的券商费率配置整节迁入
 *
 * user 由 App 传入(/api/auth/me 结果); onUserChange 上报资料变更; onAvatarChange
 * 同步顶栏小头像; onLogout 走 App 的登出链路。
 */
const SECTIONS = [
  { key: 'account', label: '账户', icon: (
      <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c1.2-3.4 3.8-5 7-5s5.8 1.6 7 5" /></>
    ) },
  { key: 'display', label: '显示设置', icon: (
      <><circle cx="12" cy="12" r="8" /><circle cx="9" cy="9" r="1" fill="currentColor" stroke="none" /><path d="M12 4v16M4 12h16" strokeWidth="1.2" /></>
    ) },
  { key: 'broker', label: '券商设置', icon: (
      <><rect x="3" y="6" width="18" height="13" rx="2" /><path d="M3 10h18M7 14h4" /></>
    ) },
]

export default function AccountPage({ user, avatarUrl, onUserChange, onAvatarChange, onLogout }) {
  const [section, setSection] = useState('account')

  return (
    <div className="max-w-[1060px] mx-auto px-2 md:px-4 py-4 flex gap-5 items-start">
      {/* 左节导航: 参考图窄栏, 选中 = accent 左条 + 浅底 */}
      <nav className="w-40 shrink-0 rounded-xl border border-border bg-surface/60 p-2 space-y-1 sticky top-[76px]">
        {SECTIONS.map(s => {
          const on = section === s.key
          return (
            <button key={s.key} onClick={() => setSection(s.key)}
              className={`w-full flex items-center gap-2.5 px-3 h-10 rounded-lg text-left text-[13px] transition-colors cursor-pointer
                ${on ? 'bg-accent/12 text-accent font-medium' : 'text-text-dim hover:text-text hover:bg-surface-3/60'}`}>
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" className="shrink-0">
                {s.icon}
              </svg>
              {s.label}
            </button>
          )
        })}
      </nav>

      <div className="flex-1 min-w-0">
        {section === 'account' && (
          <AccountTab user={user} avatarUrl={avatarUrl} onUserChange={onUserChange}
            onAvatarChange={onAvatarChange} onLogout={onLogout} />
        )}
        {section === 'display' && <DisplayTab />}
        {section === 'broker' && <BrokerTab />}
      </div>
    </div>
  )
}

/* ══════════════════ 账户 ══════════════════ */

function AccountTab({ user, avatarUrl, onUserChange, onAvatarChange, onLogout }) {
  const [status, setStatus] = useState({ text: '', ok: null })
  const [editingName, setEditingName] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const [bindOpen, setBindOpen] = useState(false)
  const [pwdOpen, setPwdOpen] = useState(false)
  const fileRef = useRef(null)

  const refreshUser = async () => {
    try { onUserChange(await fetchJSON('/api/auth/me')) } catch {}
  }

  /* ── 头像上传: canvas 压到 256px JPEG(~15-30KB, 远低于后端 200KB 限) ── */
  const pickAvatar = () => fileRef.current?.click()
  const onAvatarFile = async (e) => {
    const f = e.target.files?.[0]
    e.target.value = ''                       // 连选同一张也能再触发
    if (!f) return
    if (!/^image\/(png|jpeg|webp)$/.test(f.type)) {
      return setStatus({ text: '只支持 png/jpeg/webp 图片', ok: false })
    }
    try {
      const data = await compressImage(f, 256)
      await fetchJSON('/api/auth/me/avatar', { method: 'PUT', body: JSON.stringify({ data }) })
      onAvatarChange?.(data)
      setStatus({ text: '头像已更新', ok: true })
    } catch (err) {
      setStatus({ text: err.message || '上传失败', ok: false })
    }
  }
  const removeAvatar = async () => {
    try {
      await fetchJSON('/api/auth/me/avatar', { method: 'DELETE' })
      onAvatarChange?.(null)
      setStatus({ text: '已恢复默认头像', ok: true })
    } catch (err) { setStatus({ text: err.message || '失败', ok: false }) }
  }

  const saveName = async () => {
    try {
      const r = await fetchJSON('/api/auth/me', { method: 'POST', body: JSON.stringify({ display_name: nameDraft }) })
      setEditingName(false)
      await refreshUser()
      setStatus({ text: r.display_name ? '昵称已更新' : '昵称已清空(显示用户名)', ok: true })
    } catch (err) { setStatus({ text: err.message || '保存失败', ok: false }) }
  }

  const copyId = async () => {
    try {
      await navigator.clipboard.writeText(String(user.user_id))
      setStatus({ text: '用户 ID 已复制', ok: true })
    } catch { setStatus({ text: '复制失败, 请手动选择复制', ok: false }) }
  }

  const rowCls = 'flex items-center justify-between gap-3 px-4 py-3 min-h-[52px] border-b border-border-subtle last:border-b-0'
  const chevron = <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" className="text-text-muted shrink-0"><path d="M9 6l6 6-6 6" /></svg>

  const group = (children, extra = '') => (
    <div className={`rounded-xl border border-border bg-surface/70 overflow-hidden divide-y divide-border-subtle ${extra}`}>
      {children}
    </div>
  )

  const initial = (user.display_name || user.username || '?').trim().charAt(0).toUpperCase()

  return (
    <div className="space-y-4 fade-up">
      {/* 标题带 + 注销账号(预留) */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-semibold text-text-bright m-0">个人资料</h2>
          <p className="text-[12px] text-text-dim m-0 mt-1">管理头像、昵称、登录方式和账户安全</p>
        </div>
        <button onClick={() => setStatus({
            text: '注销账号功能即将上线: 涉及全部持仓/流水等用户数据的级联清理, 暂时未开放。', ok: null })}
          className="text-[12.5px] text-bear hover:text-bear-bright transition-colors cursor-pointer shrink-0 mt-1">
          注销账号
        </button>
      </div>

      {status.text && (
        <p className={`text-[12px] m-0 ${status.ok === true ? 'text-bull' : status.ok === false ? 'text-bear' : 'text-text-dim'}`}>
          {status.text}
        </p>
      )}

      {/* 头像: 居中大圆 + 铅笔角标(点击上传), 无头像时首字圆标 */}
      <div className="flex justify-center py-2">
        <button onClick={pickAvatar} title="更换头像"
          className="relative w-[104px] h-[104px] rounded-full overflow-hidden group cursor-pointer
                     bg-accent/15 border border-accent/30 flex items-center justify-center">
          {avatarUrl
            ? <img src={avatarUrl} alt="头像" className="w-full h-full object-cover" />
            : <span className="text-[36px] font-semibold text-accent select-none">{initial}</span>}
          <span className="absolute inset-0 hidden group-hover:flex items-center justify-center
                           bg-black/35 text-white text-[11px]">更换</span>
          <span className="absolute bottom-1 right-1 w-7 h-7 rounded-full bg-surface border border-border
                           flex items-center justify-center text-text-dim group-hover:text-accent transition-colors">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
              <path d="M4 20h4l10.5-10.5a2.1 2.1 0 10-3-3L5 17v3z" />
            </svg>
          </span>
        </button>
        {avatarUrl && (
          <button onClick={removeAvatar}
            className="self-end ml-2 text-[11px] text-text-muted hover:text-bear transition-colors cursor-pointer">移除头像</button>
        )}
        <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp"
          className="hidden" onChange={onAvatarFile} />
      </div>

      {/* 资料: 昵称 / 用户 ID / 注册日期 / 注册账号 */}
      {group(
        <>
          <div className={rowCls}>
            <span className="text-[13px] text-text">昵称</span>
            {editingName ? (
              <span className="flex items-center gap-2">
                <input autoFocus value={nameDraft} maxLength={32}
                  onChange={e => setNameDraft(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') saveName(); if (e.key === 'Escape') setEditingName(false) }}
                  className="bg-bg border border-border rounded-md px-2 py-1 text-[13px] text-text outline-none focus:border-accent w-44" />
                <button onClick={saveName} className="text-[12px] text-accent hover:opacity-80 cursor-pointer">保存</button>
                <button onClick={() => setEditingName(false)} className="text-[12px] text-text-dim hover:text-text cursor-pointer">取消</button>
              </span>
            ) : (
              <button onClick={() => { setNameDraft(user.display_name === user.username ? '' : (user.display_name || '')); setEditingName(true) }}
                className="flex items-center gap-1.5 text-[13px] text-text-dim hover:text-text transition-colors cursor-pointer">
                {user.display_name || user.username}
                {chevron}
              </button>
            )}
          </div>
          <div className={rowCls}>
            <span className="text-[13px] text-text">用户 ID</span>
            <button onClick={copyId} title="点击复制"
              className="flex items-center gap-1.5 text-[13px] text-text-dim font-mono hover:text-text transition-colors cursor-pointer">
              {user.user_id}
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 012-2h10" />
              </svg>
            </button>
          </div>
          <div className={rowCls}>
            <span className="text-[13px] text-text">注册日期</span>
            <span className="text-[13px] text-text-dim font-mono">{fmtDateTime(user.created_at)}</span>
          </div>
          <div className={rowCls}>
            <span className="text-[13px] text-text">注册账号</span>
            <span className="text-[13px] text-text-dim">{user.email ? '邮箱注册' : '用户名注册'}</span>
          </div>
        </>
      )}

      {/* 登录方式: 绑定邮箱 / 绑定微信(预留) / 修改密码 */}
      {group(
        <>
          <button onClick={() => setBindOpen(true)}
            className={`${rowCls} w-full text-left hover:bg-surface-3/40 transition-colors cursor-pointer`}>
            <span className="text-[13px] text-text">绑定邮箱</span>
            <span className="flex items-center gap-1.5 text-[13px] text-text-dim">
              {user.email || '未绑定'}
              {user.email && user.email_verified && <span className="text-[10.5px] text-bull">已验证</span>}
              {chevron}
            </span>
          </button>
          <button onClick={() => setStatus({ text: WECHAT_HINT, ok: null })}
            className={`${rowCls} w-full text-left hover:bg-surface-3/40 transition-colors cursor-pointer`}>
            <span className="text-[13px] text-text">绑定微信</span>
            <span className="flex items-center gap-1.5 text-[13px] text-text-dim">
              未绑定<span className="text-[10.5px] text-text-muted">暂未开通</span>
              {chevron}
            </span>
          </button>
          <button onClick={() => setPwdOpen(true)}
            className={`${rowCls} w-full text-left hover:bg-surface-3/40 transition-colors cursor-pointer`}>
            <span className="text-[13px] text-text">修改密码</span>
            {chevron}
          </button>
        </>
      )}

      {/* 退出登录 */}
      {group(
        <button onClick={onLogout}
          className={`${rowCls} w-full text-left hover:bg-surface-3/40 transition-colors cursor-pointer`}>
          <span className="text-[13px] text-accent font-medium">退出登录</span>
          {chevron}
        </button>
      )}

      {bindOpen && (
        <BindEmailModal
          current={user.email}
          onClose={() => setBindOpen(false)}
          onDone={async () => { setBindOpen(false); await refreshUser(); setStatus({ text: '邮箱已更新', ok: true }) }} />
      )}
      {pwdOpen && (
        <PasswordModal
          onClose={() => setPwdOpen(false)}
          onDone={() => { setPwdOpen(false); setStatus({ text: '密码已修改', ok: true }) }} />
      )}
    </div>
  )
}

/* 图片压缩: 最长边 maxDim, JPEG 质量 0.85。File → data URL */
function compressImage(file, maxDim = 256) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('读取文件失败'))
    reader.onload = () => {
      const img = new Image()
      img.onerror = () => reject(new Error('图片解析失败'))
      img.onload = () => {
        const scale = Math.min(1, maxDim / Math.max(img.width, img.height))
        const w = Math.max(1, Math.round(img.width * scale))
        const h = Math.max(1, Math.round(img.height * scale))
        const canvas = document.createElement('canvas')
        canvas.width = w; canvas.height = h
        canvas.getContext('2d').drawImage(img, 0, 0, w, h)
        resolve(canvas.toDataURL('image/jpeg', 0.85))
      }
      img.src = reader.result
    }
    reader.readAsDataURL(file)
  })
}

function fmtDateTime(s) {
  if (!s) return '—'
  // 库里是 UTC 文本/时间戳, 展示本地 'YYYY-MM-DD HH:mm'
  const d = new Date(s.includes('T') ? s : s.replace(' ', 'T') + 'Z')
  if (isNaN(d.getTime())) return String(s)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/* ── 绑定/换绑邮箱弹窗 ─────────────────────────────── */
function BindEmailModal({ current, onClose, onDone }) {
  const [email, setEmail] = useState(current || '')
  const [code, setCode] = useState('')
  const [countdown, setCountdown] = useState(0)
  const [sending, setSending] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (countdown <= 0) return
    const t = setInterval(() => setCountdown(c => c - 1), 1000)
    return () => clearInterval(t)
  }, [countdown > 0])   // eslint-disable-line react-hooks/exhaustive-deps

  const send = async () => {
    if (sending || countdown > 0) return
    setSending(true); setErr('')
    try {
      await fetchJSON('/api/auth/email-code', {
        method: 'POST', body: JSON.stringify({ email: email.trim(), purpose: 'bind' }),
      })
      setCountdown(60)
    } catch (e) { setErr(e.message || '发送失败') } finally { setSending(false) }
  }

  const submit = async () => {
    setBusy(true); setErr('')
    try {
      await fetchJSON('/api/auth/me/bind-email', {
        method: 'POST', body: JSON.stringify({ email: email.trim(), code: code.trim() }),
      })
      onDone()
    } catch (e) { setErr(e.message || '绑定失败') } finally { setBusy(false) }
  }

  const inp = 'w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text outline-none focus:border-accent transition-colors'
  return (
    <ModalShell title={current ? '换绑邮箱' : '绑定邮箱'} onClose={onClose}>
      <div className="space-y-3">
        <div className="flex gap-2">
          <input className={`${inp} flex-1 min-w-0`} type="email" placeholder="邮箱" value={email}
            onChange={e => setEmail(e.target.value)} autoFocus />
          <button type="button" onClick={send}
            disabled={sending || countdown > 0 || !email.trim()}
            className="shrink-0 px-3 rounded-lg border border-accent/40 bg-accent/10 text-accent text-[12px]
                       whitespace-nowrap hover:bg-accent/20 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer">
            {sending ? '发送中…' : countdown > 0 ? `${countdown}s` : '发送验证码'}
          </button>
        </div>
        <input className={inp} placeholder="邮箱验证码 (10 分钟内有效)" value={code} inputMode="numeric"
          onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} />
        {err && <p className="text-[12px] text-bear m-0">{err}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[12px] hover:text-text cursor-pointer">取消</button>
          <button onClick={submit} disabled={busy || !email.trim() || !code.trim()}
            className="px-4 py-1.5 rounded-md bg-accent text-bg-deeper text-[12px] font-medium hover:opacity-90 disabled:opacity-40 cursor-pointer">
            {busy ? '绑定中…' : '绑定'}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}

/* ── 修改密码弹窗 ─────────────────────────────────── */
function PasswordModal({ onClose, onDone }) {
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [newPwd2, setNewPwd2] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const submit = async () => {
    if (newPwd !== newPwd2) return setErr('两次输入的新密码不一致')
    setBusy(true); setErr('')
    try {
      await fetchJSON('/api/auth/me/password', {
        method: 'POST', body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
      })
      onDone()
    } catch (e) { setErr(e.message || '修改失败') } finally { setBusy(false) }
  }

  const inp = 'w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text outline-none focus:border-accent transition-colors'
  return (
    <ModalShell title="修改密码" onClose={onClose}>
      <div className="space-y-3">
        <input className={inp} type="password" placeholder="当前密码" value={oldPwd} autoFocus
          autoComplete="current-password" onChange={e => setOldPwd(e.target.value)} />
        <input className={inp} type="password" placeholder="新密码 (6 位以上)" value={newPwd}
          autoComplete="new-password" onChange={e => setNewPwd(e.target.value)} />
        <input className={inp} type="password" placeholder="再输一遍新密码" value={newPwd2}
          autoComplete="new-password" onChange={e => setNewPwd2(e.target.value)} />
        {err && <p className="text-[12px] text-bear m-0">{err}</p>}
        <div className="flex justify-end gap-2 pt-1">
          <button onClick={onClose} className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[12px] hover:text-text cursor-pointer">取消</button>
          <button onClick={submit} disabled={busy || !oldPwd || !newPwd}
            className="px-4 py-1.5 rounded-md bg-accent text-bg-deeper text-[12px] font-medium hover:opacity-90 disabled:opacity-40 cursor-pointer">
            {busy ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}

function ModalShell({ title, onClose, children }) {
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center px-4"
      style={{ background: 'rgba(0,0,0,0.4)' }} onClick={onClose}>
      <div className="w-full max-w-[380px] rounded-xl border border-border bg-surface p-4 shadow-xl fade-up"
        onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[14px] font-semibold text-text-bright m-0">{title}</h3>
          <button onClick={onClose} className="text-text-muted hover:text-text transition-colors cursor-pointer">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

/* ══════════════════ 显示设置 ══════════════════ */

const THEME_OPTIONS = [
  { key: 'light', label: '浅色', sub: '明亮清晰' },
  { key: 'dark', label: '深色', sub: '减少眩光' },
  { key: 'system', label: '跟随系统', sub: '自动切换' },
]

function AppearancePreview({ kind }) {
  // 参考图式迷你窗口预览: 浅/深两态直出, system 用对角分割双态
  const bar = (bg) => <div className="h-2 rounded-sm" style={{ background: bg }} />
  const frame = (bg, fg, sub) => (
    <div className="w-full h-full rounded-md border p-1.5 flex flex-col gap-1"
      style={{ background: bg, borderColor: sub }}>
      <div className="flex gap-0.5 mb-0.5">
        {[0, 1, 2].map(i => <span key={i} className="w-1 h-1 rounded-full" style={{ background: sub }} />)}
      </div>
      <div className="flex gap-1 flex-1">
        <div className="w-1/4 rounded-sm" style={{ background: sub }} />
        <div className="flex-1 flex flex-col gap-1 justify-center">
          {bar(fg)}{bar(fg)}
        </div>
      </div>
    </div>
  )
  if (kind === 'light') return frame('#ffffff', '#d8d2c4', '#e8e4da')
  if (kind === 'dark') return frame('#1d1c18', '#3a362c', '#2c2a24')
  return (
    <div className="relative w-full h-full">
      <div className="absolute inset-0">{frame('#ffffff', '#d8d2c4', '#e8e4da')}</div>
      <div className="absolute inset-0" style={{ clipPath: 'polygon(100% 0, 100% 100%, 0 100%)' }}>
        {frame('#1d1c18', '#3a362c', '#2c2a24')}
      </div>
    </div>
  )
}

function DisplayTab() {
  const prefs = usePrefs()

  return (
    <div className="space-y-6 fade-up">
      {/* 外观 */}
      <section>
        <h3 className="text-[14px] font-semibold text-text m-0 mb-2">外观</h3>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {THEME_OPTIONS.map(o => {
            const on = prefs.theme === o.key
            return (
              <button key={o.key} onClick={() => setPrefs({ theme: o.key })}
                className={`text-left rounded-xl border p-2.5 transition-all cursor-pointer
                  ${on ? 'border-accent bg-accent/8 shadow-[0_0_0_1px_var(--color-accent)]' : 'border-border bg-surface/70 hover:border-border-med'}`}>
                <div className="h-[72px] mb-2.5">
                  <AppearancePreview kind={o.key} />
                </div>
                <div className="flex items-center justify-between px-0.5">
                  <div className="flex items-center gap-2">
                    <ThemeMiniIcon kind={o.key} />
                    <div>
                      <div className={`text-[13px] font-medium ${on ? 'text-accent' : 'text-text'}`}>{o.label}</div>
                      <div className="text-[11px] text-text-dim">{o.sub}</div>
                    </div>
                  </div>
                  <RadioDot on={on} />
                </div>
              </button>
            )
          })}
        </div>
      </section>

      {/* 显示偏好 */}
      <section>
        <h3 className="text-[14px] font-semibold text-text m-0 mb-2">显示偏好</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {/* 金额万单位 */}
          <div className="rounded-xl border border-border bg-surface/70 p-4 flex items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              <span className="w-9 h-9 rounded-lg bg-accent/12 text-accent flex items-center justify-center shrink-0">
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round">
                  <circle cx="12" cy="12" r="8" /><path d="M14.5 9.5a2.5 2.5 0 00-2-1c-1.4 0-2.5.8-2.5 1.8s1 1.5 2.5 1.7 2.5.7 2.5 1.7-1.1 1.8-2.5 1.8a2.5 2.5 0 01-2-1M12 7v10" />
                </svg>
              </span>
              <div>
                <div className="text-[13.5px] font-medium text-text">金额以万为单位显示</div>
                <div className="text-[11.5px] text-text-dim mt-0.5">10,000 以上按万显示</div>
                <div className="flex gap-1.5 mt-2">
                  <span className="px-1.5 py-0.5 rounded bg-accent/10 text-accent text-[11px] font-mono">1.23万</span>
                  <span className="px-1.5 py-0.5 rounded bg-accent/10 text-accent text-[11px] font-mono">123.46万</span>
                </div>
              </div>
            </div>
            <Toggle on={prefs.wanUnit} onChange={() => setPrefs({ wanUnit: !prefs.wanUnit })} />
          </div>
          {/* 红涨绿跌 */}
          <div className="rounded-xl border border-border bg-surface/70 p-4 flex items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              <span className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
                style={{ background: 'var(--color-up-bg)', color: 'var(--color-up)' }}>
                <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 17l5-5 4 3 7-8" /><path d="M15 7h5v5" />
                </svg>
              </span>
              <div>
                <div className="text-[13.5px] font-medium text-text">红涨绿跌</div>
                <div className="text-[11.5px] text-text-dim mt-0.5">上涨显示红色, 下跌显示绿色 · 即时生效</div>
                <div className="flex gap-1.5 mt-2">
                  <span className="px-1.5 py-0.5 rounded text-[11px] font-mono"
                    style={{ background: 'var(--color-up-bg)', color: 'var(--color-up)' }}>+8.26%</span>
                  <span className="px-1.5 py-0.5 rounded text-[11px] font-mono"
                    style={{ background: 'var(--color-down-bg)', color: 'var(--color-down)' }}>-3.14%</span>
                </div>
              </div>
            </div>
            <Toggle on={prefs.updownRed} onChange={() => setPrefs({ updownRed: !prefs.updownRed })} />
          </div>
        </div>
      </section>

      {/* 语言与内容(预留): 全站中文硬编码, i18n 后续版本接 */}
      <section>
        <h3 className="text-[14px] font-semibold text-text m-0 mb-2">语言与内容</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <LangCard title="简体中文" sub="中国大陆" on />
          <LangCard title="跟随系统" sub="使用浏览器语言" disabled />
          <LangCard title="繁體中文" sub="台湾及港澳" disabled />
          <LangCard title="English" sub="United States" disabled />
        </div>
      </section>
    </div>
  )
}

function ThemeMiniIcon({ kind }) {
  const icons = {
    light: <><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4" /></>,
    dark: <path d="M20 13.5A8 8 0 1110.5 4 6.5 6.5 0 0020 13.5z" />,
    system: <><rect x="4" y="5" width="16" height="11" rx="2" /><path d="M9 20h6" /></>,
  }
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" className="shrink-0 text-text-dim">
      {icons[kind]}
    </svg>
  )
}

function RadioDot({ on }) {
  return on ? (
    <span className="w-5 h-5 rounded-full bg-accent flex items-center justify-center shrink-0">
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
        <path d="M5 13l4 4L19 7" />
      </svg>
    </span>
  ) : (
    <span className="w-5 h-5 rounded-full border-2 border-border-strong shrink-0" />
  )
}

function Toggle({ on, onChange }) {
  return (
    <button role="switch" aria-checked={on} onClick={onChange}
      className={`shrink-0 w-11 h-6 rounded-full transition-colors cursor-pointer relative ${on ? 'bg-accent' : 'bg-surface-raise'}`}>
      <span className={`absolute left-0.5 top-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform
        ${on ? 'translate-x-5' : 'translate-x-0'}`} />
    </button>
  )
}

function LangCard({ title, sub, on = false, disabled = false }) {
  const clickable = !disabled
  return (
    <button onClick={() => { if (!clickable) return }}
      className={`text-left rounded-xl border p-3.5 flex items-center justify-between transition-colors
        ${on ? 'border-accent bg-accent/8' : 'border-border bg-surface/70'}
        ${disabled ? 'opacity-45 cursor-not-allowed' : clickable ? 'cursor-pointer hover:border-border-med' : 'cursor-default'}`}>
      <div>
        <div className={`text-[13.5px] font-medium ${on ? 'text-accent' : 'text-text'}`}>{title}</div>
        <div className="text-[11.5px] text-text-dim mt-0.5">{sub}{disabled && ' · 即将支持'}</div>
      </div>
      {disabled
        ? <span className="w-5 h-5 rounded-full border-2 border-border-strong shrink-0" />
        : <RadioDot on={on} />}
    </button>
  )
}

/* ══════════════════ 券商设置 ══════════════════ */

function BrokerTab() {
  return (
    <div className="space-y-4 fade-up">
      <div>
        <h2 className="text-[18px] font-semibold text-text-bright m-0">券商设置</h2>
        <p className="text-[12px] text-text-dim m-0 mt-1">券商费率档案, 供持仓/流水的手续费估算取用</p>
      </div>
      <BrokerSection />
    </div>
  )
}
