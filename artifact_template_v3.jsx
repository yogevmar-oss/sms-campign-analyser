import React, { useState, useMemo } from "react";
import {
  BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell,
  ScatterChart, Scatter, ReferenceLine, LabelList, ComposedChart,
} from "recharts";

// Data is injected at build time
const DATA = __DATA_PLACEHOLDER__;

// ─── Design tokens ──────────────────────────────────────────────────────────
const C = {
  ink: "#16161A",
  inkSoft: "#52525B",
  inkFaint: "#9A9994",
  rule: "#D9D5CC",
  paper: "#FAF8F4",
  flat: "#1F3A8A",
  upTo: "#A8A29E",
  stacked: "#047857",
  accent: "#B91C1C",
  emerald: "#047857",
};

const KIND_LABEL = { flat: "Flat", up_to: "Up to", stacked: "Stacked" };
const KIND_COLOR = { flat: C.flat, up_to: C.upTo, stacked: C.stacked };

const CONF_DOT = {
  high: { color: C.emerald, label: "High" },
  medium: { color: "#B45309", label: "Medium" },
  low: { color: C.inkFaint, label: "Low" },
  insufficient: { color: C.rule, label: "—" },
};

// ─── Utilities ──────────────────────────────────────────────────────────────
function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso + (iso.length === 10 ? "T00:00:00" : ""));
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

function fmtMonthLabel(ym) {
  const [y, m] = ym.split("-");
  return new Date(parseInt(y), parseInt(m) - 1, 1).toLocaleDateString("en-US", { month: "short", year: "2-digit" });
}

// UTC-anchored date helpers — used for chart axes so they never shift by timezone
const MONTHS_SHORT = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function parseDateUTC(s) {
  // s = "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS"
  const datePart = s.slice(0, 10);
  const [y, m, d] = datePart.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}
function formatMonthUTC(ts) {
  const d = new Date(ts);
  return `${MONTHS_SHORT[d.getUTCMonth()]} ${String(d.getUTCFullYear()).slice(-2)}`;
}
function generateMonthlyTicksUTC(minTs, maxTs) {
  const ticks = [];
  const startD = new Date(minTs);
  let y = startD.getUTCFullYear();
  let m = startD.getUTCMonth();
  while (true) {
    const t = Date.UTC(y, m, 1);
    if (t > maxTs) break;
    ticks.push(t);
    m++;
    if (m > 11) { m = 0; y++; }
  }
  return ticks;
}

// ─── Sale-moment bucketing (mirrors analyze.py:sale_moment_bucket) ──────────
//
// A campaign's "bucket" is a coarser shopper-relevant category than its event_tag.
// Multi-week buckets carry their base discount forward to fill gaps between
// explicit campaigns (Israeli sale moments behave as continuous markdown cycles,
// not as discrete SMS-bounded events).
const MULTI_WEEK_BUCKETS = new Set(["november_mega_sale", "winter_clearance"]);
const CARRY_OVER_MAX_DAYS = 14;

function saleMomentBucket(c) {
  const et = c.event_tag;
  const month = c.first_seen ? c.first_seen.slice(5, 7) : "";
  if (["black_friday", "cyber_monday", "crazy_november", "singles_day"].includes(et)) {
    return "november_mega_sale";
  }
  if (c.discount_kind === "stacked" && ["01", "02", "03"].includes(month)) {
    return "winter_clearance";
  }
  if (et === "mid_season") return "mid_season";
  if (et === "store_day") return "store_day";
  if (et === "white_sale") return "white_days";
  if (et === "passover") return "passover";
  if (et === "independence_day") return "independence_day";
  if (et === "valentines_day") return "valentines_day";
  if (et === "purim") return "purim";
  if (et === "flash_sale") return "flash_sale";
  if (c.scope === "outlet") return "outlet";
  if (c.gating === "vip_card" || c.gating === "new_member") return "vip_or_card";
  return "routine";
}

// ─── Sub-components ─────────────────────────────────────────────────────────

function ConfidenceBadge({ level, n }) {
  const meta = CONF_DOT[level] || CONF_DOT.insufficient;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs" style={{ color: C.inkSoft }}>
      <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: meta.color }} />
      <span style={{ fontFamily: "ui-monospace, 'JetBrains Mono', monospace" }}>{meta.label}</span>
      <span style={{ color: C.inkFaint }}>· n={n}</span>
    </span>
  );
}

function KindChip({ kind }) {
  return (
    <span
      className="inline-block px-1.5 py-0.5 text-[10px] tracking-wide uppercase"
      style={{
        fontFamily: "ui-monospace, 'JetBrains Mono', monospace",
        background: KIND_COLOR[kind] + "1A",
        color: KIND_COLOR[kind],
        border: `1px solid ${KIND_COLOR[kind]}33`,
      }}
    >
      {KIND_LABEL[kind] || kind}
    </span>
  );
}

function PctValue({ pct, kind, extra }) {
  if (pct == null) return <span style={{ color: C.inkFaint }}>—</span>;
  const color = KIND_COLOR[kind] || C.ink;
  return (
    <span style={{ color, fontWeight: 600 }}>
      {pct}%{extra != null ? <span style={{ color: C.inkSoft, fontWeight: 400 }}> + {extra}%</span> : null}
    </span>
  );
}

// ─── Verdict band — minimal single row, always visible ──────────────────────
function VerdictBand({ store, dateRange, campaignCount, shoppingSummary }) {
  const threshold = shoppingSummary?.min_threshold_pct;
  const pattern = shoppingSummary?.store_pattern;
  const peakMonths = shoppingSummary?.peak_months || [];
  const routineBaseline = shoppingSummary?.routine_baseline_pct;

  const pill = (() => {
    if (pattern === "always_on" && routineBaseline != null) {
      return (
        <div className="flex items-center gap-1.5 shrink-0 px-3 py-1 rounded"
          style={{ background: C.inkFaint + "18", border: `1px solid ${C.inkFaint}44` }}>
          <span className="text-[10px] tracking-wider uppercase"
            style={{ fontFamily: "ui-monospace, monospace", color: C.inkSoft }}>routinely</span>
          <span className="text-sm font-bold" style={{ fontFamily: "'Fraunces', serif", color: C.inkSoft }}>
            ~{routineBaseline}%
          </span>
        </div>
      );
    }
    if (pattern === "seasonal" && peakMonths.length > 0) {
      return (
        <div className="flex items-center gap-1.5 shrink-0 px-3 py-1 rounded"
          style={{ background: "#FEF3C7", border: "1px solid #FDE68A" }}>
          <span className="text-[10px] tracking-wider uppercase"
            style={{ fontFamily: "ui-monospace, monospace", color: "#92400E" }}>peaks in</span>
          <span className="text-sm font-bold" style={{ fontFamily: "'Fraunces', serif", color: "#92400E" }}>
            {peakMonths[0]}
          </span>
        </div>
      );
    }
    if (pattern === "sparse") {
      return (
        <div className="flex items-center gap-1.5 shrink-0 px-3 py-1 rounded"
          style={{ background: "#F4F4F5", border: "1px solid #D4D4D8" }}>
          <span className="text-[10px] tracking-wider uppercase"
            style={{ fontFamily: "ui-monospace, monospace", color: C.inkSoft }}>limited data</span>
        </div>
      );
    }
    if (threshold != null) {
      return (
        <div className="flex items-center gap-1.5 shrink-0 px-3 py-1 rounded"
          style={{ background: C.emerald + "12", border: `1px solid ${C.emerald}33` }}>
          <span className="text-[10px] tracking-wider uppercase"
            style={{ fontFamily: "ui-monospace, monospace", color: C.emerald }}>wait for</span>
          <span className="text-sm font-bold" style={{ fontFamily: "'Fraunces', serif", color: C.emerald }}>
            {threshold}%+
          </span>
        </div>
      );
    }
    return null;
  })();

  return (
    <div
      className="border-b sticky top-0 z-20 backdrop-blur"
      style={{ borderColor: C.rule, background: C.paper + "F0" }}
    >
      <div className="max-w-6xl mx-auto px-6 py-2.5 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2.5 min-w-0 flex-wrap">
          <span className="text-[11px] tracking-[0.18em] uppercase font-medium"
            style={{ fontFamily: "ui-monospace, monospace", color: C.ink }}>
            {store}
          </span>
          <span style={{ color: C.rule }}>·</span>
          <span className="text-[11px]" style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint }}>
            {fmtDate(dateRange.first)} – {fmtDate(dateRange.last)}
          </span>
          <span style={{ color: C.rule }}>·</span>
          <span className="text-[11px]" style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint }}>
            {campaignCount} campaigns
          </span>
        </div>
        {pill}
      </div>
    </div>
  );
}

