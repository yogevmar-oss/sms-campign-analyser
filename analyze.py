"""
Analytics layer for SMS discount data.

Takes the per-message records produced by extract.py and computes the
derived structures the artifact needs to deliver a verdict:

  - campaigns:           dedupe reminder SMS into one campaign per coupon/body+window
  - brand_recommendations: per-brand buy threshold, normal range, best window, confidence
  - sale_moments:        event-lift buckets (median/max/n per sale moment)
  - monthly:             month-by-month aggregates

The key fix from prior iterations: a sitewide flat 30% applies to every brand
in the vocab, not zero brands. The brand-stats computation here includes
sitewide promos in each brand's history (tagged separately as `via_sitewide`).
"""

from __future__ import annotations

import hashlib
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from brands import brand_family, normalize_brand  # noqa: E402


# ---------------------------------------------------------------------------
# Campaign grouping — collapse reminder SMS into single campaigns
# ---------------------------------------------------------------------------

def assign_campaign_ids(records: list[dict]) -> None:
    """
    Mutate records in-place to add 'campaign_id'. Two messages share a campaign if:
      - they have the same coupon code AND are within 14 days, OR
      - their bodies hash identically AND they're within 7 days
    Otherwise each message is its own campaign.
    """
    # Sort by time (records are already chronological from segment_messages)
    sorted_recs = sorted(enumerate(records), key=lambda kv: kv[1]["sent_at"])

    # Coupon-code → first record assigning this campaign
    coupon_anchors: dict[str, tuple[int, datetime]] = {}
    body_anchors: dict[str, tuple[int, datetime]] = {}

    for idx, r in sorted_recs:
        sent = datetime.fromisoformat(r["sent_at"])
        assigned: Optional[str] = None

        # Coupon-based grouping
        if r.get("coupon_code"):
            code = r["coupon_code"]
            anchor = coupon_anchors.get(code)
            if anchor and (sent - anchor[1]).days <= 14:
                assigned = f"camp_coupon_{code.lower()}_{anchor[0]}"
            else:
                coupon_anchors[code] = (idx, sent)
                assigned = f"camp_coupon_{code.lower()}_{idx}"
        else:
            # Body-hash grouping (already computed as body_hash)
            h = r["body_hash"]
            anchor = body_anchors.get(h)
            if anchor and (sent - anchor[1]).days <= 7:
                assigned = f"camp_body_{h}_{anchor[0]}"
            else:
                body_anchors[h] = (idx, sent)
                assigned = f"camp_body_{h}_{idx}"

        r["campaign_id"] = assigned


def build_campaigns(records: list[dict]) -> list[dict]:
    """Group records by campaign_id into one entry per campaign."""
    by_camp: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        if r.get("is_duplicate"):
            continue
        by_camp[r["campaign_id"]].append(r)

    campaigns = []
    for cid, msgs in by_camp.items():
        msgs = sorted(msgs, key=lambda m: m["sent_at"])
        first, last = msgs[0], msgs[-1]
        campaigns.append({
            "campaign_id": cid,
            "n_messages": len(msgs),
            "first_seen": first["sent_date"],
            "last_seen": last["sent_date"],
            "expires_at": first["expires_at"],
            "duration_hours": first["duration_hours"],
            "has_discount": first["has_discount"],
            "discount_pct": first["discount_pct"],
            "discount_kind": first["discount_kind"],
            "stacked_extra_pct": first["stacked_extra_pct"],
            "effective_pct": first["effective_pct"],
            "is_range": first.get("is_range"),
            "scope": first["scope"],
            "brands": first["brands"],
            "brands_open_ended": first["brands_open_ended"],
            "categories": first["categories"],
            "gating": first["gating"],
            "coupon_code": first["coupon_code"],
            "gating_label": first["gating_label"],
            "event_tag": first["event_tag"],
            "is_marketing_only": first["is_marketing_only"],
            "has_free_shipping": first.get("has_free_shipping", False),
            "free_shipping_threshold_nis": first.get("free_shipping_threshold_nis"),
        })
    return sorted(campaigns, key=lambda c: c["first_seen"])


# ---------------------------------------------------------------------------
# Sale-moment bucketing — group event tags into coarser shopper-relevant buckets
# ---------------------------------------------------------------------------

# The reviewer correctly noted that BF/Cyber/Crazy November overlap — they're one
# shopping window from the buyer's perspective. Same logic for the late-winter
# stacked promos. These buckets are what the artifact should display.

