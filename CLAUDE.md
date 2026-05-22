# CLAUDE.md — Developer / LLM Handoff Guide

This file is for any developer or LLM picking up this codebase. It explains how the pipeline works end-to-end, where the complexity lives, known rough edges, and what is safe to change vs what is load-bearing.

---

## What this project does

Converts an iPhone SMS-export PDF from an Israeli retail store into a self-contained interactive HTML dashboard. The dashboard shows a Keepa-style discount timeline, per-brand buy thresholds, and structured "best time to buy" advice.

Four stores have been processed: TERMINALX, STORY, HM, SOHO. Their HTML dashboards are committed to `output/`. The source PDFs and raw JSON extracts are gitignored (private data).

---

## Pipeline overview

```
PDF
 └── extract.py          Parse → structured JSON
      └── analyze.py     Analytics → enriched JSON
           └── build_artifact.py   JSON + JSX template → self-contained HTML
```

`run_all.py` runs all three steps for every PDF in `sms pdf/`.

---

## File-by-file breakdown

### `extract.py`

The hardest file. Does four things:

1. **PDF text extraction** via `pypdf`. Hebrew text comes out in visual (reversed) order because pypdf reads the glyph stream left-to-right without RTL reordering. The parser handles most common patterns by reversing word order, but unusual sentence structures can mis-parse.

2. **Date inference**: iPhone SMS PDFs don't embed a date on every message — instead they show headers like "Tuesday" or "Last Tuesday". The tool infers the full ISO date by walking backward from the PDF's `CreationDate` metadata. If `CreationDate` is missing, pass `--export-date YYYY-MM-DD`.

3. **Discount extraction**: regex-based. Extracts:
   - `discount_pct` — the headline number (e.g. 50 from "50% off")
   - `discount_kind` — `flat` / `up_to` / `stacked`
   - `stacked_extra_pct` — the secondary number if stacked (e.g. 20 from "50% + 20% extra")
   - `effective_pct` — compound math: `round(100 * (1 - (1 - base/100) * (1 - extra/100)))`
   - `coupon_code` — ALL-CAPS token that looks like a code
   - `scope` — `sitewide` / `brand` / `outlet` / `category`
   - `event_tag` — `black_friday`, `passover`, `mid_season`, etc.
   - `expires_at` — parsed from Hebrew expiry phrases
   - `has_free_shipping`, `free_shipping_threshold_nis`

4. **Duplicate detection**: marks reminder SMS (same body within 7 days) as `is_duplicate`.

**Key constant to tweak:** `_COUPON_STOPWORDS` — ALL-CAPS tokens that look like coupon codes but aren't (city names, campaign labels, common words). Add new tokens here whenever a store surfaces them in `unrecognized_caps_tokens`.

**Known fragility:** The Hebrew month names and event-tag patterns are hardcoded regexes. Adding a new Israeli event (e.g. Sukkot, Rosh Hashana) requires adding patterns to the relevant dicts near the top of `extract.py`.

---

### `brands.py`

The brand vocabulary. Contains:

- **9 category sets** (`FOOTWEAR`, `SPORTSWEAR`, `DENIM`, `OUTDOOR`, `BEAUTY`, `ACCESSORIES`, `HOMEWEAR`, `KIDSWEAR`, `GENERAL`) — sets of single-word Latin brand names.
- **`MULTI_WORD_BRANDS`** — ordered list of multi-word brands (checked before single-word scan). Order matters — put longer/more specific names first.
- **`HEBREW_BRAND_ALIASES`** — `{"ניו באלאנס": "NEW BALANCE", ...}` — maps Hebrew brand names to their Latin canonical form. The parser checks these after RTL reversal.

**When adding a new store:** Check `unrecognized_caps_tokens` in the console output and add any real brands to the appropriate category set. If a brand name contains spaces, add it to `MULTI_WORD_BRANDS` instead.

---

### `analyze.py`

Analytics layer. Pure functions — takes the list of campaign dicts from `extract.py` and returns derived structures.

