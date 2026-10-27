import { jsx, jsxs } from "react/jsx-runtime";
export default function Header({ marketOpen, lastUpdate, onRefresh, refreshing, onSettings, user, onLogout }) {
  return /* @__PURE__ */ jsx("header", { className: "sticky top-0 z-50 border-b border-border bg-surface/80 backdrop-blur-xl", children: /* @__PURE__ */ jsxs("div", { className: "max-w-[1440px] mx-auto px-3 md:px-4 h-12 flex items-center justify-between", children: [
    /* @__PURE__ */ jsxs("div", { className: "flex items-center gap-2 md:gap-3", children: [
      /* @__PURE__ */ jsx("h1", { className: "text-[15px] font-semibold tracking-tight text-accent", children: "\u7406\u8D22\u52A9\u624B" }),
      /* @__PURE__ */ jsxs("span", { className: `
            inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium tracking-wide
            ${marketOpen ? "bg-bull/15 text-bull-bright border border-bull-border" : "bg-surface-3 text-text-dim border border-border"}
          `, children: [
        /* @__PURE__ */ jsx("span", { className: `w-1.5 h-1.5 rounded-full ${marketOpen ? "bg-bull animate-pulse" : "bg-text-muted"}` }),
        marketOpen ? "\u4EA4\u6613\u4E2D" : "\u5DF2\u6536\u76D8"
      ] })
    ] }),
    /* @__PURE__ */ jsxs("div", { className: "flex items-center gap-2 text-[12px] text-text-dim", children: [
      lastUpdate && /* @__PURE__ */ jsx("span", { className: "hidden sm:inline font-mono", children: lastUpdate.toLocaleTimeString("zh-CN") }),
      /* @__PURE__ */ jsx(
        "button",
        {
          onClick: onRefresh,
          disabled: refreshing,
          title: "\u6293\u53D6\u6700\u65B0\u884C\u60C5\u5199\u5165\u6570\u636E\u5E93, \u9996\u9875\u5C55\u793A\u5E93\u91CC\u7684\u5FEB\u7167",
          className: "px-3 py-1 rounded-md border border-border text-text-dim hover:text-text hover:border-text-muted transition-colors cursor-pointer disabled:opacity-50",
          children: refreshing ? "\u5237\u65B0\u4E2D\u2026" : "\u5237\u65B0"
        }
      ),
      /* @__PURE__ */ jsx(
        "button",
        {
          onClick: onSettings,
          className: "px-3 py-1 rounded-md border border-border text-text-dim hover:text-text hover:border-text-muted transition-colors cursor-pointer",
          children: "\u8BBE\u7F6E"
        }
      ),
      user && /* @__PURE__ */ jsxs("span", { className: "hidden md:inline-flex items-center gap-2 pl-2 border-l border-border", children: [
        /* @__PURE__ */ jsx("span", { className: "text-text font-medium", children: user.display_name || user.username }),
        /* @__PURE__ */ jsx(
          "button",
          {
            onClick: onLogout,
            className: "px-2 py-1 rounded-md text-text-dim hover:text-bear transition-colors cursor-pointer",
            children: "\u767B\u51FA"
          }
        )
      ] })
    ] })
  ] }) });
}