def sale_moment_bucket(record: dict) -> str:
    """Return a coarse sale-moment bucket for analytics aggregation."""
    et = record.get("event_tag")
    # Records come in two shapes: messages (with 'sent_date') and campaigns
    # (with 'first_seen' ISO timestamp). Both expose the month in position [5:7].
    sent = record.get("sent_date") or record.get("first_seen") or ""
    month = sent[5:7] if len(sent) >= 7 else ""

    # Outlet is a permanent inventory channel, not a seasonal moment — classify it
    # first so deep outlet deals (typically 80%+) never inflate the headline of
    # winter_clearance / summer_clearance buckets, which are about full-price-line
    # markdowns at end of season.
    if record.get("scope") == "outlet":
        return "outlet"

    # November mega-sale: BF, Cyber, Crazy November all roll up
    if et in ("black_friday", "cyber_monday", "crazy_november", "singles_day"):
        return "november_mega_sale"

    # Winter clearance: stacked promos in Jan–Mar (end of cold season)
    if record.get("discount_kind") == "stacked" and month in ("01", "02", "03"):
        return "winter_clearance"
    # Winter clearance: deep up_to / flat (40%+) in Jan-Mar fall under same bucket
    if month in ("01", "02", "03") and record.get("discount_kind") in ("flat", "up_to") and (record.get("discount_pct") or 0) >= 40:
        return "winter_clearance"

    # Summer clearance: end-of-season deals run mid-July through August in Israel.
    # Most stores stack a 40%+ base with an extra coupon; we also catch deep flats
    # like Story's "Final Move: 50% off" Aug 18 message.
    if record.get("discount_kind") == "stacked" and month in ("07", "08"):
        return "summer_clearance"
    if month in ("07", "08") and record.get("discount_kind") in ("flat", "up_to") and (record.get("discount_pct") or 0) >= 40:
        return "summer_clearance"

    if et == "mid_season":
        return "mid_season"
    if et == "store_day":
        return "store_day"
    if et == "white_sale":
        return "white_days"
    if et == "passover":
        return "passover"
    if et == "independence_day":
        return "independence_day"
    if et == "valentines_day":
        return "valentines_day"
    if et == "purim":
        return "purim"
    if et == "flash_sale":
        return "flash_sale"
    if record.get("gating") in ("vip_card", "new_member"):
        return "vip_or_card"
    return "routine"


SALE_MOMENT_LABEL = {
    "november_mega_sale": "November mega-sale (BF / Cyber / Crazy November)",
    "winter_clearance": "Winter clearance (Jan–Mar deep deals)",
    "summer_clearance": "Summer clearance (Jul–Aug end-of-season)",
    "mid_season": "Mid-season sale",
    "store_day": "Store-day events",
    "white_days": "White Days (pre-Passover white sale)",
    "passover": "Passover",
    "independence_day": "Independence Day",
    "valentines_day": "Valentine's Day",
    "purim": "Purim",
    "flash_sale": "Flash sale",
    "outlet": "Outlet",
    "vip_or_card": "VIP / card-only",
    "routine": "Routine promos",
}


def compute_sale_moments(campaigns: list[dict]) -> list[dict]:
    """Aggregate campaigns by sale-moment bucket. Returns sorted list."""
    by_bucket: dict[str, list[dict]] = defaultdict(list)
    for c in campaigns:
        if not c["has_discount"]:
            continue
        by_bucket[sale_moment_bucket(c)].append(c)

    buckets = []
    for key, group in by_bucket.items():
        # Use HEADLINE pct for the bucket median (this is what the user sees in the SMS)
        headlines = [c["discount_pct"] for c in group if c["discount_pct"] is not None]
        effectives = [c["effective_pct"] for c in group if c["effective_pct"] is not None]
        kind_mix = defaultdict(int)
        for c in group:
            kind_mix[c["discount_kind"]] += 1
        flat_only = [c["discount_pct"] for c in group if c["discount_kind"] == "flat"]
        buckets.append({
            "bucket": key,
            "label": SALE_MOMENT_LABEL.get(key, key),
            "n": len(group),
            "median_headline_pct": int(statistics.median(headlines)) if headlines else None,
            "max_headline_pct": max(headlines) if headlines else None,
            "median_flat_pct": int(statistics.median(flat_only)) if flat_only else None,
            "median_effective_pct": int(statistics.median(effectives)) if effectives else None,
            "kind_mix": dict(kind_mix),
        })
    # Sort by median_headline desc (deepest moments first)
    return sorted(buckets, key=lambda b: -(b["median_headline_pct"] or 0))


