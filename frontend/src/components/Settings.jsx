import { useEffect, useState } from 'react'
import { api, fetchJSON } from '../hooks/useApi'
import { AttributionSection } from './settings/AttributionSection'
import { ProviderSection } from './settings/ProviderSection'
import { LLMConfigSection } from './settings/LLMConfigSection'
import { OkxSpotSection } from './settings/OkxSpotSection'
import { PromptRulesSection } from './settings/PromptRulesSection'
import { UserMemorySection } from './settings/UserMemorySection'
import { ProxySection } from './settings/ProxySection'
import { ZsxqSection } from './settings/ZsxqSection'


export default function Settings({ onClose }) {
  const [url, setUrl] = useState('')
  const [status, setStatus] = useState({ text: '', ok: null })
  const [saving, setSaving] = useState(false)

  // OKX credentials
  const [okxStatus, setOkxStatus] = useState(null)
  const [okxApiKey, setOkxApiKey] = useState('')
  const [okxSecret, setOkxSecret] = useState('')
  const [okxPassphrase, setOkxPassphrase] = useState('')
  const [okxStatusText, setOkxStatusText] = useState({ text: '', ok: null })
  const [okxSaving, setOkxSaving] = useState(false)

  const loadOkxStatus = async () => {
    try { setOkxStatus(await fetchJSON('/api/assets/okx/status')) } catch {}
  }

  useEffect(() => {
    api.getFeishuConfig().then(d => {
      setUrl(d.webhook_url || '')
      if (d.enabled) setStatus({ text: '已启用', ok: true })
    })
    loadOkxStatus()
  }, [])

  const saveOkx = async () => {
    if (!okxApiKey || !okxSecret || !okxPassphrase) {
      return setOkxStatusText({ text: '三项都要填', ok: false })
    }
    setOkxSaving(true)
    setOkxStatusText({ text: '校验中...', ok: null })
    try {
      const r = await fetchJSON('/api/assets/okx/credentials', {
        method: 'POST',
        body: JSON.stringify({
          api_key: okxApiKey.trim(),
          secret_key: okxSecret.trim(),
          passphrase: okxPassphrase.trim(),
        }),
      })
      const detail = r.uid
        ? `UID ${r.uid} · ${r.bot_count} 个机器人`
        : `${r.bot_count} 个机器人` + (r.errors?.length ? `（注: ${r.errors.join('; ')}）` : '')
      setOkxStatusText({ text: `已保存 · ${detail}`, ok: true })
      setOkxApiKey(''); setOkxSecret(''); setOkxPassphrase('')
      await loadOkxStatus()
    } catch (e) {
      setOkxStatusText({ text: '保存失败：' + (e.message || e), ok: false })
    } finally {
      setOkxSaving(false)
    }
  }

  const clearOkx = async () => {
    if (!confirm('确定清除 OKX 凭证？已绑定的 BOT 资产将退回手动模式')) return
    try {
      await fetchJSON('/api/assets/okx/credentials', { method: 'DELETE' })
      setOkxStatusText({ text: '已清除', ok: true })
      await loadOkxStatus()
    } catch {}
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const res = await api.saveFeishuConfig(url)
      setStatus({ text: res.enabled ? '已保存并启用' : '已保存', ok: res.enabled })
    } catch {
      setStatus({ text: '保存失败', ok: false })
    }
    setSaving(false)
  }

  const handleTest = async () => {
    setStatus({ text: '发送中...', ok: null })
    try {
      const res = await api.testFeishu()
      setStatus({ text: res.message, ok: res.success })
    } catch {
      setStatus({ text: '发送失败', ok: false })
    }
  }

  return (
    <section className="rounded-xl border border-accent/20 bg-surface-2/80 overflow-hidden"
      style={{ animation: 'fade-up 0.3s ease-out' }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h2 className="text-[13px] font-medium text-accent tracking-wide">推送设置</h2>
        <button onClick={onClose}
          className="text-[12px] px-3 py-1 rounded-md border border-border text-text-dim hover:text-text transition-colors cursor-pointer">
          关闭
        </button>
      </div>
      <div className="p-4 space-y-3">
        <div>
          <label className="text-[12px] text-text-dim block mb-1">飞书 Webhook URL</label>
          <p className="text-[11px] text-text-muted mb-2">
            飞书群 → 设置 → 群机器人 → 添加机器人 → 自定义机器人 → 复制 Webhook 地址
          </p>
          <input
            className="w-full bg-bg border border-border rounded-lg px-3 py-2 text-[13px] text-text font-mono outline-none focus:border-accent transition-colors"
            placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"
            value={url} onChange={e => setUrl(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-3">
          <button onClick={handleSave} disabled={saving}
            className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
            {saving ? '保存中...' : '保存'}
          </button>
          <button onClick={handleTest}
            className="px-4 py-1.5 rounded-md border border-border text-text-dim text-[13px] hover:text-text transition-colors cursor-pointer">
            发送测试
          </button>
          {status.text && (
            <span className={`text-[12px] font-medium
              ${status.ok === true ? 'text-bull' : status.ok === false ? 'text-bear' : 'text-text-dim'}`}>
              {status.text}
            </span>
          )}
        </div>

        {/* 本地代理 (OKX/外发统一) */}
        <div className="mt-2 pt-4 border-t border-border">
          <ProxySection />
        </div>

        {/* OKX API 凭证 */}
        <div className="mt-2 pt-4 border-t border-border">
          <div className="flex items-center justify-between mb-2">
            <label className="text-[12px] text-text-dim font-semibold">OKX API 凭证</label>
            {okxStatus?.configured && (
              <span className="text-[11px] text-bull flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-bull"
                  style={{ boxShadow: '0 0 6px currentColor' }} />
                已连接
                {okxStatus.uid && <span className="text-text-muted ml-1">· UID {okxStatus.uid}</span>}
                {!okxStatus.uid && okxStatus.ok && <span className="text-text-muted ml-1">· 机器人接口可用</span>}
              </span>
            )}
          </div>
          <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
            用于自动同步网格/马丁格尔机器人的本金和盈亏。<span className="text-[var(--color-signal-moderate)]">
            只需勾选 <code className="bg-surface-3 px-1 rounded">Read</code> 权限</span>，
            禁用交易/提币 scope。凭证存入 macOS Keychain，不写数据库。
            <br />
            获取路径：OKX App → 账户 → API → 创建 API Key（IP 白名单填你的出口 IP，或留空）
          </p>

          {!okxStatus?.configured ? (
            <>
              <div className="grid grid-cols-1 gap-2 mb-2">
                <input type="password"
                  className="bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
                  placeholder="API Key" value={okxApiKey}
                  onChange={e => setOkxApiKey(e.target.value)} />
                <input type="password"
                  className="bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
                  placeholder="Secret Key" value={okxSecret}
                  onChange={e => setOkxSecret(e.target.value)} />
                <input type="password"
                  className="bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
                  placeholder="Passphrase (创建 Key 时你设的)" value={okxPassphrase}
                  onChange={e => setOkxPassphrase(e.target.value)} />
              </div>
              <div className="flex items-center gap-3">
                <button onClick={saveOkx} disabled={okxSaving}
                  className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[13px] hover:opacity-90 disabled:opacity-50">
                  {okxSaving ? '校验中...' : '保存并校验'}
                </button>
                {okxStatusText.text && (
                  <span className={`text-[12px] ${
                    okxStatusText.ok === true ? 'text-bull'
                    : okxStatusText.ok === false ? 'text-bear' : 'text-text-dim'
                  }`}>
                    {okxStatusText.text}
                  </span>
                )}
              </div>
            </>
          ) : (
            <button onClick={clearOkx}
              className="px-3 py-1 rounded border border-bear/40 text-bear hover:bg-bear/10 text-[12px]">
              清除凭证
            </button>
          )}
        </div>

        {/* LLM 配置 */}
        <div className="mt-2 pt-4 border-t border-border">
          <LLMConfigSection />
        </div>

        {/* 同花顺 fuyao 数据源(期货/期权合约与行情) */}
        <ThsKeySection />

        {/* TickFlow 行情源(A股/ETF/港/美股实时行情 + REITs) */}
        <TickFlowKeySection />

        {/* futu OpenAPI REST(港/美股行情快照) */}
        <FutuSection />

        {/* 知识星球(可选, 只读) */}
        <div className="mt-2 pt-4 border-t border-border">
          <ZsxqSection />
        </div>

        {/* 扩展数据源(可选): 深度龙虎榜席位身份 / 竞价异动 / 机构增仓等自带公开源没有的口径 */}
        <div className="mt-2 pt-4 border-t border-border">
          <ProviderSection />
        </div>

        {/* OKX 策略之外的现货 */}
        {okxStatus?.configured && (
          <div className="mt-2 pt-4 border-t border-border">
            <OkxSpotSection />
          </div>
        )}

        {/* 从纠正里沉淀出来的规则(待审队列) */}
        <div className="mt-2 pt-4 border-t border-border">
          <PromptRulesSection />
        </div>

        {/* AI 记住的关于用户的事实(画像), 待确认的在这里批 */}
        <div className="mt-2 pt-4 border-t border-border">
          <UserMemorySection />
        </div>

        {/* 开源许可 */}
        <div className="mt-2 pt-4 border-t border-border">
          <AttributionSection />
        </div>
      </div>
    </section>
  )
}

/* ── 同花顺 fuyao 数据源 Key ─────────────────────────────── */
function ThsKeySection() {
  const [saved, setSaved] = useState(null)      // {has_key, key_masked}
  const [key, setKey] = useState('')
  const [status, setStatus] = useState({ text: '', ok: null })
  const [saving, setSaving] = useState(false)

  const load = async () => {
    try { setSaved(await fetchJSON('/api/settings/ths')) } catch {}
  }
  useEffect(() => { load() }, [])

  const save = async () => {
    if (!key.trim()) return setStatus({ text: '请填 API key', ok: false })
    setSaving(true)
    try {
      await fetchJSON('/api/settings/ths', { method: 'POST', body: JSON.stringify({ api_key: key.trim() }) })
      setKey('')
      setStatus({ text: '已保存', ok: true })
      await load()
    } catch (e) {
      setStatus({ text: '保存失败：' + (e.message || e), ok: false })
    } finally { setSaving(false) }
  }

  return (
    <div className="mt-2 pt-4 border-t border-border">
      <div className="flex items-center justify-between mb-2">
        <label className="text-[12px] text-text-dim font-semibold">同花顺 fuyao 数据源 Key</label>
        {saved?.has_key && <span className="text-[11px] text-bull">已配置 · {saved.key_masked}</span>}
      </div>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        期货/期权合约目录与行情(fuyao.aicubes.cn, X-api-key 鉴权)。key 存全局配置, 所有用户共用。
      </p>
      <div className="flex items-center gap-2">
        <input type="password"
          className="flex-1 bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
          placeholder={saved?.has_key ? '留空则不修改' : '粘贴 fuyao API key'}
          value={key} onChange={e => setKey(e.target.value)} />
        <button onClick={save} disabled={saving}
          className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[12px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
          {saving ? '保存中...' : '保存'}
        </button>
        {status.text && (
          <span className={`text-[12px] ${status.ok === true ? 'text-bull' : status.ok === false ? 'text-bear' : 'text-text-dim'}`}>
            {status.text}
          </span>
        )}
      </div>
    </div>
  )
}

/* ── TickFlow 行情源 Key ─────────────────────────────────── */
function TickFlowKeySection() {
  const [saved, setSaved] = useState(null)      // {has_key, key_masked}
  const [key, setKey] = useState('')
  const [status, setStatus] = useState({ text: '', ok: null })
  const [saving, setSaving] = useState(false)

  const load = async () => {
    try { setSaved(await fetchJSON('/api/settings/tickflow')) } catch {}
  }
  useEffect(() => { load() }, [])

  const save = async () => {
    if (!key.trim()) return setStatus({ text: '请填 API key', ok: false })
    setSaving(true)
    try {
      await fetchJSON('/api/settings/tickflow', { method: 'POST', body: JSON.stringify({ api_key: key.trim() }) })
      setKey('')
      setStatus({ text: '已保存', ok: true })
      await load()
    } catch (e) {
      setStatus({ text: '保存失败：' + (e.message || e), ok: false })
    } finally { setSaving(false) }
  }

  return (
    <div className="mt-2 pt-4 border-t border-border">
      <div className="flex items-center justify-between mb-2">
        <label className="text-[12px] text-text-dim font-semibold">TickFlow 行情源 Key</label>
        {saved?.has_key && <span className="text-[11px] text-bull">已配置 · {saved.key_masked}</span>}
      </div>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        A股/ETF/港/美股实时行情(REITs 也走它; api.tickflow.org, x-api-key 鉴权)。主源失败时二段兜底, 未配置则跳过。
        在{' '}
        <a href="https://tickflow.org" target="_blank" rel="noreferrer" className="text-accent hover:underline">tickflow.org</a>{' '}
        控制台生成 key, 存全局配置, 所有用户共用。
      </p>
      <div className="flex items-center gap-2">
        <input type="password"
          className="flex-1 bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent"
          placeholder={saved?.has_key ? '留空则不修改' : '粘贴 TickFlow API key'}
          value={key} onChange={e => setKey(e.target.value)} />
        <button onClick={save} disabled={saving}
          className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[12px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
          {saving ? '保存中...' : '保存'}
        </button>
        {status.text && (
          <span className={`text-[12px] ${status.ok === true ? 'text-bull' : status.ok === false ? 'text-bear' : 'text-text-dim'}`}>
            {status.text}
          </span>
        )}
      </div>
    </div>
  )
}

/* ── futu OpenAPI REST(港/美股行情快照; https://open.futunn.com) ─────── */
function FutuSection() {
  const [cfg, setCfg] = useState(null)          // {api_host, app_key_masked, sign_algo, configured, last_error}
  const [apiHost, setApiHost] = useState('')
  const [appKey, setAppKey] = useState('')
  const [privateKey, setPrivateKey] = useState('')
  const [algo, setAlgo] = useState('ed25519')
  const [status, setStatus] = useState({ text: '', ok: null })
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  const load = async () => {
    try {
      const d = await fetchJSON('/api/settings/futu')
      setCfg(d)
      setAlgo(d.sign_algo || 'ed25519')
    } catch {}
  }
  useEffect(() => { load() }, [])

  const save = async () => {
    setSaving(true)
    try {
      const d = await fetchJSON('/api/settings/futu', {
        method: 'POST',
        body: JSON.stringify({
          api_host: apiHost.trim(),
          app_key: appKey.trim(),
          private_key: privateKey,
          sign_algo: algo,
        }),
      })
      setStatus({ text: d.configured ? '已保存(已配置)' : '已保存(凭证不完整)', ok: true })
      setAppKey(''); setPrivateKey('')
      await load()
    } catch (e) {
      setStatus({ text: '保存失败：' + (e.message || e), ok: false })
    } finally { setSaving(false) }
  }

  const test = async () => {
    setTesting(true)
    try {
      const d = await fetchJSON('/api/settings/futu/test', { method: 'POST' })
      if (d.valid === true) setStatus({ text: `接口可达${d.configured ? '' : '（凭证未配置）'}`, ok: true })
      else if (d.valid === false) setStatus({ text: '不可达：' + (d.note || ''), ok: false })
      else setStatus({ text: d.note || '响应异常', ok: null })
    } catch (e) {
      setStatus({ text: '测试失败：' + (e.message || e), ok: false })
    } finally { setTesting(false) }
  }

  const inp = 'bg-bg border border-border rounded px-3 py-1.5 text-[12px] text-text font-mono outline-none focus:border-accent'
  return (
    <div className="mt-2 pt-4 border-t border-border">
      <div className="flex items-center justify-between mb-2">
        <label className="text-[12px] text-text-dim font-semibold">futu OpenAPI（港/美股行情）</label>
        {cfg?.app_key_masked && (
          <span className="text-[11px] text-text-muted font-mono">AppKey {cfg.app_key_masked}</span>
        )}
      </div>
      <p className="text-[11px] text-text-muted mb-2 leading-relaxed">
        港/美股(含 ETF)行情快照走富途 OpenAPI REST。在{' '}
        <a href="https://open.futunn.com/dashboard" target="_blank" rel="noreferrer" className="text-accent hover:underline">open.futunn.com/dashboard</a>{' '}
        创建 AppKey 并上传公钥(Ed25519/RSA-SHA256), 私钥 PEM 粘到下面 —— 只存本地库, 不回显。
        未配置或接口失败时自动回退免费源。
      </p>
      {cfg?.last_error && (
        <p className="text-[11px] text-bear mb-2">最近错误：{cfg.last_error}</p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input
          className={`${inp} w-64`}
          placeholder={cfg?.api_host ? `API Host（当前 ${cfg.api_host}）` : 'API Host（默认 webapi.futunn.com）'}
          value={apiHost} onChange={e => setApiHost(e.target.value)} />
        <select className={`${inp} w-28`} value={algo} onChange={e => setAlgo(e.target.value)}>
          <option value="ed25519">Ed25519</option>
          <option value="rsa">RSA-SHA256</option>
        </select>
        <input
          className={`${inp} flex-1 min-w-[180px]`}
          placeholder={cfg?.app_key_masked ? `AppKey（当前 ${cfg.app_key_masked}）` : 'AppKey'}
          value={appKey} onChange={e => setAppKey(e.target.value)} />
      </div>
      <div className="flex flex-wrap items-center gap-2 mt-2">
        <textarea
          className={`${inp} flex-1 min-w-[260px] h-16`}
          placeholder={cfg?.configured ? '私钥 PEM（已存，粘贴新值覆盖；留空不变）' : '私钥 PEM 全文（-----BEGIN PRIVATE KEY-----…）'}
          value={privateKey} onChange={e => setPrivateKey(e.target.value)} />
      </div>
      <div className="flex items-center gap-2 mt-2">
        <button onClick={save} disabled={saving}
          className="px-4 py-1.5 rounded-md bg-accent text-bg font-medium text-[12px] hover:opacity-90 disabled:opacity-50 cursor-pointer">
          {saving ? '保存中...' : '保存'}
        </button>
        <button onClick={test} disabled={testing}
          className="px-3 py-1.5 rounded-md border border-border text-text-dim text-[12px] hover:text-text cursor-pointer">
          {testing ? '测试中...' : '测试连接'}
        </button>
        {status.text && (
          <span className={`text-[12px] ${status.ok === true ? 'text-bull' : status.ok === false ? 'text-bear' : 'text-text-dim'}`}>
            {status.text}
          </span>
        )}
      </div>
    </div>
  )
}
