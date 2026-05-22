# SMS Campaign Analyser

Turn your iPhone SMS history with a retail store into an interactive discount dashboard — a Keepa-style timeline that shows every promo, the exact months worth waiting for, and a per-brand "buy at X%" threshold.

Built for the Israeli retail market (Hebrew + English SMS), but the extraction logic generalises to any store that sends discount SMS.

---

## Live demos

Open any file directly in Chrome — no internet connection required.

| Store | Dashboard |
|---|---|
| Terminal X | [TERMINALX_explorer.html](output/TERMINALX_explorer.html) |
| Story | [STORY_explorer.html](output/STORY_explorer.html) |
| H&M | [HM_explorer.html](output/HM_explorer.html) |
| SOHO | [SOHO_explorer.html](output/SOHO_explorer.html) |

---

## What it produces

Each dashboard shows:

- **Shopping intelligence card** — the 1–2 best buying windows per year, with the max discount seen and how many campaigns back it up. For stores with seasonal patterns ("only active in November"), it flags that explicitly instead of giving fake timing advice.
- **Keepa-style discount timeline** — every day in the history coloured by the best available deal. Filter by brand, category, sitewide-only, or outlet.
- **Brand verdict** — per-brand buy threshold (75th-percentile flat discount), normal range, and the deepest campaign that named that brand.
- **Sale moments chart** — which events (BF, clearance, mid-season…) actually beat routine promos, ranked by median discount.
- **Monthly view** — mean and max effective discount per month, using compound math (70% + 20% extra = 76%, not 90%).

---

## What you need

| Requirement | Version | Notes |
|---|---|---|
| iPhone | Any | With the store's SMS conversation |
| Mac or Windows | — | To run the script |
| Python | 3.10+ | `python3 --version` to check |
| Node.js | 18+ | `node --version` to check |

---

## Step 1 — Export the SMS from your iPhone

The tool reads a PDF you print from the Messages app. Here's how to make that PDF:

**a. Open the conversation on your iPhone**

Open **Messages** and tap the store's conversation thread.

**b. Select all messages**

Long-press any message bubble → tap **More…** → tap **Select All** (top-left).

**c. Open the print preview**

Tap the **share arrow** (bottom-right corner) → scroll down in the share sheet → tap **Print**.

**d. Pinch-zoom the print preview to open it as a PDF**

In the print preview, place two fingers on the page thumbnail and **pinch outward** (zoom in). This opens the full PDF viewer instead of sending to a printer.

**e. Share the PDF to your Mac**

Tap the **share icon** (top-right) → **AirDrop** → select your Mac. The PDF will appear in your Downloads folder.

> **Tip — AirDrop not showing your Mac?** Make sure your Mac has AirDrop set to "Everyone" or "Contacts Only": Finder → Go → AirDrop → set "Allow me to be discovered by".
>
> **Alternative:** Tap share → **Save to Files** → iCloud Drive. Then open it on your Mac via Finder → iCloud Drive.

---

## Step 2 — Set up the tool (first time only)

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/sms-campaign-analyser.git
cd sms-campaign-analyser

# Install Python dependencies
pip3 install -r requirements.txt

# Install Node.js dependencies (needed to build the dashboard)
npm install
```

> **On Mac**, use `pip3` and `python3`. On Windows, `pip` and `py` also work.

---

## Step 3 — Add your PDF and run

1. Copy your PDF into the `sms pdf/` folder.
2. Name it after the store — e.g. `Story.pdf`, `Zara.pdf`.
3. Run:

```bash
python3 run_all.py --pdfs "sms pdf/"
```

This produces two files per store inside `output/`:
- `STORY_v3.json` — the structured extract (keep local, not committed)
- `STORY_explorer.html` — the self-contained interactive dashboard (~2.5 MB)

### Process a single store

```bash
python3 extract.py --pdf "sms pdf/Story.pdf" --store STORY --out output/STORY_v3.json
python3 build_artifact.py --json output/STORY_v3.json --out output/STORY_explorer.html
```

---

## Step 4 — Open the dashboard

Double-click the `.html` file in Finder (or drag it into Chrome). React and Recharts are bundled directly into the file — no internet connection or local server needed.

On Mac terminal:

```bash
open output/STORY_explorer.html
```

---

## Adding a new store

1. Put the PDF in `sms pdf/`.
2. If the PDF filename differs from the store's canonical name, add a mapping to `STORE_NAMES` in `run_all.py`:
   ```python
   STORE_NAMES = {
       "terminalx2": "TERMINALX",
       "h&m": "HM",
       # add yours here:
       "zara_israel": "ZARA",
   }
   ```
3. If the store sells brands not yet in the vocabulary, add them to `brands.py` — see the section below.
4. Run `python3 run_all.py`.

### Adding brands to the vocabulary

`brands.py` holds the brand dictionary. Three lists matter:

| List | What goes here | Example |
|---|---|---|
| Category sets (`FOOTWEAR`, `SPORTSWEAR`, …) | Single-word brands | `"VEJA"`, `"NIKE"` |
| `MULTI_WORD_BRANDS` | Brands with spaces | `"NEW BALANCE"`, `"SCOTCH AND SODA"` |
| `HEBREW_BRAND_ALIASES` | Hebrew name → Latin name | `"ניו באלאנס": "NEW BALANCE"` |

### Review protocol — unrecognised tokens

After each run, the console prints `unrecognized_caps_tokens` — ALL-CAPS tokens that look brand-shaped but aren't in the vocabulary. For each:

| Token type | Action |
|---|---|
| Known brand (e.g. `BERGHOFF`) | Add to `brands.py` |
| Coupon code fragment (e.g. `WSSC`, `OCT`) | Add to `_COUPON_STOPWORDS` in `extract.py` |
| Campaign label (e.g. `CTRL`, `STAR`) | Add to `_COUPON_STOPWORDS` |
| City / country abbreviation (e.g. `TLV`) | Add to `_COUPON_STOPWORDS` |

---

## How the analysis works

```
PDF
 └─ extract.py        Parses every SMS: date, Hebrew RTL text, discount %,
    │                 coupon codes, expiry, free-shipping threshold.
    │                 Classifies: discount kind (flat / up-to / stacked),
    │                 scope (sitewide / brand / outlet), gating, event tag.
    │                 Groups into campaigns (deduplicates reminder SMS).
    │
    └─ analyze.py     Classifies the store by pattern:
       │                seasonal  — deals cluster in 1-2 months
       │                sparse    — fewer than 8 discount campaigns
       │                normal    — regular cadence with clear peaks
       │                always_on — deals spread year-round, no timing advantage
       │
       │              Computes: buy windows, brand thresholds, monthly rollups,
       │              sale-moment buckets, compound discount math.
       │
       └─ build_artifact.py   Injects the JSON into artifact_template_v3.jsx,
                              bundles React + Recharts via esbuild → self-contained HTML.
