const MARKETS = [
  { key: 'A', label: 'A股', short: 'A' },
  { key: 'HK', label: '港股', short: '港' },
  { key: 'US', label: '美股', short: '美' },
]

// marketStatus: {A, HK, US} → 'open'(交易中) / 'closed'(已收盘) / 'rest'(休息, 非交易日)。
// 后端按各市场自己的时区 + 交易日历判定(A股 chinese_calendar, 港美股 exchange_calendars),
// App 每 60s 轮询 /api/market/status 刷新。open 绿点脉冲, 休息/已收盘同灰系、靠文字区分。
export default function Header({ marketStatus, lastUpdate, onRefresh, refreshing, onSettings, user, onLogout }) {
  return (
    <header className="sticky top-0 z-50 border-b border-border bg-surface/80 backdrop-blur-xl">
      <div className="max-w-[1440px] mx-auto px-3 md:px-4 h-12 flex items-center justify-between">
        <div className="flex items-center gap-1.5 md:gap-2">
          <h1 className="text-[15px] font-semibold tracking-tight text-accent mr-1">
            理财助手
          </h1>
          {MARKETS.map(m => {
            const st = (marketStatus || {})[m.key] || 'closed'
            const open = st === 'open'
            const text = open ? '交易中' : st === 'rest' ? '休息' : '已收盘'
            return (
              <span key={m.key} title={`${m.label} ${text}`} className={`
                inline-flex items-center gap-1 px-1.5 sm:px-2 py-0.5 rounded-full text-[11px] font-medium tracking-wide shrink-0
                ${open
                  ? 'bg-bull/15 text-bull-bright border border-bull-border'
                  : 'bg-surface-3 text-text-dim border border-border'}
              `}>
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${open ? 'bg-bull animate-pulse' : 'bg-text-muted'}`} />
                <span className="hidden sm:inline">{m.label}</span>
                <span className="sm:hidden">{m.short}</span>
                <span className="hidden md:inline">{text}</span>
              </span>
            )
          })}
        </div>

        <div className="flex items-center gap-2 text-[12px] text-text-dim">
          {lastUpdate && (
            <span className="hidden sm:inline font-mono">
              {lastUpdate.toLocaleTimeString('zh-CN')}
            </span>
          )}
          <button onClick={onRefresh} disabled={refreshing}
            title="抓取最新行情写入数据库, 首页展示库里的快照"
            className="px-3 py-1 rounded-md border border-border text-text-dim hover:text-text hover:border-text-muted transition-colors cursor-pointer disabled:opacity-50">
            {refreshing ? '刷新中…' : '刷新'}
          </button>
          <button onClick={onSettings}
            className="px-3 py-1 rounded-md border border-border text-text-dim hover:text-text hover:border-text-muted transition-colors cursor-pointer">
            设置
          </button>
          {user && (
            <span className="hidden md:inline-flex items-center gap-2 pl-2 border-l border-border">
              <span className="text-text font-medium">{user.display_name || user.username}</span>
              <button onClick={onLogout}
                className="px-2 py-1 rounded-md text-text-dim hover:text-bear transition-colors cursor-pointer">
                登出
              </button>
            </span>
          )}
        </div>
      </div>
    </header>
  )
}