#### `sale_moment_bucket(record)`

Maps a campaign to one of 13 coarse buckets: `november_mega_sale`, `winter_clearance`, `summer_clearance`, `mid_season`, `white_days`, `passover`, `independence_day`, `valentines_day`, `purim`, `flash_sale`, `store_day`, `outlet`, `vip_or_card`, `routine`.

**Important:** outlet scope is classified first (before event tags) so clearance items don't inflate seasonal buckets.

**What needs tweaking:** The winter/summer clearance rules use month-of-year + discount_kind heuristics. If a new store sends deep discounts in months not covered (e.g. September), they'll fall into `routine`. Add a month range + threshold rule if needed.

#### `compute_shopping_summary(sale_moments, campaigns)`

The most complex function. Does two things:

**1. Classifies the store into a pattern:**

| Pattern | Criteria |
|---|---|
| `sparse` | Fewer than 8 discount campaigns — not enough data |
| `seasonal` | ≥75% of discount campaigns concentrated in the top 2 months |
| `always_on` | ≥75% of months active AND no sale moment beats routine by >15pp |
| `normal` | Everything else |

**Why this matters:** SOHO is a clearance outlet where BF events (50%) are actually WORSE than their routine clearance (70%). Without pattern classification, the old code called SOHO "always on sale" and told users to wait for 70%+ — which was the routine level, not a peak. Now SOHO is correctly classified as `seasonal` with December and April as the peak months (70%).

**2. Builds buy windows:**

- For `seasonal` stores: builds windows from the actual peak calendar months (not from `sale_moment_bucket`). This is critical — SOHO's best months have no event tag, so they'd fall into `routine` and be excluded by the bucket-based logic.
- For `normal` stores: uses `sale_moment_bucket` windows and filters out any window whose max% doesn't beat the routine baseline.
- For `sparse`/`always_on`: uses raw bucket windows without filtering.

**Thresholds that may need tuning:**

```python
SPARSE_THRESHOLD = 8          # min discount campaigns to trust stats
SEASONAL_CONCENTRATION = 0.75 # fraction of campaigns in top-2 months → seasonal
ALWAYS_ON_COVERAGE = 0.75     # fraction of months active → candidate for always_on
```

---

### `artifact_template_v3.jsx`

React/Recharts JSX source. Built by esbuild at publish time — viewers don't need Node.js.

`__DATA_PLACEHOLDER__` is replaced at build time with the full JSON.

**Key components:**

| Component | What it renders |
|---|---|
| `VerdictBand` | Sticky top bar: store name, date range, pattern-aware pill |
| `ShoppingHero` | First card: pattern banner + up to 2 buy window cards + KPI strip |
| `DiscountTimeline` | Keepa-style stacked bar chart with brand/category/sitewide filter |
| `BrandCard` | Per-brand verdict with buy threshold, normal range, peak deals |
| `SaleMomentsChart` | Horizontal bar chart ranked by median discount per bucket |
| `MonthlyChart` | Mean + max effective discount per month |
| `EvidenceDrawer` | Collapsible campaign list + raw message list |

**Pattern-aware rendering in ShoppingHero:**
- `always_on`: shows a single "shop anytime" prose card, no window cards.
- `seasonal`: shows an amber banner with peak months, then window cards.
- `sparse`: shows a gray "limited data" banner, then window cards.
- `normal`: shows window cards directly.

**Confidence badge:** window cards with `n ≤ 3` show a "low confidence" amber badge. The analysis is correct in principle but based on very few data points.

---

### `build_artifact.py`

Reads the JSX template, prepends a ReactDOM import, strips existing imports (esbuild will resolve them from `node_modules`), injects the JSON data, writes a temp `.jsx` file, runs esbuild with `--bundle --format=iife`, and inlines the output JS into an HTML shell.

**Windows note:** `npx` is a `.cmd` file on Windows and must be called with `shell=True` in `subprocess.run`. This is already set — don't remove it.