// ─── Shopping Hero — the first thing you see ─────────────────────────────────
function ShoppingHero({ shoppingSummary, store }) {
  if (!shoppingSummary) return null;
  const {
    store_pattern,
    buy_windows,
    peak_months = [],
    n_discount_campaigns,
    always_on_sale,
    routine_baseline_pct,
    wait_delta_pct,
    deal_frequency_days,
    coupon_coverage_pct,
    coupon_lift_pct,
  } = shoppingSummary;

  const windowColor = (w, i) => {
    if (w.is_fallback) return "#B45309"; // amber for opportunistic
    return i === 0 ? C.accent : C.flat;
  };
  const windowBorderColor = (w, i) => {
    if (w.is_fallback) return "#B4530933";
    return i === 0 ? C.accent + "33" : C.flat + "33";
  };
  const windowTag = (w, i) => {
    if (w.is_fallback) return "Best opportunistic deal";
    return `Window ${i + 1}${w.months ? " · " + w.months : ""}`;
  };

  const kpis = [
    routine_baseline_pct != null && store_pattern !== "seasonal" && {
      label: "Routine baseline",
      value: `${routine_baseline_pct}%`,
      sub: "skip deals at or below this",
      color: C.inkSoft,
    },
    wait_delta_pct != null && wait_delta_pct > 0 && {
      label: "Patience payoff",
      value: `+${wait_delta_pct}pp`,
      sub: "extra vs a routine-day buy",
      color: C.emerald,
    },
    deal_frequency_days != null && {
      label: "Deal every",
      value: `~${deal_frequency_days}d`,
      sub: "median gap between promos",
      color: C.flat,
    },
    coupon_coverage_pct != null && {
      label: "Coupons",
      value: `${coupon_coverage_pct}%`,
      sub: coupon_lift_pct ? `of deals · +${coupon_lift_pct}% boost` : "of deals include a code",
      color: C.ink,
    },
  ].filter(Boolean);

  return (
    <section
      className="mx-4 mb-10 rounded-lg overflow-hidden"
      style={{ border: `1px solid ${C.rule}`, background: "#F0EDE8" }}
    >
      {/* Header row */}
      <div className="px-6 pt-5 pb-3 flex items-center gap-2">
        <span className="text-[10px] tracking-[0.2em] uppercase"
          style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint }}>
          Shopping intelligence
        </span>
        <span style={{ color: C.rule }}>·</span>
        <span className="text-[10px] tracking-[0.15em] uppercase"
          style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint }}>
          {store}
        </span>
      </div>

      {/* Pattern context banner */}
      {store_pattern === "always_on" && (
        <div className="mx-6 mb-5 p-5 rounded-md"
          style={{ background: "white", border: `1px solid ${C.rule}` }}>
          <div className="text-[9px] tracking-widest uppercase mb-2"
            style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint }}>
            Always-on store
          </div>
          <div style={{ fontFamily: "'Fraunces', serif", fontSize: 18, color: C.ink, lineHeight: 1.5 }}>
            Discounts run throughout the year
            {routine_baseline_pct != null && (
              <span style={{ fontWeight: 600 }}> (~{routine_baseline_pct}% floor)</span>
            )}.{" "}No timing advantage — shop when you need to.
          </div>
        </div>
      )}
      {store_pattern === "seasonal" && (
        <div className="mx-6 mb-4 px-5 py-3 rounded-md flex items-center gap-3"
          style={{ background: "#FFFBEB", border: "1px solid #FDE68A" }}>
          <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 10, color: "#92400E", lineHeight: 1.6 }}>
            <span style={{ fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase" }}>
              Seasonal store
            </span>
            {" · "}Discounts cluster in{" "}
            <span style={{ fontWeight: 700 }}>{peak_months.join(" & ")}</span>.
            {" "}Outside that window, no promos expected — plan accordingly.
          </div>
        </div>
      )}
      {store_pattern === "sparse" && (
        <div className="mx-6 mb-4 px-5 py-3 rounded-md"
          style={{ background: "#F4F4F5", border: "1px solid #D4D4D8" }}>
          <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 10, color: C.inkSoft, lineHeight: 1.6 }}>
            <span style={{ fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase" }}>
              Limited data
            </span>
            {" · "}Only {n_discount_campaigns} discount campaigns observed.
            {" "}Treat these recommendations as directional only.
          </div>
        </div>
      )}

      {/* Window cards (shown for all patterns except always_on) */}
      {store_pattern !== "always_on" && buy_windows.length > 0 && (
        <div className={`grid gap-4 px-6 pb-5 ${buy_windows.length === 1 ? "" : "md:grid-cols-2"}`}>
          {buy_windows.map((w, i) => {
            const col = windowColor(w, i);
            return (
              <div
                key={i}
                className="p-5 rounded-md"
                style={{
                  background: "white",
                  border: `1px solid ${windowBorderColor(w, i)}`,
                  borderTop: `3px solid ${col}`,
                }}
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[9px] tracking-widest uppercase"
                    style={{ fontFamily: "ui-monospace, monospace", color: col }}>
                    {windowTag(w, i)}
                  </div>
                  {w.n <= 3 && (
                    <div className="text-[9px] px-1.5 py-0.5 rounded"
                      style={{ fontFamily: "ui-monospace, monospace", background: "#FEF3C7", color: "#92400E" }}>
                      n={w.n} · low confidence
                    </div>
                  )}
                </div>

                {/* Window title */}
                <div className="mb-4" style={{ fontFamily: "'Fraunces', serif", fontSize: 18, fontWeight: 500, color: C.ink, lineHeight: 1.25 }}>
                  {w.label}
                </div>

                {/* Big number */}
                <div className="flex items-end gap-3 mb-4">
                  <span style={{ fontFamily: "'Fraunces', serif", fontSize: 72, fontWeight: 700, color: col, lineHeight: 1 }}>
                    {w.max_pct}
                  </span>
                  <div className="pb-3">
                    <div style={{ fontFamily: "ui-monospace, monospace", fontSize: 22, color: col, fontWeight: 600 }}>%</div>
                    <div className="text-[10px] tracking-wider uppercase" style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint }}>
                      max seen
                    </div>
                  </div>
                </div>

                {/* Sub-metrics */}
                <div className="flex gap-6 flex-wrap" style={{ borderTop: `1px solid ${C.rule}`, paddingTop: 14 }}>
                  <div>
                    <div className="text-[9px] uppercase tracking-wider mb-0.5"
                      style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint }}>
                      Typical
                    </div>
                    <div style={{ fontFamily: "'Fraunces', serif", fontSize: 22, color: C.inkSoft, fontWeight: 500 }}>
                      {w.median_effective_pct ?? w.median_pct ?? "—"}%
                    </div>
                  </div>
                  <div>
                    <div className="text-[9px] uppercase tracking-wider mb-0.5"
                      style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint }}>
                      Campaigns
                    </div>
                    <div style={{ fontFamily: "'Fraunces', serif", fontSize: 22, color: C.inkSoft, fontWeight: 500 }}>
                      {w.n}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* KPI strip */}
      {kpis.length > 0 && (
        <div className="grid gap-px mx-6 mb-6 overflow-hidden rounded-md"
          style={{
            gridTemplateColumns: `repeat(${kpis.length}, 1fr)`,
            border: `1px solid ${C.rule}`,
            background: C.rule,
          }}>
          {kpis.map((kpi, i) => (
            <div key={i} className="px-5 py-4" style={{ background: "white" }}>
              <div className="text-[9px] tracking-widest uppercase mb-1.5"
                style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint }}>
                {kpi.label}
              </div>
              <div style={{ fontFamily: "'Fraunces', serif", fontSize: 30, fontWeight: 700, color: kpi.color, lineHeight: 1 }}>
                {kpi.value}
              </div>
              <div className="text-[10px] mt-1.5 leading-snug"
                style={{ fontFamily: "ui-monospace, monospace", color: C.inkSoft }}>
                {kpi.sub}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// ─── Single Brand Card — replaces the long scrolling table ──────────────────
function BrandCard({ recommendations, campaigns, selectedBrand, onSelectBrand }) {
  const rec = useMemo(() => {
    if (!selectedBrand || ["all", "sitewide", "outlet"].includes(selectedBrand)) {
      return recommendations[0] || null;
    }
    return recommendations.find(r => r.brand === selectedBrand) || null;
  }, [recommendations, selectedBrand]);

  // Evidence: top 5 deepest explicit campaigns for this brand
  const evidence = useMemo(() => {
    if (!rec) return [];
    return campaigns
      .filter(c => c.has_discount && c.brands?.includes(rec.brand))
      .sort((a, b) => (b.effective_pct || 0) - (a.effective_pct || 0))
      .slice(0, 6);
  }, [campaigns, rec]);

  if (!recommendations || recommendations.length === 0) {
    return (
      <section className="mb-12 px-4">
        <h2 className="text-2xl mb-4" style={{ fontFamily: "'Fraunces', serif", color: C.ink, fontWeight: 500 }}>
          Brand verdict
        </h2>
        <div className="p-6" style={{ background: "white", border: `1px solid ${C.rule}`, borderRadius: 4 }}>
          <div style={{ fontFamily: "'Fraunces', serif", fontSize: 16, color: C.inkSoft, fontStyle: "italic" }}>
            This store's SMS don't name specific brands — all deals are store-wide.
            Use the timeline above to see when the deepest discounts run.
          </div>
        </div>
      </section>
    );
  }

  if (!rec) return null;

  const buyThr = rec.buy_threshold_pct;
  const [lo, hi] = rec.normal_range_pct || [null, null];
  const peakExp = rec.max_explicit;
  const peakSw = rec.max_via_sitewide;
  const confMeta = CONF_DOT[rec.confidence] || CONF_DOT.insufficient;

  return (
    <section className="mb-12 px-4">
      <div className="flex items-baseline justify-between gap-4 mb-4 flex-wrap">
        <h2
          className="text-2xl"
          style={{ fontFamily: "'Fraunces', serif", color: C.ink, fontWeight: 500 }}
        >
          Brand verdict
        </h2>
        <select
          value={rec.brand}
          onChange={(e) => onSelectBrand(e.target.value)}
          className="text-sm px-3 py-1.5"
          style={{
            fontFamily: "'Fraunces', serif",
            fontSize: 14,
            color: C.ink,
            background: "white",
            border: `1px solid ${C.ink}`,
            borderRadius: 2,
            outline: "none",
            minWidth: 200,
          }}
        >
          {recommendations.map(r => (
            <option key={r.brand} value={r.brand}>
              {r.brand} — {r.n_explicit} explicit deal{r.n_explicit === 1 ? "" : "s"}
            </option>
          ))}
        </select>
      </div>

      <div
        className="p-6"
        style={{
          background: "white",
          border: `1px solid ${C.rule}`,
          borderRadius: 4,
        }}
      >
        {/* Headline row: brand name + confidence */}
        <div className="flex items-baseline justify-between gap-4 mb-5 flex-wrap">
          <div>
            <h3
              style={{
                fontFamily: "'Fraunces', serif",
                fontSize: 32,
                color: C.ink,
                fontWeight: 500,
                lineHeight: 1.1,
              }}
            >
              {rec.brand}
            </h3>
            {rec.family !== rec.brand && (
              <div className="text-[11px] mt-1" style={{ color: C.inkFaint, fontFamily: "ui-monospace, monospace" }}>
                family: {rec.family}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2 text-xs" style={{ fontFamily: "ui-monospace, monospace" }}>
            <span className="inline-block w-2 h-2 rounded-full" style={{ background: confMeta.color }} />
            <span style={{ color: C.inkSoft }}>{confMeta.label} confidence</span>
            <span style={{ color: C.rule }}>·</span>
            <span style={{ color: C.inkFaint }}>n={rec.n_flat} flat deals</span>
          </div>
        </div>

        {/* Advice — large, italic */}
        <div
          className="mb-6 pb-5"
          style={{
            fontFamily: "'Fraunces', serif",
            fontSize: 18,
            fontStyle: "italic",
            color: C.ink,
            lineHeight: 1.5,
            borderBottom: `1px solid ${C.rule}`,
          }}
        >
          {rec.advice}
        </div>

        {/* Metrics grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-6">
          <div>
            <div className="text-[10px] tracking-wider uppercase mb-1" style={{ color: C.inkFaint, fontFamily: "ui-monospace, monospace" }}>
              Buy at
            </div>
            <div style={{ fontFamily: "'Fraunces', serif", fontSize: 24, color: C.flat, fontWeight: 600 }}>
              {buyThr != null ? `${buyThr}%` : "—"}
            </div>
          </div>
          <div>
            <div className="text-[10px] tracking-wider uppercase mb-1" style={{ color: C.inkFaint, fontFamily: "ui-monospace, monospace" }}>
              Normal range
            </div>
            <div style={{ fontFamily: "'Fraunces', serif", fontSize: 24, color: C.ink, fontWeight: 500 }}>
              {lo != null ? `${lo}–${hi}%` : "—"}
            </div>
          </div>
          <div>
            <div className="text-[10px] tracking-wider uppercase mb-1" style={{ color: C.inkFaint, fontFamily: "ui-monospace, monospace" }}>
              Promos seen
            </div>
            <div style={{ fontFamily: "'Fraunces', serif", fontSize: 24, color: C.ink, fontWeight: 500 }}>
              {rec.n_explicit}
              {rec.n_sitewide_eligible > 0 && (
                <span style={{ fontSize: 14, color: C.inkSoft, fontWeight: 400 }}>
                  {" "}+ {rec.n_sitewide_eligible} sw
                </span>
              )}
            </div>
          </div>
          <div>
            <div className="text-[10px] tracking-wider uppercase mb-1" style={{ color: C.inkFaint, fontFamily: "ui-monospace, monospace" }}>
              Best window
            </div>
            <div style={{ fontFamily: "'Fraunces', serif", fontSize: 14, color: C.ink, lineHeight: 1.3 }}>
              {rec.best_window || "—"}
            </div>
          </div>
        </div>

        {/* Peak deals split */}
        <div className="grid md:grid-cols-2 gap-6 mb-6">
          {peakExp && (
            <div className="pl-4" style={{ borderLeft: `2px solid ${KIND_COLOR[peakExp.kind]}` }}>
              <div className="text-[10px] tracking-wider uppercase mb-1" style={{ color: C.inkFaint, fontFamily: "ui-monospace, monospace" }}>
                Peak explicit · {rec.brand} was named
              </div>
              <div className="flex items-baseline gap-2">
                <span style={{ fontFamily: "'Fraunces', serif", fontSize: 22, color: KIND_COLOR[peakExp.kind], fontWeight: 600 }}>
                  {peakExp.effective_pct}%
                </span>
                <KindChip kind={peakExp.kind} />
                <span className="text-xs" style={{ color: C.inkSoft }}>{fmtDate(peakExp.date)}</span>
              </div>
            </div>
          )}
          {peakSw && (
            <div className="pl-4" style={{ borderLeft: `2px solid ${KIND_COLOR[peakSw.kind]}`, opacity: 0.85 }}>
              <div className="text-[10px] tracking-wider uppercase mb-1" style={{ color: C.inkFaint, fontFamily: "ui-monospace, monospace" }}>
                Peak via sitewide · applied to {rec.brand}
              </div>
              <div className="flex items-baseline gap-2">
                <span style={{ fontFamily: "'Fraunces', serif", fontSize: 22, color: KIND_COLOR[peakSw.kind], fontWeight: 600 }}>
                  {peakSw.effective_pct}%
                </span>
                <KindChip kind={peakSw.kind} />
                <span className="text-xs" style={{ color: C.inkSoft }}>{fmtDate(peakSw.date)}</span>
              </div>
            </div>
          )}
        </div>

        {/* Recent campaigns */}
        {evidence.length > 0 && (
          <div>
            <div className="text-[10px] tracking-wider uppercase mb-2" style={{ color: C.inkFaint, fontFamily: "ui-monospace, monospace" }}>
              Deepest explicit campaigns
            </div>
            <div className="space-y-1.5">
              {evidence.map((c) => (
                <div key={c.campaign_id} className="flex items-center gap-3 text-xs py-1">
                  <span style={{ color: C.inkFaint, fontFamily: "ui-monospace, monospace", width: 80 }}>
                    {fmtDate(c.first_seen)}
                  </span>
                  <PctValue pct={c.discount_pct} kind={c.discount_kind} extra={c.stacked_extra_pct} />
                  <KindChip kind={c.discount_kind} />
                  {c.event_tag && (
                    <span style={{ color: C.accent, fontSize: 10 }}>{c.event_tag.replace(/_/g, " ")}</span>
                  )}
                  {c.coupon_code && (
                    <span style={{ fontFamily: "ui-monospace, monospace", color: C.inkSoft, fontSize: 10 }}>
                      {c.coupon_code}
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

// ─── Sale-moment bar chart ──────────────────────────────────────────────────
function SaleMomentsChart({ moments }) {
  const data = useMemo(() => {
    return moments
      .filter(m => m.median_headline_pct != null)
      .map((m) => ({
        label: m.label,
        bucket: m.bucket,
        median: m.median_headline_pct,
        max: m.max_headline_pct,
        n: m.n,
      }));
  }, [moments]);

  const colorFor = (bucket) => {
    if (bucket === "november_mega_sale") return C.accent;
    if (bucket === "winter_clearance") return C.stacked;
    if (bucket === "routine") return C.inkFaint;
    if (bucket === "outlet") return C.upTo;
    return C.flat;
  };

  const subtitle = useMemo(() => {
    const peaks = moments
      .filter(m => !["routine", "outlet", "vip_or_card"].includes(m.bucket) && m.median_headline_pct != null)
      .slice(0, 2);
    if (peaks.length === 0) return "Median headline % per bucket across all campaigns.";
    const names = peaks.map(m => m.label.toLowerCase());
    return `Median headline % per bucket. ${names.length === 2
      ? `${names[0]} and ${names[1]} deliver`
      : `${names[0]} delivers`} the deepest discounts at this store.`;
  }, [moments]);

  return (
    <section className="mb-12 px-4">
      <h2
        className="text-2xl mb-1"
        style={{ fontFamily: "'Fraunces', serif", color: C.ink, fontWeight: 500 }}
      >
        Which sale moments actually deliver
      </h2>
      <p className="text-sm mb-4" style={{ color: C.inkSoft }}>{subtitle}</p>
      <div style={{ height: Math.max(220, data.length * 36) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 60, bottom: 4, left: 0 }}>
            <XAxis
              type="number"
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              tick={{ fill: C.inkFaint, fontSize: 10, fontFamily: "ui-monospace, monospace" }}
              tickFormatter={(v) => `${v}%`}
              axisLine={{ stroke: C.rule }}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="label"
              width={250}
              tick={{ fill: C.ink, fontSize: 11, fontFamily: "'Fraunces', serif" }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: "rgba(0,0,0,0.03)" }}
              contentStyle={{
                background: "white",
                border: `1px solid ${C.rule}`,
                borderRadius: 4,
                fontFamily: "ui-monospace, monospace",
                fontSize: 12,
              }}
              formatter={(value, name) => [`${value}%`, name === "median" ? "Median" : "Max"]}
            />
            <Bar dataKey="median" barSize={20} radius={[0, 2, 2, 0]}>
              {data.map((entry, i) => (
                <Cell key={i} fill={colorFor(entry.bucket)} />
              ))}
              <LabelList
                dataKey="max"
                position="right"
                content={({ x, y, width, height, value, index }) => {
                  const d = data[index];
                  return (
                    <text
                      x={x + width + 8}
                      y={y + height / 2 + 4}
                      fontSize={10}
                      fontFamily="ui-monospace, monospace"
                      fill={C.inkFaint}
                    >
                      n={d.n} · max {value}%
                    </text>
                  );
                }}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

// ─── Monthly chart ──────────────────────────────────────────────────────────
function MonthlyChart({ monthly }) {
  const data = monthly.map((m) => ({
    month: fmtMonthLabel(m.month),
    mean: m.mean_effective_pct,
    max: m.max_effective_pct,
    n: m.n_campaigns,
  }));

  return (
    <section className="mb-12 px-4">
      <h2
        className="text-2xl mb-1"
        style={{ fontFamily: "'Fraunces', serif", color: C.ink, fontWeight: 500 }}
      >
        By month
      </h2>
      <p className="text-sm mb-4" style={{ color: C.inkSoft }}>
        Mean and max effective discount per month — using compound math, so 70% + 20% stacked counts as 76%, not 90%.
      </p>
      <div style={{ height: 240 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
            <XAxis
              dataKey="month"
              tick={{ fill: C.inkSoft, fontSize: 10, fontFamily: "ui-monospace, monospace" }}
              axisLine={{ stroke: C.rule }}
              tickLine={false}
            />
            <YAxis
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              tick={{ fill: C.inkFaint, fontSize: 10, fontFamily: "ui-monospace, monospace" }}
              tickFormatter={(v) => `${v}%`}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: "rgba(0,0,0,0.03)" }}
              contentStyle={{
                background: "white",
                border: `1px solid ${C.rule}`,
                borderRadius: 4,
                fontFamily: "ui-monospace, monospace",
                fontSize: 12,
              }}
              formatter={(value, name, props) => [`${value}%`, name === "mean" ? "Mean" : "Max"]}
              labelFormatter={(label) => `${label} · n=${data.find(d => d.month === label)?.n || 0}`}
            />
            <Bar dataKey="max" fill={C.rule} barSize={28} />
            <Bar dataKey="mean" fill={C.ink} barSize={28} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

// ─── Evidence drawer (timeline + message list) ──────────────────────────────
function EvidenceDrawer({ messages, campaigns }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("campaigns");

  return (
    <section className="mb-12 px-4">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-baseline gap-3 w-full text-left"
        style={{
          fontFamily: "ui-monospace, monospace",
          color: C.inkSoft,
          paddingTop: 16,
          paddingBottom: 12,
          borderTop: `1px solid ${C.rule}`,
        }}
      >
        <span style={{ fontFamily: "'Fraunces', serif", fontSize: 18, color: C.ink, fontWeight: 500 }}>
          Evidence
        </span>
        <span className="text-xs">
          {open ? "− collapse" : `+ expand · ${campaigns.length} campaigns, ${messages.length} messages`}
        </span>
      </button>
      {open && (
        <div>
          <div className="flex gap-3 mb-3">
            {["campaigns", "messages"].map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className="text-xs px-2 py-1"
                style={{
                  fontFamily: "ui-monospace, monospace",
                  color: tab === t ? C.ink : C.inkFaint,
                  borderBottom: tab === t ? `1px solid ${C.ink}` : "1px solid transparent",
                }}
              >
                {t}
              </button>
            ))}
          </div>

          {tab === "campaigns" && (
            <div className="space-y-1 max-h-96 overflow-y-auto">
              {campaigns.filter(c => c.has_discount).slice().reverse().map((c) => (
                <div
                  key={c.campaign_id}
                  className="flex items-center gap-3 py-2 text-xs"
                  style={{ borderBottom: `1px solid ${C.rule}` }}
                >
                  <span style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint, width: 80 }}>
                    {fmtDate(c.first_seen)}
                  </span>
                  <PctValue pct={c.discount_pct} kind={c.discount_kind} extra={c.stacked_extra_pct} />
                  <KindChip kind={c.discount_kind} />
                  <span style={{ color: C.inkSoft, flex: 1 }} className="truncate">
                    {c.brands?.length ? c.brands.slice(0, 3).join(", ") : c.scope}
                    {c.brands_open_ended && " & more"}
                  </span>
                  {c.event_tag && <span style={{ color: C.accent, fontSize: 10 }}>{c.event_tag.replace(/_/g, " ")}</span>}
                  {c.n_messages > 1 && (
                    <span style={{ color: C.inkFaint, fontFamily: "ui-monospace, monospace", fontSize: 10 }}>
                      ×{c.n_messages}
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          {tab === "messages" && (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {messages.filter(m => m.has_discount).slice().reverse().slice(0, 50).map((m, i) => (
                <div key={i} className="py-2 text-xs" style={{ borderBottom: `1px solid ${C.rule}` }}>
                  <div className="flex items-center gap-3 mb-1">
                    <span style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint }}>
                      {fmtDate(m.sent_date)}
                    </span>
                    <PctValue pct={m.discount_pct} kind={m.discount_kind} extra={m.stacked_extra_pct} />
                    <KindChip kind={m.discount_kind} />
                    {m.event_tag && <span style={{ color: C.accent }}>{m.event_tag.replace(/_/g, " ")}</span>}
                  </div>
                  <div style={{ color: C.inkSoft, direction: "rtl", textAlign: "right", fontSize: 11, fontFamily: "system-ui, sans-serif" }}>
                    {m.raw_text.slice(0, 200)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ─── Discount Timeline (Keepa-style continuous chart) ──────────────────────
//
// For each day in the date range, computes the BEST discount that was active
// on that day for the selected filter. This shows the floor (no deal) and
// the peaks (when promos ran) as a continuous step area, like a price-history
// chart — not as scattered dots.
//
// A promo is "active" from sent_date to expires_at (or sent_date + median
// duration if no expiry is in the data).
//
// Filter semantics:
//   "all"      → every campaign EXCEPT outlet (outlet ceilings shadow the real deals)
//   "sitewide" → sitewide campaigns only
//   "outlet"   → outlet promos only (treated as its own category, not a brand boost)
//   <brand>    → explicit promos naming the brand + sitewide promos. Outlet is excluded.

const DEFAULT_PROMO_DURATION_DAYS = 3;

function DiscountTimeline({
  campaigns, brandVocab, brandRecs,
  selectedBrand, onSelectBrand,
  selectedCategory, onSelectCategory,
}) {
  // Filter modes are mutually exclusive. Order of precedence:
  //   category set → category mode
  //   else selectedBrand is "all"/"sitewide"/"outlet" → that mode
  //   else → brand mode
  const filterMode = selectedCategory
    ? "category"
    : (["all", "sitewide", "outlet"].includes(selectedBrand) ? selectedBrand : "brand");
  const filterTarget = filterMode === "category"
    ? selectedCategory
    : (filterMode === "brand" ? selectedBrand : null);
  const isBrandFilter = filterMode === "brand";
  const isCategoryFilter = filterMode === "category";
  const isSpecificFilter = isBrandFilter || isCategoryFilter;
  // Legacy alias used in some surface text
  const filter = filterTarget || filterMode;

  const brandOptions = useMemo(() => {
    return brandVocab.slice(0, 80);
  }, [brandVocab]);

  const categoryOptions = useMemo(() => {
    // Build category vocab from discount campaigns. Ranked by frequency.
    const counts = new Map();
    for (const c of campaigns) {
      // Include campaigns that have EITHER a % discount OR a free-shipping promo.
      // Pure-shipping campaigns contribute zero base/extra (no bar) but still produce
      // a shipping marker spanning the full validity window of the promo.
      if (!c.has_discount && !c.has_free_shipping) continue;
      for (const cat of (c.categories || [])) {
        counts.set(cat, (counts.get(cat) || 0) + 1);
      }
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([category, count]) => ({ category, count }));
  }, [campaigns]);

  // Build active-promo intervals per filter. Each campaign contributes BASE and/or EXTRA:
  //   - Stacked with extra (e.g. "70% + 20% extra"): base = discount_pct, extra = stacked_extra_pct
  //   - Stacked extra-only coupon (e.g. "15% extra"):  base = 0,            extra = discount_pct
  //   - Flat or up_to:                                 base = discount_pct, extra = 0
  // Daily render combines max-base from any active campaign with max-extra from any active campaign.
  // So Singles Day's 70% up_to + 15% extra coupon shows visually as 70 stacked with 15 on top.
  const intervals = useMemo(() => {
    const out = [];
    for (const c of campaigns) {
      if (!c.has_discount || c.is_marketing_only) continue;

      const isSitewide = c.scope === "sitewide";
      const isOutlet = c.scope === "outlet";

      let applies = false;
      if (filterMode === "all") {
        applies = !isOutlet;
      } else if (filterMode === "sitewide") {
        applies = isSitewide;
      } else if (filterMode === "outlet") {
        applies = isOutlet;
      } else if (filterMode === "brand") {
        // Brand: explicit naming OR sitewide. Outlet is excluded.
        applies = (c.brands?.includes(filterTarget) || isSitewide) && !isOutlet;
      } else if (filterMode === "category") {
        // Category: explicit category OR sitewide. Outlet is excluded.
        applies = (c.categories?.includes(filterTarget) || isSitewide) && !isOutlet;
      }
      if (!applies) continue;

      const startMs = parseDateUTC(c.first_seen);
      let endMs;
      if (c.expires_at) {
        const expMs = parseDateUTC(c.expires_at);
        endMs = expMs > startMs ? expMs : startMs + DEFAULT_PROMO_DURATION_DAYS * 86400000;
      } else {
        endMs = startMs + DEFAULT_PROMO_DURATION_DAYS * 86400000;
      }

      // Decompose campaign into base + extra contributions
      let basePct = 0, baseKind = c.discount_kind;
      let extraPct = 0;
      if (c.discount_kind === "stacked") {
        if (c.stacked_extra_pct != null && c.discount_pct != null) {
          // Type 1: "70% + 20% extra" — both parts known from this one campaign
          basePct = c.discount_pct;
          extraPct = c.stacked_extra_pct;
          baseKind = "up_to"; // stacked bases are typically up-to ceilings
        } else if (c.discount_pct != null) {
          // Type 2: "15% extra" coupon — extra only, no known base
          extraPct = c.discount_pct;
          basePct = 0;
        }
      } else {
        basePct = c.discount_pct ?? 0;
      }

      const fromSitewide = isSpecificFilter && isSitewide && (
        filterMode === "brand"
          ? !c.brands?.includes(filterTarget)
          : !c.categories?.includes(filterTarget)
      );

      out.push({
        startMs, endMs,
        basePct, baseKind,
        extraPct,
        headline: c.discount_pct,
        extra: c.stacked_extra_pct,
        effective: c.effective_pct,
        kind: c.discount_kind,
        scope: c.scope,
        brands: c.brands,
        categories: c.categories,
        event: c.event_tag,
        coupon: c.coupon_code,
        sourceLabel: fromSitewide ? "via sitewide" : "explicit",
        bucket: saleMomentBucket(c),
        firstSeen: c.first_seen,
        hasFreeShipping: !!c.has_free_shipping,
        freeShippingThresholdNis: c.free_shipping_threshold_nis,
      });
    }
    return out;
  }, [campaigns, filterMode, filterTarget, isSpecificFilter]);

  // Compute daily series: for each day, max base + max extra across active campaigns.
  // For days inside a multi-week sale-moment bucket where no explicit base is active,
  // carry forward the most recent base from the same bucket (capped at 14 days).
  // Carry-over applies to BASE only — coupons (extras) expire when their SMS says.
  const series = useMemo(() => {
    // Use ALL campaigns (drops + discounts) for the X-axis span so the chart
    // shows the full data history, not just months that had a discount.
    if (!campaigns.length) return { points: [], xDomain: [0, 1] };

    const minDate = campaigns.reduce((m, c) => Math.min(m, parseDateUTC(c.first_seen)), Infinity);
    const maxDate = campaigns.reduce((m, c) => Math.max(m, parseDateUTC(c.first_seen)), -Infinity);

    // Build per-bucket windows for carry-over eligibility. A day is "inside" a
    // multi-week bucket if it falls between the first campaign and the last
    // campaign's end + 14 days. Outside these windows, no carry-over.
    const bucketWindows = {};
    for (const iv of intervals) {
      if (!MULTI_WEEK_BUCKETS.has(iv.bucket)) continue;
      const w = bucketWindows[iv.bucket] || { start: Infinity, end: -Infinity };
      w.start = Math.min(w.start, iv.startMs);
      w.end = Math.max(w.end, iv.endMs + CARRY_OVER_MAX_DAYS * 86400000);
      bucketWindows[iv.bucket] = w;
    }
    function dayBucket(t) {
      for (const [bucket, w] of Object.entries(bucketWindows)) {
        if (t >= w.start && t <= w.end) return bucket;
      }
      return null;
    }

    const points = [];
    const oneDay = 86400000;
    for (let t = minDate; t <= maxDate; t += oneDay) {
      let bestBase = null;
      let bestExtra = null;

      // 1) Explicit active campaigns
      for (const iv of intervals) {
        if (iv.startMs <= t && t <= iv.endMs) {
          if (iv.basePct > 0 && (!bestBase || iv.basePct > bestBase.pct)) {
            bestBase = { pct: iv.basePct, kind: iv.baseKind, source: iv, isCarryOver: false };
          }
          if (iv.extraPct > 0 && (!bestExtra || iv.extraPct > bestExtra.pct)) {
            bestExtra = { pct: iv.extraPct, source: iv };
          }
        }
      }

      // 2) Carry-over base: only if no explicit base AND we're inside a multi-week bucket.
      //    Look backward up to CARRY_OVER_MAX_DAYS for the most recent base in the same bucket.
      if (!bestBase) {
        const bucket = dayBucket(t);
        if (bucket) {
          const cutoff = t - CARRY_OVER_MAX_DAYS * oneDay;
          let candidate = null;
          for (const iv of intervals) {
            if (iv.bucket !== bucket) continue;
            if (iv.basePct <= 0) continue;
            if (iv.endMs >= t) continue;        // not yet expired
            if (iv.endMs < cutoff) continue;     // expired too long ago
            if (!candidate || iv.basePct > candidate.basePct) candidate = iv;
          }
          if (candidate) {
            bestBase = {
              pct: candidate.basePct,
              kind: candidate.baseKind,
              source: candidate,
              isCarryOver: true,
              carriedFromMs: candidate.endMs,
            };
          }
        }
      }

      const base = bestBase ? bestBase.pct : 0;
      const extra = bestExtra ? bestExtra.pct : 0;
      const effective = base + extra > 0
        ? Math.round(100 * (1 - (1 - base / 100) * (1 - extra / 100)))
        : 0;

      // Free shipping: any active interval flagged. Take the lowest threshold seen
      // (most attractive). null threshold beats a high threshold (unconditional > "above ₪499").
      let shipping = null;
      // Did any active interval explicitly name the currently-filtered brand or category?
      // Used to visually distinguish brand/category-specific days from days that only had sitewide.
      let explicitOnThisDay = false;
      for (const iv of intervals) {
        if (iv.startMs <= t && t <= iv.endMs) {
          if (iv.hasFreeShipping) {
            if (!shipping) {
              shipping = { threshold: iv.freeShippingThresholdNis };
            } else {
              const cur = shipping.threshold;
              const cand = iv.freeShippingThresholdNis;
              if (cand == null && cur != null) shipping = { threshold: null };
              else if (cur != null && cand != null && cand < cur) shipping = { threshold: cand };
            }
          }
          if (isBrandFilter && iv.brands?.includes(filterTarget)) {
            explicitOnThisDay = true;
          } else if (isCategoryFilter && iv.categories?.includes(filterTarget)) {
            explicitOnThisDay = true;
          }
        }
      }

      points.push({
        x: t,
        base, extra,
        total: base + extra,
        effective,
        bestBase, bestExtra,
        sameSource: bestBase && bestExtra && bestBase.source === bestExtra.source,
        shipping,
        shippingY: shipping ? 108 : null,
        brandExplicit: explicitOnThisDay,
      });
    }
    return { points, xDomain: [minDate, maxDate] };
  }, [intervals, campaigns, filterMode, filterTarget, isBrandFilter, isCategoryFilter]);

  // Reference lines
  const baseline = useMemo(() => {
    if (filterMode === "all" || filterMode === "sitewide" || filterMode === "outlet") {
      const flats = campaigns
        .filter(c => {
          if (!c.has_discount || c.discount_kind !== "flat") return false;
          if (filterMode === "all") return c.scope !== "outlet";
          if (filterMode === "sitewide") return c.scope === "sitewide";
          return c.scope === "outlet";
        })
        .map(c => c.discount_pct);
      if (!flats.length) return null;
      flats.sort((a, b) => a - b);
      return flats[Math.floor(flats.length / 2)];
    }
    if (filterMode === "brand") {
      const br = brandRecs.find(r => r.brand === filterTarget);
      return br?.flat_p50_pct ?? null;
    }
    // category mode: median of flat discounts that mention this category
    const flats = campaigns
      .filter(c => c.has_discount && c.discount_kind === "flat" &&
                   c.categories?.includes(filterTarget))
      .map(c => c.discount_pct);
    if (!flats.length) return null;
    flats.sort((a, b) => a - b);
    return flats[Math.floor(flats.length / 2)];
  }, [filterMode, filterTarget, campaigns, brandRecs]);

  const buyThreshold = useMemo(() => {
    if (filterMode !== "brand") return null;
    const br = brandRecs.find(r => r.brand === filterTarget);
    return br?.buy_threshold_pct ?? null;
  }, [filterMode, filterTarget, brandRecs]);

  const eventMarkers = useMemo(() => {
    const seen = new Map();
    for (const c of campaigns) {
      if (c.has_discount && c.event_tag && !seen.has(c.event_tag)) {
        seen.set(c.event_tag, c.first_seen);
      }
    }
    return Array.from(seen.entries()).map(([tag, date]) => ({
      tag, x: parseDateUTC(date),
      label: tag.replace(/_/g, " "),
    }));
  }, [campaigns]);

  const stats = useMemo(() => {
    const activeDays = series.points.filter(p => p.total > 0).length;
    const totalDays = series.points.length;
    const peakPoint = series.points.reduce((m, p) => p.total > (m?.total || 0) ? p : m, null);
    const pctActive = totalDays ? Math.round((activeDays / totalDays) * 100) : 0;
    return { activeDays, totalDays, pctActive, peak: peakPoint };
  }, [series.points]);

  // Count explicit (non-sitewide) campaigns matching the active filter — used in the
  // summary line to honestly tell the user whether they're looking at brand/category-
  // specific data or just sitewide-fallback.
  const explicitCount = useMemo(() => {
    if (!isSpecificFilter) return 0;
    return campaigns.filter(c => {
      if (!c.has_discount || c.scope === "outlet") return false;
      return isBrandFilter
        ? c.brands?.includes(filterTarget)
        : c.categories?.includes(filterTarget);
    }).length;
  }, [campaigns, filterMode, filterTarget, isSpecificFilter, isBrandFilter]);

  const summary = (() => {
    const peakStr = stats.peak
      ? (stats.peak.extra > 0
          ? `${stats.peak.base}% + ${stats.peak.extra}% extra (${stats.peak.effective}% effective)`
          : `${stats.peak.total}%`)
      : "—";
    if (filterMode === "all") return `${stats.pctActive}% of days had a discount running (outlet excluded). Peak available: ${peakStr}.`;
    if (filterMode === "sitewide") return `Sitewide coverage: ${stats.pctActive}% of days had a store-wide deal. Peak: ${peakStr}.`;
    if (filterMode === "outlet") return `Outlet coverage: ${stats.pctActive}% of days had an outlet promo. Outlet ceilings (typically 80%) apply only to limited inventory.`;
    const noun = isCategoryFilter ? "category-specific" : "brand-specific";
    const ctx = isCategoryFilter ? "category" : "brand";
    if (explicitCount === 0) {
      return `${filterTarget} has zero ${noun} % off campaigns in this data. The chart shows sitewide deals that would apply to ${filterTarget}. Coverage: ${stats.pctActive}% of days. Peak available: ${peakStr}.`;
    }
    return `Showing ${explicitCount} explicit ${filterTarget} deal${explicitCount === 1 ? "" : "s"} plus all sitewide deals (which apply to ${filterTarget} too). Coverage: ${stats.pctActive}% of days. Peak available: ${peakStr}.`;
  })();

  return (
    <section className="mb-12 px-4">
      <div className="flex items-baseline justify-between gap-4 mb-1 flex-wrap">
        <h2
          className="text-2xl"
          style={{ fontFamily: "'Fraunces', serif", color: C.ink, fontWeight: 500 }}
        >
          Best available discount over time
          {isSpecificFilter && (
            <span style={{ color: C.accent, fontStyle: "italic", fontWeight: 400, fontSize: 22 }}>
              {" "}— {filterTarget}
              {isCategoryFilter && (
                <span style={{ color: C.inkSoft, fontStyle: "normal", fontSize: 14 }}> (category)</span>
              )}
            </span>
          )}
          {filterMode === "sitewide" && (
            <span style={{ color: C.inkSoft, fontWeight: 400, fontSize: 18 }}>
              {" "}· sitewide only
            </span>
          )}
          {filterMode === "outlet" && (
            <span style={{ color: C.inkSoft, fontWeight: 400, fontSize: 18 }}>
              {" "}· outlet only
            </span>
          )}
        </h2>
        <div className="flex items-center gap-2 flex-wrap">
          {[
            { key: "all", label: "All" },
            { key: "sitewide", label: "Sitewide" },
            { key: "outlet", label: "Outlet" },
          ].map(opt => (
            <button
              key={opt.key}
              onClick={() => onSelectBrand(opt.key)}
              className="text-xs px-2.5 py-1"
              style={{
                fontFamily: "ui-monospace, monospace",
                color: filterMode === opt.key ? C.ink : C.inkFaint,
                background: filterMode === opt.key ? "white" : "transparent",
                border: `1px solid ${filterMode === opt.key ? C.ink : C.rule}`,
                borderRadius: 2,
              }}
            >
              {opt.label}
            </button>
          ))}
          <select
            value={isBrandFilter ? filterTarget : ""}
            onChange={(e) => e.target.value && onSelectBrand(e.target.value)}
            className="text-xs px-2 py-1"
            style={{
              fontFamily: "ui-monospace, monospace",
              color: isBrandFilter ? C.ink : C.inkFaint,
              background: isBrandFilter ? "white" : "transparent",
              border: `1px solid ${isBrandFilter ? C.ink : C.rule}`,
              borderRadius: 2,
              outline: "none",
              minWidth: 160,
            }}
          >
            <option value="">— pick a brand —</option>
            {brandOptions.map(b => (
              <option key={b.brand} value={b.brand}>{b.brand} ({b.mentions})</option>
            ))}
          </select>
          <select
            value={isCategoryFilter ? filterTarget : ""}
            onChange={(e) => e.target.value && onSelectCategory(e.target.value)}
            className="text-xs px-2 py-1"
            style={{
              fontFamily: "ui-monospace, monospace",
              color: isCategoryFilter ? C.ink : C.inkFaint,
              background: isCategoryFilter ? "white" : "transparent",
              border: `1px solid ${isCategoryFilter ? C.ink : C.rule}`,
              borderRadius: 2,
              outline: "none",
              minWidth: 160,
            }}
          >
            <option value="">— pick a category —</option>
            {categoryOptions.map(c => (
              <option key={c.category} value={c.category}>{c.category} ({c.count})</option>
            ))}
          </select>
        </div>
      </div>
      <p className="text-sm mb-4" style={{ color: C.inkSoft }}>{summary}</p>

      <div style={{ height: 360 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={series.points}
            margin={{ top: 36, right: 12, left: 4, bottom: 8 }}
            barCategoryGap={0}
            barGap={0}
          >
            <XAxis
              type="number"
              dataKey="x"
              domain={series.xDomain}
              scale="time"
              tickFormatter={formatMonthUTC}
              tick={{ fill: C.inkSoft, fontSize: 10, fontFamily: "ui-monospace, monospace" }}
              axisLine={{ stroke: C.rule }}
              tickLine={false}
              interval={0}
              ticks={generateMonthlyTicksUTC(series.xDomain[0], series.xDomain[1])}
            />
            <YAxis
              type="number"
              domain={[0, 115]}
              ticks={[0, 25, 50, 75, 100]}
              tickFormatter={(v) => `${v}%`}
              tick={{ fill: C.inkFaint, fontSize: 10, fontFamily: "ui-monospace, monospace" }}
              axisLine={false}
              tickLine={false}
            />
            {eventMarkers.map((m) => (
              <ReferenceLine
                key={m.tag}
                x={m.x}
                stroke={C.accent}
                strokeDasharray="2 3"
                strokeOpacity={0.3}
                ifOverflow="visible"
                label={{
                  value: m.label,
                  angle: -90,
                  position: "insideTopRight",
                  fontSize: 8,
                  fontFamily: "ui-monospace, monospace",
                  fill: C.accent,
                  offset: 2,
                }}
              />
            ))}
            {baseline != null && (
              <ReferenceLine
                y={baseline}
                stroke={C.inkFaint}
                strokeDasharray="4 4"
                strokeOpacity={0.6}
                label={{
                  value: `baseline ${baseline}%`,
                  position: "insideTopLeft",
                  fontSize: 9,
                  fontFamily: "ui-monospace, monospace",
                  fill: C.inkSoft,
                }}
              />
            )}
            {buyThreshold != null && (
              <ReferenceLine
                y={buyThreshold}
                stroke={C.emerald}
                strokeDasharray="2 2"
                strokeOpacity={0.7}
                label={{
                  value: `buy at ${buyThreshold}%+`,
                  position: "insideTopLeft",
                  fontSize: 9,
                  fontFamily: "ui-monospace, monospace",
                  fill: C.emerald,
                  offset: 20,
                }}
              />
            )}
            <Tooltip
              cursor={{ fill: "rgba(0,0,0,0.04)" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const p = payload[0].payload;
                const d = new Date(p.x);
                const dateStr = `${MONTHS_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
                const bb = p.bestBase;
                const be = p.bestExtra;
                if (!bb && !be) {
                  return (
                    <div style={{ background: "white", padding: "8px 10px", border: `1px solid ${C.rule}`, borderRadius: 3, fontFamily: "ui-monospace, monospace", fontSize: 11, boxShadow: "0 2px 8px rgba(0,0,0,0.06)" }}>
                      <div style={{ color: C.inkSoft }}>{dateStr}</div>
                      <div style={{ color: C.inkFaint, fontStyle: "italic" }}>No active deal</div>
                    </div>
                  );
                }
                return (
                  <div
                    style={{
                      background: "white",
                      padding: "8px 10px",
                      border: `1px solid ${C.rule}`,
                      borderRadius: 3,
                      fontFamily: "ui-monospace, monospace",
                      fontSize: 11,
                      boxShadow: "0 2px 8px rgba(0,0,0,0.06)",
                      minWidth: 220,
                    }}
                  >
                    <div style={{ color: C.inkSoft, marginBottom: 6 }}>{dateStr}</div>
                    {bb && (
                      <div style={{ marginBottom: be ? 4 : 0 }}>
                        <div style={{ color: KIND_COLOR[bb.kind], fontWeight: 600, fontSize: 13 }}>
                          Base: {bb.pct}% {KIND_LABEL[bb.kind]}
                          {bb.isCarryOver && (
                            <span style={{ color: C.inkSoft, fontWeight: 400, fontSize: 10 }}> · carried</span>
                          )}
                        </div>
                        <div style={{ color: C.inkSoft, fontSize: 10 }}>
                          {bb.source.scope === "sitewide" ? "Sitewide"
                            : bb.source.scope === "outlet" ? "Outlet"
                            : (bb.source.brands?.slice(0, 3).join(", ") || bb.source.scope)}
                          {bb.source.event && <span style={{ color: C.accent }}> · {bb.source.event.replace(/_/g, " ")}</span>}
                        </div>
                        {bb.isCarryOver && (
                          <div style={{ color: C.inkFaint, fontSize: 9, fontStyle: "italic", marginTop: 2 }}>
                            carried from {(() => {
                              const d = new Date(bb.carriedFromMs);
                              return `${MONTHS_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}`;
                            })()} (same sale moment, likely still active)
                          </div>
                        )}
                      </div>
                    )}
                    {be && (
                      <div>
                        <div style={{ color: C.stacked, fontWeight: 600, fontSize: 13 }}>
                          + Extra: {be.pct}%{p.sameSource ? " (same deal)" : ""}
                        </div>
                        {!p.sameSource && (
                          <div style={{ color: C.inkSoft, fontSize: 10 }}>
                            {be.source.coupon ? `coupon ${be.source.coupon}` : "stacked coupon"}
                            {be.source.event && <span style={{ color: C.accent }}> · {be.source.event.replace(/_/g, " ")}</span>}
                          </div>
                        )}
                      </div>
                    )}
                    {p.extra > 0 && (
                      <div style={{ marginTop: 6, paddingTop: 6, borderTop: `1px solid ${C.rule}`, color: C.ink, fontSize: 11 }}>
                        Effective compound: <b>{p.effective}%</b>
                        <span style={{ color: C.inkFaint, fontSize: 10 }}> · visual stack: {p.total}%</span>
                      </div>
                    )}
                    {p.shipping && (
                      <div style={{ marginTop: 6, paddingTop: 6, borderTop: `1px solid ${C.rule}`, color: C.ink, fontSize: 11, fontWeight: 600 }}>
                        ✱ Free shipping active
                        {p.shipping.threshold != null && (
                          <span style={{ color: C.inkSoft, fontWeight: 400, fontSize: 10 }}> · above ₪{p.shipping.threshold}</span>
                        )}
                      </div>
                    )}
                  </div>
                );
              }}
            />
            {/* Base bar — colored by base campaign's kind; faded for carry-over.
                Days where the selected brand is explicitly named get an accent stroke
                so they stand out from the sea of sitewide-only days. */}
            <Bar dataKey="base" stackId="d" isAnimationActive={false}>
              {series.points.map((p, i) => (
                <Cell
                  key={`b-${i}`}
                  fill={p.bestBase ? KIND_COLOR[p.bestBase.kind] : C.rule}
                  fillOpacity={
                    !p.bestBase ? 0.12
                    : p.bestBase.isCarryOver ? 0.32
                    : 0.78
                  }
                  stroke={p.brandExplicit ? C.accent : "none"}
                  strokeWidth={p.brandExplicit ? 1.5 : 0}
                />
              ))}
            </Bar>
            {/* Extra (stacked) bar — always green, stacked on top */}
            <Bar dataKey="extra" stackId="d" isAnimationActive={false}>
              {series.points.map((p, i) => (
                <Cell
                  key={`e-${i}`}
                  fill={C.stacked}
                  fillOpacity={p.extra > 0 ? 0.85 : 0}
                />
              ))}
            </Bar>
            {/* Free-shipping markers — small dots above the bars on days when shipping is free */}
            <Scatter
              dataKey="shippingY"
              fill={C.ink}
              shape="circle"
              isAnimationActive={false}
              legendType="none"
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="flex items-center gap-4 mt-3 flex-wrap text-[10px]" style={{ fontFamily: "ui-monospace, monospace", color: C.inkSoft }}>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-2.5" style={{ background: C.flat, opacity: 0.78 }} /> Flat base
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-2.5" style={{ background: C.upTo, opacity: 0.78 }} /> Up-to / stacked base
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-2.5" style={{ background: C.upTo, opacity: 0.32 }} /> Carried over
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-2.5" style={{ background: C.stacked, opacity: 0.85 }} /> + Extra (stacked on top)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ background: C.ink }} /> Free shipping
        </span>
        {isSpecificFilter && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-2.5" style={{ background: "white", border: `1.5px solid ${C.accent}` }} /> {filterTarget}-explicit
          </span>
        )}
        {baseline != null && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3" style={{ borderTop: `1px dashed ${C.inkFaint}` }} /> baseline (median flat)
          </span>
        )}
        {buyThreshold != null && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3" style={{ borderTop: `1px dashed ${C.emerald}` }} /> buy threshold
          </span>
        )}
        <span style={{ color: C.rule }}>|</span>
        <span style={{ color: C.accent }}>dashed = events</span>
      </div>
    </section>
  );
}

// ─── Main App ───────────────────────────────────────────────────────────────
export default function App() {
  // Default to the most-mentioned brand so the brand card has content right away
  const defaultBrand = DATA.brand_recommendations[0]?.brand || "all";
  const [selectedBrand, setSelectedBrand] = useState(defaultBrand);
  const [selectedCategory, setSelectedCategory] = useState(null);

  // Selecting a brand (or All/Sitewide/Outlet) clears any active category filter.
  // Selecting a category leaves the brand state alone so the BrandCard keeps showing.
  const handleSelectBrand = (b) => {
    setSelectedBrand(b);
    setSelectedCategory(null);
  };
  const handleSelectCategory = (c) => {
    setSelectedCategory(c);
  };

  return (
    <div style={{ background: C.paper, minHeight: "100vh", color: C.ink }}>
      <VerdictBand
        store={DATA.store}
        dateRange={DATA.date_range}
        campaignCount={DATA.campaign_count}
        shoppingSummary={DATA.shopping_summary}
      />
      <main className="max-w-6xl mx-auto py-8">
        <ShoppingHero shoppingSummary={DATA.shopping_summary} store={DATA.store} />
        <DiscountTimeline
          campaigns={DATA.campaigns}
          brandVocab={DATA.brand_vocab}
          brandRecs={DATA.brand_recommendations}
          selectedBrand={selectedBrand}
          onSelectBrand={handleSelectBrand}
          selectedCategory={selectedCategory}
          onSelectCategory={handleSelectCategory}
        />
        <BrandCard
          recommendations={DATA.brand_recommendations}
          campaigns={DATA.campaigns}
          selectedBrand={selectedBrand}
          onSelectBrand={handleSelectBrand}
        />
        <SaleMomentsChart moments={DATA.sale_moments} />
        <MonthlyChart monthly={DATA.monthly} />
        <EvidenceDrawer
          messages={DATA.messages.filter(m => !m.is_duplicate)}
          campaigns={DATA.campaigns}
        />
      </main>
      <footer className="max-w-6xl mx-auto px-4 pb-12">
        <div
          className="pt-6 text-[10px] tracking-wider uppercase"
          style={{ fontFamily: "ui-monospace, monospace", color: C.inkFaint, borderTop: `1px solid ${C.rule}` }}
        >
          {DATA.message_count} messages indexed · {DATA.discount_message_count} with discount · {DATA.campaign_count} campaigns · {DATA.brand_vocab.length} brands
        </div>
      </footer>
    </div>
  );
}