# ---------------------------------------------------------------------------
# Per-brand recommendations
# ---------------------------------------------------------------------------

# Confidence buckets by sample size
def _confidence(n: int) -> str:
    if n >= 20:
        return "high"
    if n >= 8:
        return "medium"
    if n >= 3:
        return "low"
    return "insufficient"


def percentile(values: list[float], p: float) -> Optional[float]:
    """Return p-th percentile (0-1). Returns None for empty list."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def compute_brand_recommendations(campaigns: list[dict], brand_vocab: list[dict]) -> list[dict]:
    """
    For each brand (top 20 by mention count), compute:
      - n_explicit: campaigns naming this brand explicitly
      - n_sitewide: sitewide campaigns (which include this brand)
      - n_relevant: explicit + sitewide (the user can use these for this brand)
      - flat_p50, flat_p75: median / 75th percentile of flat discounts
      - buy_threshold_pct: 75th-percentile flat (this is the "good deal" bar)
      - max_seen: deepest deal observed for this brand
      - best_window: name of the sale-moment bucket where the deepest deals occurred
      - confidence: based on n_flat sample size
      - advice: one-line natural language verdict
    """
    # Order brands by mention count, take top set
    ranked = [b["brand"] for b in brand_vocab]

    out = []
    for brand in ranked:
        explicit = [c for c in campaigns if c["has_discount"] and brand in c["brands"]]
        sitewide = [c for c in campaigns if c["has_discount"] and c["scope"] == "sitewide"]
        relevant = explicit + sitewide

        if not relevant:
            continue

        flat_vals = [c["discount_pct"] for c in relevant if c["discount_kind"] == "flat" and c["discount_pct"] is not None]
        flat_p50 = percentile(flat_vals, 0.5)
        flat_p75 = percentile(flat_vals, 0.75)

        # Two peaks: explicit (brand named) vs via sitewide (brand implied)
        def _peak(camps):
            if not camps:
                return None
            top = max(camps, key=lambda c: c["effective_pct"] or 0)
            return {
                "effective_pct": top["effective_pct"],
                "headline_pct": top["discount_pct"],
                "kind": top["discount_kind"],
                "date": top["first_seen"],
            }
        max_explicit = _peak(explicit)
        max_via_sitewide = _peak(sitewide)

        # Best window: which sale-moment bucket has the deepest median for this brand?
        # Use EXPLICIT only when we have enough; sitewide best-windows are identical for all brands.
        bucket_source = explicit if len(explicit) >= 5 else relevant
        by_bucket = defaultdict(list)
        for c in bucket_source:
            by_bucket[sale_moment_bucket(c)].append(c["effective_pct"] or 0)
        if by_bucket:
            best_bucket = max(by_bucket.items(),
                              key=lambda kv: statistics.median(kv[1]) if kv[1] else 0)[0]
            best_window = SALE_MOMENT_LABEL.get(best_bucket, best_bucket)
        else:
            best_window = None

        # Normal range: 25th–75th percentile of flat (or "—" if too few)
        flat_p25 = percentile(flat_vals, 0.25)
        normal_range = None
        if flat_p25 is not None and flat_p75 is not None:
            normal_range = [int(flat_p25), int(flat_p75)]

        n_flat = len(flat_vals)
        conf = _confidence(n_flat)

        # Advice synthesis — use the EXPLICIT peak for brand-specific framing
        peak_for_advice = max_explicit or max_via_sitewide
        advice = _make_advice(
            brand, flat_p75, normal_range, best_window,
            peak_for_advice["effective_pct"] if peak_for_advice else None,
            peak_for_advice["kind"] if peak_for_advice else None,
            conf,
            from_sitewide=(max_explicit is None and max_via_sitewide is not None),
        )

        out.append({
            "brand": brand,
            "family": brand_family(brand),
            "n_total": len(relevant),
            "n_explicit": len(explicit),
            "n_sitewide_eligible": len(sitewide),
            "n_flat": n_flat,
            "flat_p50_pct": int(flat_p50) if flat_p50 is not None else None,
            "flat_p75_pct": int(flat_p75) if flat_p75 is not None else None,
            "buy_threshold_pct": int(flat_p75) if flat_p75 is not None else None,
            "normal_range_pct": normal_range,
            "max_explicit": max_explicit,
            "max_via_sitewide": max_via_sitewide,
            "best_window": best_window,
            "confidence": conf,
            "advice": advice,
        })

    # Sort by relevance (explicit count desc, then total)
    return sorted(out, key=lambda r: (-r["n_explicit"], -r["n_total"]))


def _make_advice(brand, p75, normal_range, best_window, max_eff, max_kind, conf, from_sitewide=False):
    if conf == "insufficient":
        return f"Too few {brand} flat deals to recommend a threshold."
    if p75 is None:
        return f"Watch sitewide deals — explicit {brand} flat promos are rare."
    peak_clause = ""
    if max_eff is not None and max_eff > (p75 or 0) + 20:
        kind_word = {"flat": "flat", "up_to": "ceiling", "stacked": "stacked"}.get(max_kind, "")
        source = " via sitewide" if from_sitewide else ""
        peak_clause = f" Peak{source}: {max_eff}% ({kind_word})."
    if not normal_range:
        return f"Buy at {int(p75)}%+ flat.{peak_clause}"
    lo, hi = normal_range
    if hi - lo <= 5:
        return f"Routine band is tight at {lo}–{hi}%. Anything {int(p75)}%+ is worth a look.{peak_clause}"
    return f"Buy at {int(p75)}%+ flat. Routine band is {lo}–{hi}%.{peak_clause}"


# ---------------------------------------------------------------------------
# Monthly aggregates
# ---------------------------------------------------------------------------

def compute_monthly(campaigns: list[dict]) -> list[dict]:
    """One entry per (year, month) that has data."""
    by_month: dict[str, list[dict]] = defaultdict(list)
    for c in campaigns:
        if not c["has_discount"]:
            continue
        key = c["first_seen"][:7]  # YYYY-MM
        by_month[key].append(c)

    out = []
    for ym, group in sorted(by_month.items()):
        # Use effective_pct (compound math) for honesty in the rollup
        effs = [c["effective_pct"] for c in group if c["effective_pct"] is not None]
        flats = [c["discount_pct"] for c in group if c["discount_kind"] == "flat"]
        ups = [c["discount_pct"] for c in group if c["discount_kind"] == "up_to"]
        out.append({
            "month": ym,
            "n_campaigns": len(group),
            "mean_effective_pct": int(round(statistics.mean(effs))) if effs else None,
            "max_effective_pct": max(effs) if effs else None,
            "median_flat_pct": int(statistics.median(flats)) if flats else None,
            "median_up_to_pct": int(statistics.median(ups)) if ups else None,
        })
    return out


# ---------------------------------------------------------------------------
# Verdict — the one-line top-of-page recommendation
# ---------------------------------------------------------------------------

def compute_verdict(sale_moments: list[dict], monthly: list[dict]) -> str:
    """A single sentence answering: when is the best time to buy at this store?"""
    if not sale_moments:
        return "Not enough data to recommend a buying window."
    # Find the bucket with deepest median that isn't routine/outlet noise
    relevant = [b for b in sale_moments if b["bucket"] not in ("routine", "outlet", "flash_sale", "vip_or_card")]
    if not relevant:
        relevant = sale_moments
    # Pick the bucket with the best EFFECTIVE median (falls back to headline if effective missing).
    # This catches stacked clearances (50%+15% = 57% effective) that the headline number
    # alone would understate.
    def _score(b: dict) -> int:
        return b.get("median_effective_pct") or b.get("median_headline_pct") or 0
    top = max(relevant, key=_score)
    routine = next((b for b in sale_moments if b["bucket"] == "routine"), None)
    routine_str = ""
    if routine and routine.get("median_flat_pct"):
        routine_str = f" Routine promos sit around {routine['median_flat_pct']}%, so anything below that isn't urgent."
    # If headline and effective diverge meaningfully (stacked deals), surface both.
    headline = top["median_headline_pct"]
    effective = top.get("median_effective_pct")
    if effective and effective > (headline or 0) + 3:
        depth_str = f"median {headline}% headline ({effective}% effective once extras apply), max {top['max_headline_pct']}%"
    else:
        depth_str = f"median {headline}%, max {top['max_headline_pct']}%"
    return f"Best buying window seen: {top['label'].lower()} ({depth_str}).{routine_str}"


# ---------------------------------------------------------------------------
# Shopping summary — structured buying guidance for the dashboard header
# ---------------------------------------------------------------------------

BUCKET_MONTHS = {
    "november_mega_sale": "November",
    "winter_clearance": "Jan – Mar",
    "summer_clearance": "Jul – Aug",
    "mid_season": "Mar / Sep",
    "white_days": "Mar – Apr",
    "passover": "April",
    "independence_day": "May",
    "valentines_day": "February",
    "purim": "March",
    "flash_sale": "varies",
    "store_day": "varies",
}


def compute_shopping_summary(sale_moments: list[dict], campaigns: list[dict]) -> dict:
    """
    Structured buying guidance.

    Stores are first classified by temporal pattern, then advice is tailored:

      seasonal  — discounts concentrate in 1-2 months; outside that, don't expect promos
      sparse    — fewer than 8 discount campaigns; insufficient data for reliable stats
      normal    — regular promo cadence with clear seasonal peaks that beat routine
      always_on — discounts spread throughout the year with no meaningful timing advantage
    """
    from datetime import datetime as _dt
    from collections import defaultdict as _dd

    disc_camps = [c for c in campaigns if c["has_discount"]]
    n_discount = len(disc_camps)

    # ── 1. Temporal distribution ─────────────────────────────────────────────
    month_counts: dict[str, int] = _dd(int)
    for c in disc_camps:
        month_counts[c["first_seen"][:7]] += 1

    n_active_months = len(month_counts)

    if disc_camps:
        first_date = min(c["first_seen"] for c in disc_camps)
        last_date = max(c["first_seen"] for c in disc_camps)
        fd = _dt.fromisoformat(first_date[:10])
        ld = _dt.fromisoformat(last_date[:10])
        span_months = max(1, (ld.year - fd.year) * 12 + ld.month - fd.month + 1)
    else:
        span_months = 1

    # Concentration: fraction of discount campaigns in the busiest 2 months
    sorted_month_vals = sorted(month_counts.values(), reverse=True)
    top2 = sum(sorted_month_vals[:2])
    concentration = top2 / n_discount if n_discount else 1.0

    # ── 2. Routine baseline (computed before pattern classification) ──────────
    routine_flat = [
        c["discount_pct"] for c in campaigns
        if c["has_discount"] and c["discount_kind"] == "flat"
        and c["discount_pct"] is not None
        and sale_moment_bucket(c) == "routine"
    ]
    routine_baseline = int(statistics.median(routine_flat)) if routine_flat else None

    # ── 3. Store pattern classification ──────────────────────────────────────
    # Thresholds tuned to avoid false positives:
    #   seasonal  needs high concentration (≥75% in top-2 months)
    #   always_on needs both high temporal coverage AND no meaningful peak lift
    SPARSE_THRESHOLD = 8
    SEASONAL_CONCENTRATION = 0.75
    ALWAYS_ON_COVERAGE = 0.75   # 75%+ of months active

    if n_discount < SPARSE_THRESHOLD:
        store_pattern = "sparse"
    elif concentration >= SEASONAL_CONCENTRATION:
        store_pattern = "seasonal"
    elif (n_active_months / span_months) >= ALWAYS_ON_COVERAGE:
        # Only mark always_on if there's genuinely no peak advantage
        primary_exclude = {"routine", "outlet", "vip_or_card", "flash_sale"}
        potential_peaks = [
            m for m in sale_moments
            if m["bucket"] not in primary_exclude and m.get("max_headline_pct")
        ]
        peak_beats_baseline = any(
            (m["max_headline_pct"] or 0) > (routine_baseline or 0) + 15
            for m in potential_peaks
        )
        store_pattern = "always_on" if not peak_beats_baseline else "normal"
    else:
        store_pattern = "normal"

    # ── 4. Peak months (the 1-2 calendar months with the most discount activity) ─
    peak_month_keys = sorted(month_counts, key=month_counts.__getitem__, reverse=True)[:2]
    peak_months = []
    for ym in peak_month_keys:
        y, m_str = ym.split("-")
        peak_months.append(_dt(int(y), int(m_str), 1).strftime("%B"))

    # ── 5. Buy windows ────────────────────────────────────────────────────────
    def _score(m):
        return m.get("median_effective_pct") or m.get("median_headline_pct") or 0

    primary_exclude = {"routine", "outlet", "vip_or_card", "flash_sale"}
    primary = sorted(
        [m for m in sale_moments if m["bucket"] not in primary_exclude and m.get("max_headline_pct")],
        key=_score, reverse=True,
    )[:2]

    fallback = sorted(
        [m for m in sale_moments if m["bucket"] in ("flash_sale", "vip_or_card") and m.get("max_headline_pct")],
        key=_score, reverse=True,
    )

    combined = primary[:]
    if len(combined) < 2 and fallback:
        combined.append(fallback[0])

    buy_windows_raw = [
        {
            "bucket": w["bucket"],
            "label": w["label"],
            "months": BUCKET_MONTHS.get(w["bucket"], ""),
            "median_pct": w["median_headline_pct"],
            "max_pct": w["max_headline_pct"],
            "median_effective_pct": w.get("median_effective_pct"),
            "n": w["n"],
            "is_fallback": w["bucket"] in ("flash_sale", "vip_or_card"),
        }
        for w in combined
    ]

    # For seasonal stores: ignore bucket-based windows entirely.
    # The sale-moment bucketer assigns event tags (BF, clearance) but misses peak months
    # that have no special tag (e.g. SOHO's December/April clearance falls into "routine").
    # Use the actual peak-month data as the buying windows instead.
    if store_pattern == "seasonal":
        buy_windows = []
        for ym in peak_month_keys:
            month_camps = [c for c in disc_camps if c["first_seen"][:7] == ym]
            if not month_camps:
                continue
            headlines = [c["discount_pct"] for c in month_camps if c["discount_pct"] is not None]
            effectives = [c["effective_pct"] for c in month_camps if c["effective_pct"] is not None]
            y_str, m_str = ym.split("-")
            month_name = _dt(int(y_str), int(m_str), 1).strftime("%B")
            buy_windows.append({
                "bucket": f"month_{ym}",
                "label": f"{month_name} clearance",
                "months": month_name,
                "median_pct": int(statistics.median(headlines)) if headlines else None,
                "max_pct": max(headlines) if headlines else None,
                "median_effective_pct": int(statistics.median(effectives)) if effectives else None,
                "n": len(month_camps),
                "is_fallback": False,
            })
    elif store_pattern == "normal" and routine_baseline is not None:
        # Only filter windows against the routine baseline for normal stores.
        buy_windows = [w for w in buy_windows_raw if (w["max_pct"] or 0) > routine_baseline]
    else:
        buy_windows = buy_windows_raw

    always_on_sale = store_pattern == "always_on"

    # ── 6. Scalar metrics ─────────────────────────────────────────────────────
    all_flat = [
        c["discount_pct"] for c in campaigns
        if c["has_discount"] and c["discount_kind"] == "flat"
        and c["discount_pct"] is not None
        and sale_moment_bucket(c) != "outlet"
    ]
    min_threshold = int(percentile(all_flat, 0.75)) if len(all_flat) >= 3 else None

    disc_dates = sorted(c["first_seen"] for c in disc_camps)
    if len(disc_dates) >= 2:
        gaps = [
            (_dt.fromisoformat(disc_dates[i + 1]) - _dt.fromisoformat(disc_dates[i])).days
            for i in range(len(disc_dates) - 1)
        ]
        gaps = [g for g in gaps if g > 0]
        deal_frequency_days = round(statistics.median(gaps), 1) if gaps else None
    else:
        deal_frequency_days = None

    coupon_count = sum(1 for c in disc_camps if c.get("coupon_code"))
    coupon_coverage_pct = round(100 * coupon_count / n_discount) if n_discount else None
    stacked_extras = [
        c["stacked_extra_pct"] for c in campaigns
        if c["has_discount"] and c.get("stacked_extra_pct") is not None
    ]
    coupon_lift_pct = int(statistics.median(stacked_extras)) if stacked_extras else None

    wait_delta_pct = None
    if buy_windows and routine_baseline is not None:
        wait_delta_pct = (buy_windows[0]["max_pct"] or 0) - routine_baseline

    return {
        "store_pattern": store_pattern,
        "buy_windows": buy_windows,
        "peak_months": peak_months,
        "n_discount_campaigns": n_discount,
        "always_on_sale": always_on_sale,
        "min_threshold_pct": min_threshold if not always_on_sale else None,
        "routine_baseline_pct": routine_baseline,
        "deal_frequency_days": deal_frequency_days,
        "coupon_coverage_pct": coupon_coverage_pct,
        "coupon_lift_pct": coupon_lift_pct,
        "wait_delta_pct": wait_delta_pct if not always_on_sale else None,
    }