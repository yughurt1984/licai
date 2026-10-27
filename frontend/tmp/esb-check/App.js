import { jsx, jsxs } from "react/jsx-runtime";
import { useState, useEffect, useCallback, useRef } from "react";
import { useWebSocket } from "./hooks/useWebSocket";
import { api, fetchJSON, AUTH_EVENT } from "./hooks/useApi";
import AuthPage from "./components/AuthPage";
import Header from "./components/Header";
import Sidebar, { NAV } from "./components/Sidebar";
import Dashboard from "./components/Dashboard";
import RiskBanner from "./components/RiskBanner";
import StaleBundleNotice from "./components/StaleBundleNotice";
import UnifiedPortfolio from "./components/UnifiedPortfolio";
import Rankings from "./components/Rankings";
import StockAsk from "./components/StockAsk";
import { GlobalStockModal } from "./components/ask/stockOpener";
import TradePlans, { PlanToast, PLAN_EVENT } from "./components/TradePlans";
import Settings from "./components/Settings";
import ProfilePage from "./components/ProfilePage";
import EditModal from "./components/EditModal";
import TransactionHistory from "./components/TransactionHistory";
import MorningBriefing from "./components/MorningBriefing";
import SentimentThermometer from "./components/SentimentThermometer";
import SectorShare from "./components/SectorShare";
import SectorMatrix from "./components/SectorMatrix";
import SectorOpportunities from "./components/SectorOpportunities";
import EtfXray from "./components/EtfXray";
import MarketPools from "./components/MarketPools";
import AnalyzerLists from "./components/AnalyzerLists";
import ProviderInstTheme from "./components/ProviderInstTheme";
import MacroDashboard from "./components/MacroDashboard";
import PortfolioNews from "./components/PortfolioNews";
import Cashflow from "./components/Cashflow";
import BenchmarkCompare from "./components/BenchmarkCompare";
import SectorRadar from "./components/SectorRadar";
import PortfolioCurve from "./components/PortfolioCurve";
import PortfolioCorrelation from "./components/PortfolioCorrelation";
import AllocationAdvisor from "./components/AllocationAdvisor";
import AShareSectorGap from "./components/AShareSectorGap";
import AITradeReview from "./components/AITradeReview";
import EntryReview from "./components/EntryReview";
const VIEWS = NAV.flatMap((s) => s.items.map((i) => i.key));
const normalizeView = (v) => VIEWS.includes(v) ? v : "portfolio";
function resolveHash() {
  const raw = (window.location.hash || "").slice(1);
  const h = raw.split("?")[0];
  if (h === "rankings" && new URLSearchParams(raw.split("?")[1] || "").get("t") === "pools") {
    try {
      window.location.hash = "pools";
    } catch {
    }
    return normalizeView("pools");
  }
  return normalizeView(h);
}
function CardShell({ title, sub, children }) {
  return /* @__PURE__ */ jsxs(
    "section",
    {
      className: "rounded-xl border border-border bg-surface/60 overflow-hidden",
      style: { animation: "fade-up 0.4s ease-out" },
      children: [
        /* @__PURE__ */ jsxs(
          "div",
          {
            className: "px-3 md:px-5 py-3 border-b border-border flex items-baseline gap-2 flex-wrap",
            style: { background: "linear-gradient(180deg, var(--color-surface-2), var(--color-surface))" },
            children: [
              /* @__PURE__ */ jsx("h3", { className: "text-[13px] font-semibold text-text-bright m-0", children: title }),
              /* @__PURE__ */ jsx("span", { className: "text-[11px] text-text-dim", children: sub })
            ]
          }
        ),
        /* @__PURE__ */ jsx("div", { className: "px-3 md:px-5 py-3 [&>div]:mb-0", children })
      ]
    }
  );
}
export default function App() {
  const [holdings, setHoldings] = useState([]);
  const [marketOpen, setMarketOpen] = useState(false);
  const [editTarget, setEditTarget] = useState(null);
  const [historyTarget, setHistoryTarget] = useState(null);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [user, setUser] = useState(void 0);
  const [view, _setView] = useState(resolveHash);
  const setView = (v) => {
    const next = normalizeView(v);
    _setView(next);
    try {
      window.location.hash = next;
    } catch {
    }
  };
  useEffect(() => {
    const onHashChange = () => _setView(resolveHash());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  useEffect(() => {
    let alive = true;
    fetchJSON("/api/auth/me").then((u) => {
      if (alive) setUser(u);
    }).catch(() => {
      if (alive) setUser(null);
    });
    const onAuthLost = () => setUser(null);
    window.addEventListener(AUTH_EVENT, onAuthLost);
    return () => {
      alive = false;
      window.removeEventListener(AUTH_EVENT, onAuthLost);
    };
  }, []);
  const handleLogout = async () => {
    try {
      await fetchJSON("/api/auth/logout", { method: "POST" });
    } catch {
    }
    setUser(null);
  };
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const pageKeys = Object.keys(PAGES);
    const noPage = VIEWS.filter((k) => !pageKeys.includes(k));
    const noNav = pageKeys.filter((k) => !VIEWS.includes(k));
    if (noPage.length) console.error(`App: \u8FD9\u4E9B NAV \u9879\u6CA1\u6709\u5BF9\u5E94\u9875\u9762: ${noPage.join(", ")}`);
    if (noNav.length) console.error(`App: \u8FD9\u4E9B\u9875\u9762\u70B9\u4E0D\u5230(\u4E0D\u5728 NAV \u91CC): ${noNav.join(", ")}`);
  }, []);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [dataVersion, setDataVersion] = useState(0);
  const quotesRef = useRef({});
  const loadPortfolio = useCallback(async () => {
    try {
      setHoldings(await api.getPortfolio());
    } catch {
    }
  }, []);
  useEffect(() => {
    loadPortfolio();
  }, [loadPortfolio]);
  const [refreshing, setRefreshing] = useState(false);
  const handleManualRefresh = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      await fetchJSON("/api/market/refresh", { method: "POST", body: JSON.stringify({}) });
      try {
        window.dispatchEvent(new CustomEvent("licai:data-mutated", { detail: "/api/market/refresh" }));
      } catch {
      }
    } catch {
    }
    await loadPortfolio();
    setDataVersion((v) => v + 1);
    setRefreshing(false);
  }, [refreshing, loadPortfolio]);
  useEffect(() => {
    if (user) {
      loadPortfolio();
      setDataVersion((v) => v + 1);
    }
  }, [user?.user_id]);
  const hasForeign = holdings.some((h) => /^(HK|US)\./i.test(String(h.stock_code || "")));
  useEffect(() => {
    if (!hasForeign) return;
    const t = setInterval(loadPortfolio, 10 * 60 * 1e3);
    return () => clearInterval(t);
  }, [hasForeign, loadPortfolio]);
  const handleWsMessage = useCallback((msg) => {
    if (msg.type === "plan_alert") {
      window.dispatchEvent(new CustomEvent(PLAN_EVENT, { detail: msg }));
      return;
    }
    if (msg.type === "price_update") {
      quotesRef.current = msg.data;
      setMarketOpen(msg.market_open || false);
      setLastUpdate(/* @__PURE__ */ new Date());
      setHoldings((prev) => prev.map((h) => {
        const q = msg.data[h.stock_code];
        if (!q) return h;
        const currentPrice = q.price;
        const fxRate = q.fx_rate || h.fx_rate || 1;
        const originalCostValue = h.cost_price * h.shares;
        const originalMarketValue = currentPrice * h.shares;
        const pnl = (originalMarketValue - originalCostValue) * fxRate;
        const pnlPct = h.cost_price > 0 ? (currentPrice - h.cost_price) / h.cost_price * 100 : 0;
        return {
          ...h,
          current_price: currentPrice,
          fx_rate: fxRate,
          fx_time: q.fx_time || h.fx_time || "",
          fx_source: q.fx_source || h.fx_source || "",
          price_change_pct: q.change_pct,
          today_valid: q.today_valid !== void 0 ? q.today_valid : h.today_valid,
          unrealized_pnl: Math.round(pnl * 100) / 100,
          pnl_pct: Math.round(pnlPct * 100) / 100,
          original_cost_value: Math.round(originalCostValue * 100) / 100,
          original_market_value: Math.round(originalMarketValue * 100) / 100,
          cost_value: Math.round(originalCostValue * fxRate * 100) / 100,
          market_value: Math.round(originalMarketValue * fxRate * 100) / 100
        };
      }));
    }
  }, []);
  useWebSocket(handleWsMessage, user !== null);
  const handleHoldingChange = () => {
    loadPortfolio();
    setDataVersion((v) => v + 1);
  };
  const PAD = "max-w-[1440px] mx-auto px-2 md:px-4 py-3 md:py-4";
  const PAGES = {
    portfolio: () => /* @__PURE__ */ jsx("div", { className: `${PAD} space-y-3 md:space-y-4`, children: /* @__PURE__ */ jsx(
      UnifiedPortfolio,
      {
        holdings,
        onEdit: setEditTarget,
        onHistory: setHistoryTarget,
        onAdd: handleHoldingChange,
        dataVersion
      }
    ) }),
    cashflow: () => /* @__PURE__ */ jsx("div", { className: `${PAD} space-y-3 md:space-y-4`, children: /* @__PURE__ */ jsx(Cashflow, {}) }),
    performance: () => /* @__PURE__ */ jsxs("div", { className: `${PAD} space-y-3 md:space-y-4`, children: [
      /* @__PURE__ */ jsx(BenchmarkCompare, {}),
      /* @__PURE__ */ jsx(SectorRadar, {}),
      /* @__PURE__ */ jsx(CardShell, { title: "\u76C8\u4E8F\u66F2\u7EBF", sub: "\u65F6\u95F4\u52A0\u6743 \xB7 \u5BF9\u7167\u6CAA\u6DF1300", children: /* @__PURE__ */ jsx(PortfolioCurve, {}) }),
      /* @__PURE__ */ jsx(CardShell, { title: "\u540C\u6E90\u98CE\u9669", sub: "\u5728\u6301\u6807\u7684\u4E24\u4E24\u76F8\u5173\u6027 \xB7 \u5206\u6563\u662F\u5426\u540D\u4E49", children: /* @__PURE__ */ jsx(PortfolioCorrelation, {}) })
    ] }),
    allocation: () => /* @__PURE__ */ jsxs("div", { className: `${PAD} space-y-3 md:space-y-4`, children: [
      /* @__PURE__ */ jsx(AllocationAdvisor, {}),
      /* @__PURE__ */ jsx(AShareSectorGap, {}),
      /* @__PURE__ */ jsx(EtfXray, { mode: "mine" })
    ] }),
    review: () => /* @__PURE__ */ jsxs("div", { className: `${PAD} space-y-3 md:space-y-4`, children: [
      /* @__PURE__ */ jsx(TradePlans, {}),
      /* @__PURE__ */ jsx(AITradeReview, {}),
      /* @__PURE__ */ jsx(EntryReview, {})
    ] }),
    open: () => /* @__PURE__ */ jsxs("div", { className: `${PAD} space-y-3 md:space-y-4`, children: [
      /* @__PURE__ */ jsx(MorningBriefing, {}),
      /* @__PURE__ */ jsx(SentimentThermometer, {})
    ] }),
    sector: () => /* @__PURE__ */ jsxs("div", { className: `${PAD} space-y-3 md:space-y-4`, children: [
      /* @__PURE__ */ jsx(SectorShare, {}),
      /* @__PURE__ */ jsx(SectorMatrix, {}),
      /* @__PURE__ */ jsx(SectorOpportunities, {}),
      /* @__PURE__ */ jsx(EtfXray, { mode: "theme" })
    ] }),
    // 榜单是"占满一屏"的视图: 外壳撑满滚动区, 面板自己吃掉剩余高度。
    // 原来面板写死 h-[calc(100vh-11rem)] —— 那 11rem 是照着某个窗口估的顶栏高度,
    // 窗口一变高、顶栏一换行就对不上, 底下留出一条谁也用不上的空带。
    rankings: () => /* @__PURE__ */ jsx("div", { className: `${PAD} h-full flex flex-col`, children: /* @__PURE__ */ jsx(Rankings, {}) }),
    // 大盘·股池: 三列卡 + 连板梯队 + 历史分池回测表, 是"昨日涨停今天怎么样"的复盘面板,
    // 信息形态跟榜单那种左列表+右K线天生冲突 —— 所以单独上页, 满宽铺开。
    // MarketPools 的 onPick 可选, 不传就用它自带的 K 线弹窗。
    pools: () => /* @__PURE__ */ jsxs("div", { className: `${PAD} space-y-3 md:space-y-4`, children: [
      /* @__PURE__ */ jsx(MarketPools, {}),
      /* @__PURE__ */ jsx(AnalyzerLists, {})
    ] }),
    capital: () => /* @__PURE__ */ jsx("div", { className: `${PAD} space-y-3 md:space-y-4`, children: /* @__PURE__ */ jsx(ProviderInstTheme, { standalone: true }) }),
    macro: () => /* @__PURE__ */ jsx("div", { className: `${PAD} space-y-3 md:space-y-4`, children: /* @__PURE__ */ jsx(MacroDashboard, {}) }),
    news: () => /* @__PURE__ */ jsx("div", { className: `${PAD} space-y-3 md:space-y-4`, children: /* @__PURE__ */ jsx(PortfolioNews, {}) }),
    ask: () => /* @__PURE__ */ jsx("div", { className: `${PAD} max-w-[900px] h-[calc(100vh-8rem)]`, children: /* @__PURE__ */ jsx(StockAsk, { page: true }) }),
    settings: () => /* @__PURE__ */ jsx("div", { className: `${PAD} max-w-[900px]`, children: /* @__PURE__ */ jsx(Settings, { onClose: () => setView("portfolio") }) }),
    profile: () => /* @__PURE__ */ jsx("div", { className: `${PAD} max-w-[980px]`, children: /* @__PURE__ */ jsx(ProfilePage, {}) })
  };
  if (user === void 0) {
    return /* @__PURE__ */ jsx("div", { className: "min-h-screen bg-bg" });
  }
  if (user === null) {
    return /* @__PURE__ */ jsx(AuthPage, { onAuthed: setUser });
  }
  return /* @__PURE__ */ jsxs("div", { className: "h-screen flex flex-col overflow-hidden", children: [
    /* @__PURE__ */ jsx(
      Header,
      {
        marketOpen,
        lastUpdate,
        onRefresh: handleManualRefresh,
        refreshing,
        onSettings: () => setView("settings"),
        user,
        onLogout: handleLogout
      }
    ),
    /* @__PURE__ */ jsxs("div", { className: "flex flex-1 min-h-0", children: [
      /* @__PURE__ */ jsx(Sidebar, { active: view, onNav: setView, open: sidebarOpen, onToggle: () => setSidebarOpen((o) => !o) }),
      /* @__PURE__ */ jsxs("main", { className: "flex-1 min-w-0 flex flex-col min-h-0", children: [
        /* @__PURE__ */ jsxs("div", { className: "shrink-0", children: [
          /* @__PURE__ */ jsx(Dashboard, { holdings }),
          /* @__PURE__ */ jsx(RiskBanner, { holdings })
        ] }),
        /* @__PURE__ */ jsx("div", { className: "flex-1 min-h-0 overflow-y-auto", children: PAGES[view] ? PAGES[view]() : /* @__PURE__ */ jsxs("div", { className: `${PAD} text-[13px] text-text-dim`, children: [
          "\u8FD9\u4E00\u9875\u8FD8\u6CA1\u63A5\u4E0A\u5185\u5BB9(view=",
          /* @__PURE__ */ jsx("code", { className: "text-text", children: view }),
          ")\u3002"
        ] }) })
      ] })
    ] }),
    editTarget && /* @__PURE__ */ jsx(EditModal, { holding: editTarget, onClose: () => setEditTarget(null), onChange: handleHoldingChange }),
    historyTarget && /* @__PURE__ */ jsx(
      TransactionHistory,
      {
        stockCode: historyTarget.stock_code,
        stockName: historyTarget.stock_name,
        onClose: () => setHistoryTarget(null),
        onChange: handleHoldingChange
      }
    ),
    /* @__PURE__ */ jsx(StaleBundleNotice, {}),
    /* @__PURE__ */ jsx(GlobalStockModal, {}),
    /* @__PURE__ */ jsx(PlanToast, {})
  ] });
}