**esbuild flags:** `--bundle --format=iife --target=es2017 --platform=browser`. No `--loader=jsx` — esbuild infers JSX from the `.jsx` extension.

---

## What is known to be incomplete or rough

### Things that are correct but have known edge cases

1. **`compute_verdict`** (in `analyze.py`) is in the JSON output but not displayed in the dashboard. It's slightly stale for SOHO (still describes BF as best window). Not a user-facing problem, but if you add a verdicts section to the UI, update this function to be pattern-aware first.

2. **`always_on` store pattern** — the logic is implemented and tested in unit sense, but none of the 4 processed stores trigger it (they're all seasonal or normal). The `always_on` hero card in the template is untested with real data.

3. **The Keepa timeline carry-over logic** only applies to `november_mega_sale` and `winter_clearance` buckets (`MULTI_WEEK_BUCKETS`). If a new store has a multi-week summer clearance that isn't tagged as `summer_clearance`, days between explicit campaigns will show zero. Add the bucket to `MULTI_WEEK_BUCKETS` in the JSX if needed.

### Things that are wrong and known

1. **TERMINALX has RTL-fragmented beauty brand tokens** — `ESTEE`, `LANCOME`, `LAUDER` appear in `unrecognized_caps_tokens`. These are fragments of ESTÉE LAUDER and LANCÔME reversed by pypdf. To fix: add reversed multi-word forms to `MULTI_WORD_BRANDS` in `brands.py`. Low priority since TERMINALX is a fashion-first store.

2. **H&M buy windows have n=1 and n=2** — mid-season sale (n=1) and winter clearance (n=2). Low-confidence badges are shown. The numbers are technically correct but based on very few campaigns. More H&M SMS history would improve these.

3. **SOHO has no brand recommendations** — clearance outlet, SMS never mention specific brands. The brand verdict section shows a "no brand data" message. Correct behaviour, not a bug.

### What to do when a new store's analysis looks wrong

1. Check `unrecognized_caps_tokens` — add brands or stopwords.
2. Check `store_pattern` in the JSON — is the classification right? If a store you know is seasonal is classified as `normal`, its `concentration` score is probably below 0.75. Either lower `SEASONAL_CONCENTRATION` or check whether the store's campaigns are spread more than expected.
3. Check `buy_windows` — are the months correct? For seasonal stores they come from peak calendar months. For normal stores they come from `sale_moment_bucket` — if a real event isn't being detected, check the event tag patterns in `extract.py`.
4. Check `routine_baseline_pct` — if it seems too high, clearance/outlet campaigns are likely leaking into the routine bucket. Check their `scope` field; they should be `outlet` and get classified as `outlet` bucket before event tags.

---

## Environment notes

- **Python**: 3.13 on Windows (`py` launcher). On Mac, use `python3`.
- **Node**: v25.8.0, npm with react, react-dom, recharts installed locally.
- **esbuild**: invoked via `npx --yes esbuild` — downloads on first run if not cached.
- **Windows encoding**: The console is cp1252. Never use Unicode arrows (`→`) in `print()` statements — use `->` instead. The JSON and HTML output are UTF-8.

---

## Design decisions worth preserving

- **Self-contained HTML**: no CDN, no server. Viewers open the file in Chrome directly. This was a deliberate choice for LinkedIn/website sharing — don't add CDN dependencies.
- **JSX stays as source**: `artifact_template_v3.jsx` is human-readable source committed to the repo. The HTML output is a build artifact. If you add a chart or component, edit the JSX; run `build_artifact.py` to rebuild.
- **Compound discount math**: `effective_pct` uses `100 * (1 - (1 - base/100) * (1 - extra/100))`. A "70% + 20% extra" deal is 76% effective, not 90%. This is correct and intentional — don't simplify to addition.
- **Outlet excluded from main timeline**: outlet promos (typically 80%+ ceiling) are excluded from the "all" filter in the timeline. They inflate peaks and shadow the real full-price-line discount history.