```

---

## Output schema (key fields)

```
store                  — canonical store name
shopping_summary
  store_pattern        — seasonal | sparse | normal | always_on
  buy_windows[]        — up to 2 best buying moments with max/median % and campaign count
  peak_months[]        — calendar months with the most discount activity
  min_threshold_pct    — 75th-pct of all flat deals (the "good deal" bar)
  routine_baseline_pct — median flat on untagged / routine campaigns
  deal_frequency_days  — median gap (days) between consecutive discount campaigns
  coupon_coverage_pct  — % of discount campaigns that include a coupon code
  coupon_lift_pct      — median extra % from stacked coupons
brand_recommendations[]
  brand                — brand name
  buy_threshold_pct    — 75th-percentile flat discount
  normal_range_pct     — [25th, 75th] percentile range
  max_explicit         — deepest campaign that named this brand explicitly
  max_via_sitewide     — deepest sitewide campaign (applies to all brands)
  confidence           — high | medium | low | insufficient
  advice               — one-line recommendation
sale_moments[]         — ranked buckets: median/max % per event type
monthly[]              — month-by-month mean/max effective discount
campaigns[]            — one record per deduplicated campaign
messages[]             — raw per-message records (~25 fields each)
```

---

## File structure

```
extract.py               — PDF parser → JSON
brands.py                — brand vocabulary (9 categories + Hebrew aliases)
analyze.py               — analytics layer (patterns, buy windows, brand recs)
artifact_template_v3.jsx — React/Recharts dashboard source
build_artifact.py        — bundles template + data → self-contained HTML
run_all.py               — batch runner for all PDFs in a folder
requirements.txt         — Python deps (pypdf)
package.json             — Node deps (react, recharts, esbuild)
sms pdf/                 — put your PDFs here (gitignored)
output/                  — generated JSON (gitignored) + HTML dashboards
```

---

## Known limitations

- **Date inference needs an export date.** The tool reads it from the PDF's `CreationDate` metadata, which iPhone sets reliably. If it's missing, pass `--export-date YYYY-MM-DD` to `extract.py`.
- **Hebrew RTL reversal.** pypdf extracts Hebrew in visual (reversed) order. The parser handles most patterns, but unusual sentence structures may mis-parse.
- **Multi-word beauty brands** (ESTÉE LAUDER, LANCÔME, L'ORÉAL) sometimes surface as individual tokens when the RTL flip prevents multi-word matching. Add the reversed form to `MULTI_WORD_BRANDS` if needed.
- **Clearance / outlet stores.** For stores like SOHO where the "best" discounts run during end-of-season clearance (not BF), the tool correctly identifies the seasonal pattern and shows the actual peak months rather than event-tagged buckets.
- **Single-brand stores** (H&M): the brand filter in the timeline is mostly redundant. The brand verdict section still works; it's just less interesting.
- **Coupon codes:** one coupon per message is captured. Messages with separate online/in-store codes will only capture one.
- **No cross-run accumulation.** Each PDF is a standalone extraction. Running "Story May 2026" after "Story December 2025" produces two independent JSONs.
- **Small datasets.** Buy windows backed by fewer than 3 campaigns are flagged "low confidence" in the dashboard. Treat them as directional only.

---

## Built with

- [pypdf](https://github.com/py-pdf/pypdf) — PDF text extraction
- [React 18](https://react.dev/) + [Recharts](https://recharts.org/) — interactive dashboard
- [esbuild](https://esbuild.github.io/) — bundles JSX + dependencies into a single self-contained file
- [Tailwind CSS](https://tailwindcss.com/) — layout utilities (CDN, only needed in development)
- [Fraunces](https://fonts.google.com/specimen/Fraunces) — serif display font
