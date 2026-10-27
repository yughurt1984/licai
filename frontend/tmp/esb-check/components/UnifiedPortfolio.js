import { jsx, jsxs } from "react/jsx-runtime";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fmtMoney, fmtPct, fundPassthroughType, loadBrokers, priceColor } from "../helpers";
import { fetchJSON } from "../hooks/useApi";
import SkeletonCard from "./Skeleton";
import Tooltip from "./Tooltip";
import { AllocationDonut, FxHint, MarketChip, ProxyPulse, RowActions, TodayPulse, TypeChip, TypeMiniInfo, WeightBar } from "./portfolio/atoms";
import { FAMILY_LABEL, TYPE_COLOR, TYPE_META, TYPE_ORDER } from "./portfolio/constants";
import { buildFamilyIndex, formatCurrencyMoney, riskFamiliesOf } from "./portfolio/helpers";
import { aggregate, normalizeAsset, normalizeHolding } from "./portfolio/model";
import { HoldingRow } from "./portfolio/HoldingRow";
import { PortfolioModals } from "./portfolio/PortfolioModals";
import { PendingDividends } from "./portfolio/PendingDividends";
import { ClosedPositionsBlock, SummaryStrip } from "./portfolio/summary";
import { usePortfolioModals } from "./portfolio/useModals";
export default function UnifiedPortfolio({ holdings, onEdit, onHistory, onAdd, dataVersion = 0 }) {
  const [assets, setAssets] = useState([]);
  const [brokers, setBrokers] = useState([]);
  useEffect(() => {
    loadBrokers().then(setBrokers);
  }, []);
  useEffect(() => {
    const onChange = () => {
      loadBrokers().then(setBrokers).catch(() => {
      });
    };
    window.addEventListener("licai:brokers-changed", onChange);
    return () => window.removeEventListener("licai:brokers-changed", onChange);
  }, []);
  const [assetsLoaded, setAssetsLoaded] = useState(false);
  const [tradingDay, setTradingDay] = useState(null);
  const [collapsed, setCollapsed] = useState({});
  const [hoverId, setHoverId] = useState(null);
  const [filter, setFilter] = useState("ALL");
  const [sortKey, setSortKey] = useState("mv");
  const { open, show, hide } = usePortfolioModals();
  const [thesisCodes, setThesisCodes] = useState(() => /* @__PURE__ */ new Set());
  const loadThesisCodes = useCallback(async () => {
    try {
      const list = await fetchJSON("/api/portfolio/thesis");
      setThesisCodes(new Set((list || []).map((t) => t.code)));
    } catch {
    }
  }, []);
  useEffect(() => {
    loadThesisCodes();
  }, [loadThesisCodes]);
  const [realized, setRealized] = useState({ stock: 0, asset: 0 });
  const [todayPnl, setTodayPnl] = useState(null);
  const [settling, setSettling] = useState(false);
  const [settleMsg, setSettleMsg] = useState("");
  const loadAssets = useCallback(async () => {
    try {
      const d = await fetchJSON("/api/assets");
      setAssets(d.assets || []);
    } catch {
    } finally {
      setAssetsLoaded(true);
    }
  }, []);
  const settlePending = useCallback(async () => {
    setSettling(true);
    setSettleMsg("");
    try {
      const d = await fetchJSON("/api/assets/settle-pending", { method: "POST" });
      const parts = [`\u786E\u8BA4 ${d.settled || 0} \u7B14`];
      if (d.skipped > 0) parts.push(`${d.skipped} \u7B14\u51C0\u503C\u672A\u51FA, \u7559\u7740\u4E0B\u6B21`);
      setSettleMsg(parts.join(" \xB7 "));
      await loadAssets();
    } catch (e) {
      setSettleMsg("\u5931\u8D25: " + e.message);
    } finally {
      setSettling(false);
    }
  }, [loadAssets]);
  const recomputeDca = useCallback(async () => {
    setSettling(true);
    setSettleMsg("");
    try {
      const d = await fetchJSON("/api/assets/recompute-dca", { method: "POST" });
      setSettleMsg(d.recomputed > 0 ? `\u91CD\u7B97 ${d.recomputed} \u7B14` : "\u6CA1\u6709\u9700\u8981\u91CD\u7B97\u7684\uFF08\u4EFD\u989D\u5DF2\u662F\u6700\u65B0\uFF09");
      await loadAssets();
    } catch (e) {
      setSettleMsg("\u5931\u8D25: " + e.message);
    } finally {
      setSettling(false);
    }
  }, [loadAssets]);
  const loadRealized = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([
        fetchJSON("/api/portfolio/realized"),
        fetchJSON("/api/assets/realized")
      ]);
      const stockCarry = s.total_realized_carry != null ? s.total_realized_carry : (s.items || []).filter((it) => !it.still_holding).reduce((sum, it) => sum + (it.realized_pnl || 0), 0);
      const assetExclCash = (a.items || []).filter((it) => it.asset_type !== "CASH").reduce((sum, it) => sum + (it.closed_realized ?? it.realized_pnl ?? 0), 0);
      setRealized({
        stock: Math.round(stockCarry * 100) / 100,
        asset: Math.round(assetExclCash * 100) / 100,
        stockItems: s.items || [],
        assetItems: a.items || []
      });
    } catch (e) {
      console.error("realized load failed", e);
    }
  }, []);
  const loadTodayPnl = useCallback(async () => {
    try {
      const d = await fetchJSON("/api/portfolio/today-pnl");
      setTodayPnl(d && typeof d.total === "number" ? d : null);
    } catch {
      setTodayPnl(null);
    }
  }, []);
  useEffect(() => {
    loadAssets();
    loadRealized();
    loadTodayPnl();
    const t = setInterval(() => {
      loadAssets();
      loadRealized();
      loadTodayPnl();
    }, 2e4);
    return () => clearInterval(t);
  }, [loadAssets, loadRealized, loadTodayPnl]);
  useEffect(() => {
    fetchJSON("/api/market/trading-day").then(setTradingDay).catch(() => {
    });
    const t = setInterval(() => fetchJSON("/api/market/trading-day").then(setTradingDay).catch(() => {
    }), 36e5);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (dataVersion > 0) {
      loadRealized();
      loadAssets();
      loadTodayPnl();
    }
  }, [dataVersion, loadRealized, loadAssets, loadTodayPnl]);
  const rows = useMemo(() => {
    const a = (holdings || []).filter((h) => (h.shares || 0) > 0).map(normalizeHolding);
    const e = (assets || []).filter((x) => x.asset_type === "BOT" || x.asset_type === "CASH" || (x.shares || 0) > 0 || (x.cost_amount || 0) > 0 || (x.current_value || 0) > 0 || (x.pending_amount || 0) > 0).map(normalizeAsset);
    return [...a, ...e];
  }, [holdings, assets]);
  const filtered = useMemo(
    () => filter === "ALL" ? rows : rows.filter((r) => r.type === filter),
    [rows, filter]
  );
  const aShareTradingDay = tradingDay ? !!tradingDay.is_trading_day : ![0, 6].includes((/* @__PURE__ */ new Date()).getDay());
  const agg = useMemo(() => aggregate(filtered, aShareTradingDay), [filtered, aShareTradingDay]);
  const carryByCode = useMemo(() => {
    const m = {};
    for (const it of realized?.stockItems || []) {
      if (it.realized_carry) m[it.stock_code] = it.realized_carry;
    }
    return m;
  }, [realized]);
  const tabTypes = TYPE_ORDER;
  const visibleTypes = TYPE_ORDER.filter((t) => agg.groups[t]);
  const [expo, setExpo] = useState(null);
  useEffect(() => {
    let alive = true;
    fetchJSON("/api/portfolio/exposure").then((d) => {
      if (alive && !d?.error) setExpo(d);
    }).catch(() => {
    });
    return () => {
      alive = false;
    };
  }, []);
  const famIdx = useMemo(() => buildFamilyIndex(expo), [expo]);
  const [risk, setRisk] = useState(null);
  useEffect(() => {
    let alive = true;
    fetchJSON("/api/portfolio/risk").then((d) => alive && setRisk(d?.available ? d : null)).catch(() => {
    });
    return () => {
      alive = false;
    };
  }, []);
  const insights = useMemo(() => {
    const allAgg = aggregate(rows, true);
    const total = allAgg.totalMv || 0;
    const warnings = [];
    const passthroughBuckets = { A: 0, F: 0, W: 0, M: 0, C: 0, R: 0 };
    for (const r of rows) {
      const v = r.mv || 0;
      if (r.type === "F") {
        const target = fundPassthroughType(r.name || "");
        passthroughBuckets[target] = (passthroughBuckets[target] || 0) + v;
      } else {
        passthroughBuckets[r.type] = (passthroughBuckets[r.type] || 0) + v;
      }
    }
    for (const t of TYPE_ORDER) {
      const mv = passthroughBuckets[t] || 0;
      if (mv === 0 || total === 0) continue;
      const pct = mv / total * 100;
      if (pct >= 50) {
        warnings.push({
          level: pct >= 70 ? "high" : "med",
          text: `${TYPE_META[t].label} \u5360\u6BD4 ${pct.toFixed(1)}%\uFF0C\u96C6\u4E2D\u5EA6\u8FC7\u9AD8`
        });
      }
    }
    for (const t of ["A", "F"]) {
      const g = allAgg.groups[t];
      const items = t === "A" ? (g?.items || []).filter((it) => (it.extra?.market || "A") === "A") : g?.items || [];
      if (items.length < 2) continue;
      const itemsMv = items.reduce((s, it) => s + (it.mv || 0), 0);
      if (itemsMv <= 0) continue;
      const subs = {};
      for (const it of items) {
        const cid = it.category?.id || "other";
        subs[cid] = (subs[cid] || 0) + (it.mv || 0);
      }
      const entries = Object.entries(subs);
      const scope = t === "A" && items.length < g.items.length ? "A\u80A1" : TYPE_META[t].label;
      if (entries.length === 1) {
        const cat = items[0].category?.label || "\u540C\u4E00\u7C7B";
        warnings.push({
          level: "high",
          text: `${scope}\u5168\u62BC\u300C${cat}\u300D\uFF0C\u677F\u5757\u672A\u5206\u6563`
        });
      } else {
        const top = entries.sort((a, b) => b[1] - a[1])[0];
        const topPct = top[1] / itemsMv * 100;
        if (topPct >= 70) {
          const cat = items.find((it) => (it.category?.id || "other") === top[0])?.category?.label || "\u67D0\u7C7B";
          warnings.push({
            level: "med",
            text: `${scope}\u5185\u300C${cat}\u300D\u5360 ${topPct.toFixed(0)}%\uFF0C\u677F\u5757\u8FC7\u5EA6\u96C6\u4E2D`
          });
        }
      }
    }
    const familyRows = {};
    const add = (fam, r) => {
      if (!familyRows[fam]) familyRows[fam] = { types: /* @__PURE__ */ new Set(), rows: [], mv: 0 };
      familyRows[fam].types.add(r.type);
      familyRows[fam].rows.push(r);
      familyRows[fam].mv += r.mv || 0;
    };
    for (const r of rows) {
      const fams = famIdx ? famIdx.byRow.get(`${r.type}:${r.code}`) || [] : riskFamiliesOf(r).map((f) => FAMILY_LABEL[f] || f);
      for (const f of fams) add(f, r);
    }
    const saidByBackend = new Set((expo?.warnings || []).filter((w) => w.kind === "industry" && w.industry).map((w) => w.industry));
    const overlapRowIds = /* @__PURE__ */ new Set();
    const overlapByRow = {};
    for (const [fam, info] of Object.entries(familyRows)) {
      if (info.rows.length < 2) continue;
      const famMv = famIdx?.fams.get(fam)?.mv ?? info.mv;
      const famPct = total > 0 ? famMv / total * 100 : 0;
      for (const r of info.rows) {
        overlapRowIds.add(r.id);
        if (!overlapByRow[r.id]) overlapByRow[r.id] = [];
        overlapByRow[r.id].push({ fam, pct: famPct, others: info.rows.filter((x) => x.id !== r.id).map((x) => x.name) });
      }
      if (info.types.size >= 2 && !saidByBackend.has(fam)) {
        warnings.push({
          level: "med",
          text: `\u300C${fam}\u300D\u6A2A\u8DE8 A\u80A1 + \u57FA\u91D1\uFF0C\u7A7F\u900F\u540E\u5408\u8BA1 ${famPct.toFixed(1)}% \u2014 ${info.rows.map((r) => r.name).slice(0, 4).join(" + ")} \u5176\u5B9E\u662F\u540C\u4E00\u5757\u98CE\u9669`
        });
      }
    }
    return { warnings, overlapRowIds, overlapByRow };
  }, [rows, famIdx, expo]);
  const removeAsset = async (row) => {
    if (!confirm(`\u5220\u9664 ${row.name}\uFF1F`)) return;
    await fetchJSON(`/api/assets/${row._raw.id}`, { method: "DELETE" });
    loadAssets();
  };
  const handleEdit = (row) => {
    if (row.type === "A") onEdit?.(row._raw);
    else show("edit", row._raw);
  };
  const handleAddLot = (row) => {
    if (row.type === "A") return;
    show("addLot", row._raw);
  };
  const handleReduceLot = (row) => {
    if (row.type === "A") return;
    show("reduce", row._raw);
  };
  const handleShowActions = (row) => {
    if (row.type === "A") return;
    show("actions", row._raw);
  };
  const editBotBudget = async (row) => {
    const cur = row.extra?.okxTotalBudgetUsdt;
    const next = prompt(
      `\u300C${row.name}\u300D\u9A6C\u4E01\u5B9E\u9645\u603B\u9884\u7B97 (USDT)
\u586B OKX \u5BA2\u6237\u7AEF\u7B56\u7565\u8BE6\u60C5\u770B\u5230\u7684"\u603B\u6295\u8D44\u989D", \u7559\u7A7A\u6062\u590D\u7B97\u6CD5\u4F30\u7B97`,
      cur ? String(cur) : ""
    );
    if (next === null) return;
    const v = next.trim() === "" ? null : parseFloat(next);
    if (next.trim() !== "" && (!v || v <= 0)) {
      alert("\u603B\u9884\u7B97\u5FC5\u987B\u4E3A\u6B63\u6570");
      return;
    }
    try {
      await fetchJSON(`/api/assets/${row.extra.assetIdRaw}`, {
        method: "PUT",
        body: JSON.stringify({ bot_budget_override_usdt: v })
      });
      await loadAssets();
    } catch (err) {
      alert("\u4FDD\u5B58\u5931\u8D25: " + (err?.message || ""));
    }
  };
  const rowActions = {
    onEditBotBudget: editBotBudget,
    onEdit: handleEdit,
    onHistory,
    onRemove: removeAsset,
    onAddLot: handleAddLot,
    onReduceLot: handleReduceLot,
    onShowActions: handleShowActions,
    onCashAdjust: (r) => show("cashAdjust", r._raw),
    onKline: (h) => show("kline", h),
    onThesis: (r) => show("thesis", r),
    onQuality: (r) => show("quality", r)
  };
  const isEmpty = rows.length === 0;
  if (isEmpty && !assetsLoaded) {
    return /* @__PURE__ */ jsx(SkeletonCard, { rows: 8, label: "\u6301\u4ED3\u52A0\u8F7D\u4E2D" });
  }
  return /* @__PURE__ */ jsxs(
    "section",
    {
      className: "rounded-xl border border-border bg-surface/60 overflow-hidden",
      style: { animation: "fade-up 0.4s ease-out" },
      children: [
        /* @__PURE__ */ jsxs(
          "div",
          {
            className: "px-3 md:px-6 py-3 md:py-5 border-b border-border flex flex-col md:flex-row md:flex-wrap md:justify-between md:items-center gap-4 md:gap-8",
            style: { background: "linear-gradient(180deg, var(--color-surface-2), var(--color-surface))" },
            children: [
              /* @__PURE__ */ jsxs("div", { className: "flex flex-col gap-3 w-full md:flex-1 md:w-auto min-w-0 md:min-w-[340px]", children: [
                /* @__PURE__ */ jsxs("div", { className: "flex items-baseline gap-3", children: [
                  /* @__PURE__ */ jsx("h2", { className: "text-[14px] font-semibold text-text-bright tracking-wide m-0", children: "\u6301\u4ED3\u603B\u89C8" }),
                  /* @__PURE__ */ jsx("span", { className: "text-[11px] text-text-dim", children: "\u80A1\u7968 \xB7 \u57FA\u91D1 \xB7 \u7406\u8D22 \xB7 \u73B0\u91D1 \xB7 \u52A0\u5BC6 \xB7 \u673A\u5668\u4EBA" })
                ] }),
                isEmpty ? /* @__PURE__ */ jsx("div", { className: "text-text-dim text-[12px] py-2", children: "\u8FD8\u6CA1\u6709\u6301\u4ED3,\u70B9\u51FB\u4E0B\u65B9\u300C+ \u6DFB\u52A0\u300D\u5F00\u59CB" }) : /* @__PURE__ */ jsx(SummaryStrip, { agg, aShareClosed: !aShareTradingDay, realized, todayPnl })
              ] }),
              !isEmpty && /* @__PURE__ */ jsx(AllocationDonut, { groups: agg.groups, totalMv: agg.totalMv })
            ]
          }
        ),
        /* @__PURE__ */ jsx(PendingDividends, { dataVersion }),
        (() => {
          const pendN = (assets || []).reduce((s, a) => s + (a.pending_actions_count || 0), 0);
          const hasRateFund = (assets || []).some((a) => a.asset_type === "FUND" && a.purchase_fee_rate != null);
          if (pendN === 0 && !hasRateFund && !settleMsg) return null;
          return /* @__PURE__ */ jsxs("div", { className: "px-3 md:px-6 py-2.5 border-b border-border bg-accent/5 flex items-center gap-3 flex-wrap", children: [
            /* @__PURE__ */ jsx("span", { className: "inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] shrink-0 bg-accent/15 text-accent border border-accent/50", children: "\u23F3" }),
            /* @__PURE__ */ jsxs("span", { className: "text-[11.5px] text-text", children: [
              pendN > 0 ? `${pendN} \u7B14\u5B9A\u6295\u5F85\u786E\u8BA4\u4EFD\u989D` : "\u5B9A\u6295",
              settleMsg && /* @__PURE__ */ jsxs("span", { className: "text-text-dim ml-2", children: [
                "\xB7 ",
                settleMsg
              ] })
            ] }),
            /* @__PURE__ */ jsxs("div", { className: "ml-auto flex items-center gap-2", children: [
              hasRateFund && /* @__PURE__ */ jsx(
                "button",
                {
                  onClick: recomputeDca,
                  disabled: settling,
                  title: "\u6539\u4E86\u7533\u8D2D\u8D39\u7387\u540E, \u6309\u5F53\u65E5\u51C0\u503C\u91CD\u7B97\u5DF2\u786E\u8BA4\u7684\u5B9A\u6295\u4EFD\u989D",
                  className: "px-3 py-1 rounded text-[11.5px] border border-border-med text-text-dim hover:text-text hover:border-accent transition-colors cursor-pointer disabled:opacity-50",
                  children: settling ? "\u5904\u7406\u4E2D\u2026" : "\u6309\u8D39\u7387\u91CD\u7B97"
                }
              ),
              pendN > 0 && /* @__PURE__ */ jsx(
                "button",
                {
                  onClick: settlePending,
                  disabled: settling,
                  className: "px-3 py-1 rounded text-[11.5px] border border-accent/50 bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer disabled:opacity-50",
                  children: settling ? "\u786E\u8BA4\u4E2D\u2026" : "\u4E00\u952E\u6309\u51C0\u503C\u786E\u8BA4"
                }
              )
            ] })
          ] });
        })(),
        !isEmpty && (risk || insights.warnings.length > 0 || (expo?.warnings || []).length > 0) && /* @__PURE__ */ jsxs("div", { className: "px-3 md:px-6 py-2.5 border-b border-border bg-surface-2/60 flex flex-col gap-1.5", children: [
          risk && /* @__PURE__ */ jsxs("div", { className: "flex items-baseline gap-2 text-[11.5px] flex-wrap", title: risk.note + (risk.excluded?.length ? ` \u672A\u7EB3\u5165: ${risk.excluded.map((e) => `${e.name} ${Math.round(e.mv)}\u5143`).join("\u3001")}\u3002` : ""), children: [
            /* @__PURE__ */ jsx(
              "span",
              {
                className: "inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] font-bold shrink-0",
                style: { background: "#85a0b41f", color: "#85a0b4", border: "1px solid #85a0b460" },
                children: "\u03C3"
              }
            ),
            /* @__PURE__ */ jsxs("span", { className: "text-text", children: [
              "\u7EC4\u5408\u6CE2\u52A8: \u4E0B\u4E00\u4E2A\u4EA4\u6613\u65E5 95% \u7684\u60C5\u51B5\u4E0B\u6700\u591A\u4E8F ",
              /* @__PURE__ */ jsxs("b", { className: "font-mono text-bull", children: [
                "\xA5",
                Math.round(risk.var1).toLocaleString()
              ] }),
              /* @__PURE__ */ jsxs("span", { className: "text-text-muted", children: [
                "(\u6CE2\u52A8\u8D44\u4EA7 \xA5",
                Math.round(risk.risky_value / 1e3),
                "k \u7684 ",
                (risk.var1 / risk.risky_value * 100).toFixed(1),
                "%)"
              ] }),
              " ",
              "\xB7 5 \u4E2A\u4EA4\u6613\u65E5 ",
              /* @__PURE__ */ jsxs("b", { className: "font-mono text-bull", children: [
                "\xA5",
                Math.round(risk.var5).toLocaleString()
              ] })
            ] }),
            /* @__PURE__ */ jsxs("span", { className: "text-text-dim", children: [
              "\u4E3B\u8981\u6765\u81EA ",
              risk.contrib.slice(0, 3).map((c) => `${c.name.replace(/\(.*$/, "").slice(0, 8)} ${Math.round(c.share * 100)}%`).join(" \xB7 ")
            ] }),
            risk.backtest?.rate != null && /* @__PURE__ */ jsxs("span", { className: "text-text-muted text-[10.5px]", children: [
              "\u56DE\u6D4B ",
              risk.backtest.days,
              " \u5929\u8D85\u51FA ",
              risk.backtest.breaches,
              " \u6B21(",
              (risk.backtest.rate * 100).toFixed(1),
              "%, \u5E94\u22485%)"
            ] })
          ] }),
          [...insights.warnings, ...(expo?.warnings || []).filter((w) => w.level !== "low")].map((w, i) => {
            const color = w.level === "high" ? "#e58a8a" : "#d4a05c";
            return /* @__PURE__ */ jsxs("div", { className: "flex items-center gap-2 text-[11.5px]", children: [
              /* @__PURE__ */ jsx(
                "span",
                {
                  className: "inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] font-bold shrink-0",
                  style: { background: `${color}1f`, color, border: `1px solid ${color}60` },
                  children: "!"
                }
              ),
              /* @__PURE__ */ jsx("span", { className: "text-text", children: w.text })
            ] }, i);
          }),
          expo?.coverage && /* @__PURE__ */ jsxs("div", { className: "text-[10px] text-text-muted leading-snug pl-6", children: [
            "\u7A7F\u900F\u53E3\u5F84: \u57FA\u91D1\u53EA\u62C6\u5230\u5B63\u62A5\u524D\u5341\u5927(\u5408\u8BA1\u7EA6\u5360\u51C0\u503C 25%~50%), \u6240\u4EE5\u4E0A\u9762\u7684\u655E\u53E3\u662F",
            /* @__PURE__ */ jsx("b", { children: "\u4E0B\u9650" }),
            "\uFF1B \u5DF2\u7A7F\u900F ",
            expo.coverage.penetrated_funds,
            "/",
            expo.n_funds,
            " \u53EA\u57FA\u91D1",
            (expo.coverage.uncovered || []).length > 0 && `\uFF0C\u62C9\u4E0D\u5230\u660E\u7EC6\u7684 ${expo.coverage.uncovered.length} \u53EA(${expo.coverage.uncovered.map((u) => u.name).join("\u3001")})\u672A\u8BA1\u5165\uFF0C\u90A3\u4E0D\u7B49\u4E8E\u655E\u53E3\u4E3A 0`
          ] })
        ] }),
        /* @__PURE__ */ jsxs(
          "div",
          {
            className: "px-3 md:px-6 py-2 border-b border-border flex justify-between items-center gap-3 flex-wrap",
            style: { background: "var(--color-surface-2)" },
            children: [
              /* @__PURE__ */ jsx("div", { className: "flex gap-1.5 flex-wrap", children: [["ALL", "\u5168\u90E8"], ...tabTypes.map((t) => [t, TYPE_META[t].label])].map(([k, l]) => {
                const active = filter === k;
                const c = k === "ALL" ? "#c8a876" : TYPE_COLOR[k];
                return /* @__PURE__ */ jsx(
                  "button",
                  {
                    onClick: () => setFilter(k),
                    className: "px-2.5 py-[3px] rounded-md text-[11px] border transition-colors cursor-pointer",
                    style: {
                      borderColor: active ? c : "var(--color-border-med)",
                      background: active ? `${c}1a` : "transparent",
                      color: active ? c : "var(--color-text-dim)"
                    },
                    children: l
                  },
                  k
                );
              }) }),
              /* @__PURE__ */ jsxs("div", { className: "flex gap-1.5", children: [
                /* @__PURE__ */ jsxs(
                  "button",
                  {
                    onClick: () => setSortKey(sortKey === "mv" ? "pnl" : "mv"),
                    className: "px-2.5 py-[3px] rounded-md text-[11px] border border-border-med bg-transparent text-text-dim hover:text-text transition-colors cursor-pointer",
                    children: [
                      "\u6392\u5E8F \xB7 ",
                      sortKey === "mv" ? "\u5E02\u503C" : "\u76C8\u4E8F"
                    ]
                  }
                ),
                /* @__PURE__ */ jsx(
                  "button",
                  {
                    onClick: () => show("add", "menu"),
                    className: "px-2.5 py-[3px] rounded-md text-[11px] border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 transition-colors cursor-pointer",
                    children: "+ \u6DFB\u52A0"
                  }
                )
              ] })
            ]
          }
        ),
        /* @__PURE__ */ jsx(
          PortfolioModals,
          {
            open,
            show,
            hide,
            brokers,
            onAdd,
            onAssetsChanged: loadAssets,
            onRealizedChanged: () => {
              loadAssets();
              loadRealized();
            },
            onThesisSaved: loadThesisCodes
          }
        ),
        !isEmpty && /* @__PURE__ */ jsxs("div", { className: "licai-row px-3 md:px-6 py-2 text-[10.5px] text-text-dim tracking-wider font-medium border-b border-border bg-surface", children: [
          /* @__PURE__ */ jsx("div", { className: "text-left", children: "\u540D\u79F0 \xB7 \u4EE3\u7801" }),
          /* @__PURE__ */ jsx("div", { className: "text-right", children: "\u5E02\u503C" }),
          /* @__PURE__ */ jsx("div", { className: "text-right licai-md-only", children: "\u6210\u672C" }),
          /* @__PURE__ */ jsx("div", { className: "text-right", children: "\u6D6E\u52A8\u76C8\u4E8F" }),
          /* @__PURE__ */ jsx("div", { className: "text-right", children: "\u4ECA\u65E5" }),
          /* @__PURE__ */ jsx("div", { className: "text-right licai-md-only", children: "\u5360\u6BD4" }),
          /* @__PURE__ */ jsx("div", { className: "text-left pl-2", children: "\u64CD\u4F5C" })
        ] }),
        visibleTypes.map((type) => {
          const g = agg.groups[type];
          const isCol = collapsed[type];
          const groupFxExposure = type === "A" ? agg.fxExposure || [] : [];
          const items = [...g.items].sort(
            (a, b) => sortKey === "pnl" ? (b.pnl || 0) - (a.pnl || 0) : (b.mv || 0) - (a.mv || 0)
          );
          return /* @__PURE__ */ jsxs("div", { children: [
            /* @__PURE__ */ jsxs(
              "div",
              {
                onClick: () => setCollapsed((c) => ({ ...c, [type]: !c[type] })),
                className: "licai-row px-3 md:px-6 py-2 border-b border-border cursor-pointer select-none items-center text-[11px] font-semibold text-text",
                style: { background: "var(--color-surface-2)" },
                children: [
                  /* @__PURE__ */ jsxs("div", { className: "flex items-center gap-2", children: [
                    /* @__PURE__ */ jsx(
                      "span",
                      {
                        className: "inline-block w-2 text-text-dim transition-transform",
                        style: { transform: isCol ? "rotate(-90deg)" : "rotate(0)" },
                        children: "\u25BE"
                      }
                    ),
                    /* @__PURE__ */ jsx("div", { className: "w-[3px] h-[13px] rounded-sm", style: { background: TYPE_COLOR[type] } }),
                    /* @__PURE__ */ jsx("span", { children: TYPE_META[type].label }),
                    /* @__PURE__ */ jsxs("span", { className: "text-text-dim font-normal text-[10.5px]", children: [
                      items.length,
                      " \u9879"
                    ] })
                  ] }),
                  /* @__PURE__ */ jsxs("div", { className: "text-right flex flex-col items-end", children: [
                    /* @__PURE__ */ jsxs("span", { className: "font-mono text-text", children: [
                      "\xA5",
                      fmtMoney(g.mv)
                    ] }),
                    groupFxExposure.length > 0 && /* @__PURE__ */ jsx("span", { className: "font-mono text-[9.5px] text-text-muted hidden md:inline", children: groupFxExposure.map((e) => formatCurrencyMoney(e.currency, e.originalMarketValue)).join(" / ") })
                  ] }),
                  /* @__PURE__ */ jsxs("div", { className: "text-right font-mono text-text-dim text-[10.5px] licai-md-only", children: [
                    "\xA5",
                    fmtMoney(g.cost)
                  ] }),
                  /* @__PURE__ */ jsxs("div", { className: `text-right font-mono ${priceColor(g.pnl)}`, children: [
                    g.pnl >= 0 ? "+" : "",
                    fmtMoney(g.pnl),
                    /* @__PURE__ */ jsxs("span", { className: "text-[10px] opacity-80 ml-1.5", children: [
                      "(",
                      fmtPct(g.pnlPct),
                      ")"
                    ] })
                  ] }),
                  /* @__PURE__ */ jsx("div", {}),
                  /* @__PURE__ */ jsxs("div", { className: "text-right font-mono text-text licai-md-only", children: [
                    (g.weight * 100).toFixed(1),
                    "%"
                  ] }),
                  /* @__PURE__ */ jsx("div", {})
                ]
              }
            ),
            !isCol && (() => {
              const renderRow = (row, isLast) => /* @__PURE__ */ jsx(
                HoldingRow,
                {
                  row,
                  isLast,
                  hovered: hoverId === row.id,
                  onHover: setHoverId,
                  totalMv: agg.totalMv,
                  insights,
                  carry: carryByCode[row.code],
                  hasThesis: thesisCodes.has(row.code),
                  actions: rowActions
                },
                row.id
              );
              if (type === "A" && items.length > 1) {
                const MK_LABEL = { A: "A\u80A1", HK: "\u6E2F\u80A1", US: "\u7F8E\u80A1" };
                const MK_ORDER = ["A", "HK", "US"];
                const clusters = {};
                for (const it of items) {
                  const mk = it.extra?.market || "A";
                  if (!clusters[mk]) clusters[mk] = { id: `mk-${mk}`, label: MK_LABEL[mk] || mk, items: [], mv: 0, cost: 0, pnl: 0 };
                  clusters[mk].items.push(it);
                  clusters[mk].mv += it.mv || 0;
                  clusters[mk].cost += it.cost || 0;
                  clusters[mk].pnl += it.pnl || 0;
                }
                const ordered = MK_ORDER.filter((mk) => clusters[mk]).map((mk) => clusters[mk]);
                if (ordered.length > 1) {
                  return ordered.map((cl, ci) => {
                    const isLastCluster = ci === ordered.length - 1;
                    return /* @__PURE__ */ jsxs(React.Fragment, { children: [
                      /* @__PURE__ */ jsxs(
                        "div",
                        {
                          className: "licai-row px-3 md:px-6 py-1.5 items-center text-[10.5px] text-text-dim border-b border-border-subtle",
                          style: { background: "var(--color-surface)" },
                          children: [
                            /* @__PURE__ */ jsxs("div", { className: "flex items-center gap-1.5 pl-4", children: [
                              /* @__PURE__ */ jsx("span", { className: "inline-block w-1 h-1 rounded-full", style: { background: TYPE_COLOR[type] } }),
                              /* @__PURE__ */ jsx("span", { className: "font-medium text-text", children: cl.label }),
                              /* @__PURE__ */ jsxs("span", { className: "opacity-70", children: [
                                cl.items.length,
                                " \u9879"
                              ] })
                            ] }),
                            /* @__PURE__ */ jsxs("div", { className: "text-right font-mono", children: [
                              "\xA5",
                              fmtMoney(cl.mv)
                            ] }),
                            /* @__PURE__ */ jsx("div", { className: "licai-md-only" }),
                            /* @__PURE__ */ jsxs("div", { className: `text-right font-mono ${priceColor(cl.pnl)}`, children: [
                              cl.pnl >= 0 ? "+" : "",
                              fmtMoney(cl.pnl)
                            ] }),
                            /* @__PURE__ */ jsx("div", {}),
                            /* @__PURE__ */ jsx("div", { className: "licai-md-only" }),
                            /* @__PURE__ */ jsx("div", {})
                          ]
                        }
                      ),
                      cl.items.map((row, ri) => renderRow(row, isLastCluster && ri === cl.items.length - 1))
                    ] }, cl.id);
                  });
                }
              }
              if (type === "F" && items.length > 1) {
                const clusters = {};
                for (const it of items) {
                  const cid = it.category?.id || "other";
                  if (!clusters[cid]) clusters[cid] = { id: cid, label: it.category?.label || "\u5176\u4ED6", items: [], mv: 0, cost: 0, pnl: 0 };
                  clusters[cid].items.push(it);
                  clusters[cid].mv += it.mv || 0;
                  clusters[cid].cost += it.cost || 0;
                  clusters[cid].pnl += it.pnl || 0;
                }
                const ordered = Object.values(clusters).sort((a, b) => b.mv - a.mv);
                if (ordered.length > 1) {
                  return ordered.map((cl, ci) => {
                    const isLastCluster = ci === ordered.length - 1;
                    return /* @__PURE__ */ jsxs(React.Fragment, { children: [
                      /* @__PURE__ */ jsxs(
                        "div",
                        {
                          className: "licai-row px-3 md:px-6 py-1.5 items-center text-[10.5px] text-text-dim border-b border-border-subtle",
                          style: { background: "var(--color-surface)" },
                          children: [
                            /* @__PURE__ */ jsxs("div", { className: "flex items-center gap-1.5 pl-4", children: [
                              /* @__PURE__ */ jsx("span", { className: "inline-block w-1 h-1 rounded-full", style: { background: TYPE_COLOR[type] } }),
                              /* @__PURE__ */ jsx("span", { className: "font-medium text-text", children: cl.label }),
                              /* @__PURE__ */ jsxs("span", { className: "opacity-70", children: [
                                cl.items.length,
                                " \u9879"
                              ] })
                            ] }),
                            /* @__PURE__ */ jsxs("div", { className: "text-right font-mono", children: [
                              "\xA5",
                              fmtMoney(cl.mv)
                            ] }),
                            /* @__PURE__ */ jsx("div", { className: "licai-md-only" }),
                            /* @__PURE__ */ jsxs("div", { className: `text-right font-mono ${priceColor(cl.pnl)}`, children: [
                              cl.pnl >= 0 ? "+" : "",
                              fmtMoney(cl.pnl)
                            ] }),
                            /* @__PURE__ */ jsx("div", {}),
                            /* @__PURE__ */ jsx("div", { className: "licai-md-only" }),
                            /* @__PURE__ */ jsx("div", {})
                          ]
                        }
                      ),
                      cl.items.map((row, ri) => renderRow(row, isLastCluster && ri === cl.items.length - 1))
                    ] }, cl.id);
                  });
                }
              }
              return items.map((row, ri) => renderRow(row, ri === items.length - 1));
            })()
          ] }, type);
        }),
        filtered.length === 0 && !isEmpty && /* @__PURE__ */ jsx("div", { className: "py-8 text-center text-text-dim text-[12px]", children: "\u5F53\u524D\u7B5B\u9009\u4E0B\u6CA1\u6709\u6301\u4ED3" }),
        /* @__PURE__ */ jsx(
          ClosedPositionsBlock,
          {
            items: [
              ...(realized?.stockItems || []).filter((i) => !i.still_holding),
              // 已清仓的理财/基金/加密 (排除 CASH/BOT): 同一只基金分多次买卖会记成多条(不同 asset_id),
              // 按 code 合并、已实现盈亏求和, 一只基金只显示一行(避免同名重复)。
              ...Object.values(
                (realized?.assetItems || []).filter((i) => !i.still_holding && i.asset_type !== "CASH" && i.asset_type !== "BOT" && (i.realized_pnl || 0) !== 0).reduce((acc, i) => {
                  const key = i.code || `#${i.asset_id}`;
                  if (!acc[key]) acc[key] = {
                    stock_code: key,
                    stock_name: i.name,
                    realized_pnl: 0,
                    _asset: true,
                    asset_id: i.asset_id,
                    code: i.code
                  };
                  acc[key].realized_pnl += i.realized_pnl || 0;
                  return acc;
                }, {})
              )
            ],
            onHistory,
            onKline: (h) => show("kline", h)
          }
        )
      ]
    }
  );
}
