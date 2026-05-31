"""
publish.py — generate published/ for yogevmarom.com/tools/sale-tracker/

Contract spec: yogevmarom-website/tools/sale-tracker/HANDOFF.md
Reference pattern: yogevmarom-website/pricey/index.html

For each store:
  1. Reads output/<STORE>_v3.json to derive the body insight sentence
  2. Takes output/<STORE>_explorer.html (bundled React dashboard)
  3. Injects the SEO <head> per the HANDOFF spec, lang/dir attributes,
     and a visible intro block (H1 + insight) that Google can index
  4. Writes to published/<slug>.html

Also writes published/manifest.json — the website sync script reads
this to auto-generate hub cards + sitemap. Adding store #5 = add an
entry to STORES below and re-run.

Usage:
    python publish.py                     # all stores
    python publish.py terminalx story     # specific slugs
"""

import json
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import _window_month_nums, BUCKET_MONTH_NUMS  # noqa: E402

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "output"
PUBLISHED = HERE / "published"
BASE_URL = "https://www.yogevmarom.com/tools/sales-calendar"
OG_IMAGE = "https://www.yogevmarom.com/uploads/og-sales-calendar.jpg"
TODAY = date.today().isoformat()

# ── Store registry ────────────────────────────────────────────────────────────
# Editorial fields (title, description, insight, h1) are written here, not
# derived, so they can be tuned for search intent without touching the data.
# `body_insight` is derived at runtime from the JSON shopping_summary.
STORES = [
    {
        "slug": "terminalx",
        "json": "TERMINALX_v3.json",
        "html": "TERMINALX_explorer.html",
        "nameHe": "טרמינל איקס",
        "nameEn": "Terminal X",
        "title": "מתי הכי כדאי לקנות בטרמינל X? — לוח מבצעים והנחות",
        "description": "ניתוח של מבצעי ה-SMS של טרמינל X: מתי ההנחות הכי גדולות, כמה הן שוות, ומתי כדאי לחכות.",
        "insight": "הכי משתלם: ינואר–מרץ, עד 70% הנחה",
        "h1": "מתי הכי כדאי לקנות בטרמינל X?",
    },
    {
        "slug": "story",
        "json": "STORY_v3.json",
        "html": "STORY_explorer.html",
        "nameHe": "סטורי",
        "nameEn": "Story",
        "title": "מתי הכי כדאי לקנות בסטורי? — לוח מבצעים והנחות",
        "description": "ניתוח מבצעי ה-SMS של סטורי: מתי הסיילים, באיזה עוצמה, ומה דפוס ההנחות לאורך השנה.",
        "insight": "הכי משתלם: קיץ (יולי–אוגוסט), עד 50% הנחה",
        "h1": "מתי הכי כדאי לקנות בסטורי?",
    },
    {
        "slug": "hm",
        "json": "HM_v3.json",
        "html": "HM_explorer.html",
        "nameHe": "אייץ' אנד אם",
        "nameEn": "H&M",
        "title": "מתי H&M עושה סייל? — לוח מבצעים והנחות",
        "description": "ניתוח מבצעי ה-SMS של H&M: מתי הסיילים, באיזה עוצמה, ומה דפוס ההנחות לאורך השנה.",
        "insight": "שיא הסיילים: סוף עונה ומבצעי בלאק פריידי",
        "h1": "מתי H&M עושה סייל?",
    },
    {
        "slug": "shoofra",
        "json": "SHOOFRA_v3.json",
        "html": "SHOOFRA_explorer.html",
        "nameHe": "שופרא",
        "nameEn": "Shoofra",
        "title": "מתי הכי כדאי לקנות בשופרא? — לוח מבצעים והנחות",
        "description": "ניתוח מבצעי ה-SMS של שופרא: מתי הסיילים, באיזה עוצמה, ומה דפוס ההנחות על נעליים לאורך השנה.",
        "insight": "הכי משתלם: ינואר–מרץ ויולי–אוגוסט — עד 50% הנחה",
        "h1": "מתי הכי כדאי לקנות בשופרא?",
    },
    {
        "slug": "ata",
        "json": "ATA_v3.json",
        "html": "ATA_explorer.html",
        "nameHe": "ATA",
        "nameEn": "ATA",
        "title": "מתי הכי כדאי לקנות ב-ATA? — לוח מבצעים והנחות",
        "description": "ניתוח מבצעי ה-SMS של ATA: מתי הסיילים, באיזה עוצמה, ומה דפוס ההנחות לאורך השנה.",
        "insight": "הכי משתלם: יולי–אוגוסט וינואר–מרץ — עד 50% הנחה",
        "h1": "מתי הכי כדאי לקנות ב-ATA?",
    },
]

# Hebrew labels for sale-moment buckets (for body_insight derivation)
BUCKET_HE = {
    "november_mega_sale": "נובמבר (Black Friday וסייבר מאנדיי)",
    "winter_clearance": "ינואר–מרץ (קלירנס חורף)",
    "summer_clearance": "יולי–אוגוסט (קלירנס קיץ)",
    "mid_season": "מבצעי אמצע עונה",
    "white_days": "ימי לבן (לפני פסח)",
    "passover": "חג הפסח",
    "independence_day": "יום העצמאות",
    "valentines_day": "ולנטיין",
    "purim": "פורים",
    "flash_sale": "מבצע פלאש",
    "store_day": "יום החנות",
}

MONTH_HE = {
    "January": "ינואר", "February": "פברואר", "March": "מרץ",
    "April": "אפריל", "May": "מאי", "June": "יוני",
    "July": "יולי", "August": "אוגוסט", "September": "ספטמבר",
    "October": "אוקטובר", "November": "נובמבר", "December": "דצמבר",
}

# Month number (1-12) -> Hebrew name, for the timing line in body_insight.
MONTH_NUM_HE = {
    1: "ינואר", 2: "פברואר", 3: "מרץ", 4: "אפריל", 5: "מאי", 6: "יוני",
    7: "יולי", 8: "אוגוסט", 9: "ספטמבר", 10: "אוקטובר", 11: "נובמבר", 12: "דצמבר",
}


def derive_timing_clause(data: dict) -> str:
    """Hebrew 'buy now vs wait' clause from the honest headline verdict."""
    hv = (data.get("insight_schema") or {}).get("headline_verdict") or {}
    timing = hv.get("timing")
    if timing == "buy_now":
        m = MONTH_NUM_HE.get(hv.get("current_month"), "")
        return f" עכשיו ({m}) זה אחד מחלונות הסייל הטובים בשנה — שווה לקנות."
    if timing == "wait":
        nxt = hv.get("next_window_month")
        if nxt:
            return f" כרגע לא חלון סייל — כדאי לחכות ל{MONTH_NUM_HE.get(nxt, '')}."
        return " כרגע לא תקופת סייל — כדאי לחכות."
    if timing == "anytime":
        return " ההנחות זמינות לאורך כל השנה, אין יתרון בתזמון."
    return ""


def derive_bait_clause(data: dict) -> str:
    """Hebrew honesty clause: the sitewide deal you actually get vs the deeper
    brand/category deals (or the advertised 'up to' ceiling when present)."""
    hv = (data.get("insight_schema") or {}).get("headline_verdict") or {}
    if hv.get("is_bait_ceiling") and hv.get("advertised_ceiling_pct") and hv.get("real_best_flat_pct"):
        return (f" שימו לב: הפרסומות מבטיחות \"עד {hv['advertised_ceiling_pct']}%\", "
                f"אבל ההנחה הגורפת האמיתית היא בערך {hv['real_best_flat_pct']}% פלאט.")
    if hv.get("category_deals_deeper") and hv.get("best_any_flat_pct") and hv.get("real_best_flat_pct"):
        return (f" ההנחה הגורפת על כל האתר היא עד {hv['real_best_flat_pct']}% — "
                f"הנחות עמוקות יותר (עד {hv['best_any_flat_pct']}%) שמורות למותגים או קטגוריות מסוימות.")
    return ""


# ── Body insight (longer sentence for the visible HTML block) ─────────────────

def derive_body_insight(data: dict, name_he: str) -> str:
    """Return a Hebrew sentence surfacing the key buying signal — shown as body
    text above the dashboard so Google can index it."""
    ss = data.get("shopping_summary", {})
    pattern = ss.get("store_pattern", "normal")
    windows = ss.get("buy_windows", [])
    peak_months = ss.get("peak_months", [])
    routine = ss.get("routine_baseline_pct")

    # Honesty + timing clauses appended to the seasonal/normal sentences.
    extra = derive_bait_clause(data) + derive_timing_clause(data)

    if pattern == "seasonal" and peak_months:
        months_he = " ו".join(MONTH_HE.get(m, m) for m in peak_months[:2])
        max_pct = windows[0]["max_pct"] if windows else "?"
        return (f"ב-{name_he} ההנחות מגיעות לשיא ב{months_he} — עד {max_pct}% הנחה. "
                f"מחוץ לתקופות אלו, כמעט ולא יוצאים מבצעים.{extra}")

    if pattern == "always_on":
        floor = routine or "?"
        return (f"{name_he} מציעה הנחות לאורך כל השנה — בסיס של כ-{floor}%. "
                f"אין יתרון מיוחד בתזמון הקנייה.")

    if windows:
        w = windows[0]
        label_he = BUCKET_HE.get(w["bucket"], w["label"])
        w2 = windows[1] if len(windows) > 1 else None
        routine_str = f" הנחות שגרתיות עומדות על כ-{routine}%." if routine else ""
        if w2:
            label2_he = BUCKET_HE.get(w2["bucket"], w2["label"])
            return (f"הזמן הכי טוב לקנות ב-{name_he}: {label_he} ו{label2_he} — "
                    f"עד {w['max_pct']}% הנחה.{routine_str}{extra}")
        return (f"הזמן הכי טוב לקנות ב-{name_he}: {label_he} — "
                f"עד {w['max_pct']}% הנחה.{routine_str}{extra}")

    return f"ניתוח היסטוריית מבצעי ה-SMS של {name_he}."


# ── Tailwind CSS compilation ──────────────────────────────────────────────────

def compile_tailwind_css() -> str:
    """Compile Tailwind against the JSX template once; return minified CSS."""
    input_css = HERE / "tailwind_input.css"
    with tempfile.NamedTemporaryFile(suffix=".css", delete=False) as tmp:
        out_path = tmp.name
    subprocess.run(
        ["npx", "--yes", "tailwindcss", "-i", str(input_css), "-o", out_path, "--minify"],
        cwd=str(HERE), shell=True, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    css = Path(out_path).read_text(encoding="utf-8")
    Path(out_path).unlink(missing_ok=True)
    return css


# ── SEO head (follows HANDOFF.md §2b exactly) ─────────────────────────────────

def make_seo_head(store: dict, tailwind_css: str) -> str:
    slug = store["slug"]
    canonical = f"{BASE_URL}/{slug}/"
    title = store["title"]
    description = store["description"]

    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": title,
        "description": description,
        "url": canonical,
        "creator": {
            "@type": "Person",
            "name": "Yogev Marom",
            "url": "https://www.yogevmarom.com",
        },
        "inLanguage": "he",
    }, ensure_ascii=False, indent=2)

    return f"""\
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />

<title>{title}</title>
<meta name="description" content="{description}" />
<link rel="canonical" href="{canonical}" />
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large" />

<link rel="alternate" hreflang="he" href="{canonical}" />
<link rel="alternate" hreflang="x-default" href="{canonical}" />

<meta property="og:type" content="website" />
<meta property="og:site_name" content="Yogev Marom" />
<meta property="og:url" content="{canonical}" />
<meta property="og:title" content="{title}" />
<meta property="og:description" content="{description}" />
<meta property="og:image" content="{OG_IMAGE}" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
<meta property="og:image:alt" content="{title}" />
<meta property="og:locale" content="he_IL" />

<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{title}" />
<meta name="twitter:description" content="{description}" />
<meta name="twitter:image" content="{OG_IMAGE}" />

<script type="application/ld+json">
{jsonld}
</script>

<style>{tailwind_css}</style>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#FAF8F4}}</style>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..900;1,9..144,300..900&family=Heebo:wght@400;500;600&display=swap" rel="stylesheet">"""


# ── Visible intro block (H1 + insight sentence, indexable by Google) ──────────

def make_intro_block(store: dict, body_insight: str) -> str:
    return (
        '<div style="background:#FAF8F4;padding:24px 32px 14px;'
        'font-family:\'Heebo\',system-ui,sans-serif;direction:rtl;text-align:right;'
        'border-bottom:1px solid #D9D5CC;">\n'
        '  <p style="font-size:11px;letter-spacing:.12em;text-transform:uppercase;'
        'color:#9A9994;margin:0 0 6px 0;font-family:ui-monospace,monospace;">'
        'ניתוח מבצעי SMS</p>\n'
        f'  <h1 style="font-size:22px;font-weight:600;color:#16161A;margin:0 0 8px 0;'
        f'line-height:1.3;">{store["h1"]}</h1>\n'
        f'  <p style="font-size:14px;color:#52525B;margin:0;line-height:1.65;">'
        f'{body_insight}</p>\n'
        '</div>'
    )


# ── HTML transformation ───────────────────────────────────────────────────────

def make_store_nav(all_stores: list[dict], current_slug: str) -> str:
    """Pure-HTML store switcher bar — no JS, no React dependency.
    current_slug=None means the hub/index page is active."""
    ACCENT = "#B91C1C"
    INK_SOFT = "#52525B"
    RULE = "#D9D5CC"
    PAPER = "#FAF8F4"

    pills = []

    # Hub / index link — always first
    is_hub = current_slug is None
    if is_hub:
        hub_style = (
            f"display:inline-flex;align-items:center;gap:5px;padding:4px 12px;"
            f"border-radius:999px;font-size:10px;font-family:ui-monospace,monospace;"
            f"letter-spacing:.12em;text-transform:uppercase;text-decoration:none;"
            f"font-weight:700;background:{ACCENT};color:white;cursor:default;"
        )
        pills.append(f'<span style="{hub_style}">&#9632; INDEX</span>')
    else:
        hub_style = (
            f"display:inline-flex;align-items:center;gap:5px;padding:4px 12px;"
            f"border-radius:999px;font-size:10px;font-family:ui-monospace,monospace;"
            f"letter-spacing:.12em;text-transform:uppercase;text-decoration:none;"
            f"font-weight:400;background:transparent;color:{INK_SOFT};"
            f"border:1px solid {RULE};cursor:pointer;"
        )
        pills.append(
            f'<a href="index.html" style="{hub_style}"'
            f' onmouseover="this.style.borderColor=\'{ACCENT}\';this.style.color=\'{ACCENT}\'"'
            f' onmouseout="this.style.borderColor=\'{RULE}\';this.style.color=\'{INK_SOFT}\'"'
            f'>&#9632; INDEX</a>'
        )

    # Separator between hub and store pills
    pills.append(f'<span style="color:{RULE};font-size:10px;margin:0 2px;">|</span>')

    for s in all_stores:
        slug = s["slug"]
        name = s["nameEn"].upper()
        is_current = slug == current_slug
        if is_current:
            style = (
                f"display:inline-block;padding:4px 12px;border-radius:999px;"
                f"font-size:10px;font-family:ui-monospace,monospace;"
                f"letter-spacing:.12em;text-transform:uppercase;text-decoration:none;"
                f"font-weight:700;background:{ACCENT};color:white;cursor:default;"
            )
            pills.append(f'<span style="{style}">{name}</span>')
        else:
            style = (
                f"display:inline-block;padding:4px 12px;border-radius:999px;"
                f"font-size:10px;font-family:ui-monospace,monospace;"
                f"letter-spacing:.12em;text-transform:uppercase;text-decoration:none;"
                f"font-weight:400;background:transparent;color:{INK_SOFT};"
                f"border:1px solid {RULE};cursor:pointer;"
            )
            pills.append(
                f'<a href="{slug}.html" style="{style}"'
                f' onmouseover="this.style.borderColor=\'{ACCENT}\';this.style.color=\'{ACCENT}\'"'
                f' onmouseout="this.style.borderColor=\'{RULE}\';this.style.color=\'{INK_SOFT}\'"'
                f'>{name}</a>'
            )

    pills_html = "\n    ".join(pills)
    return (
        f'<nav style="background:{PAPER};border-bottom:1px solid {RULE};'
        f'padding:8px 24px;position:sticky;top:0;z-index:200;">\n'
        f'  <div style="max-width:1152px;margin:0 auto;display:flex;'
        f'align-items:center;gap:6px;flex-wrap:wrap;direction:ltr;">\n'
        f'    <span style="font-size:9px;letter-spacing:.15em;text-transform:uppercase;'
        f'color:#9A9994;font-family:ui-monospace,monospace;margin-right:4px;">STORE</span>\n'
        f'    {pills_html}\n'
        f'  </div>\n'
        f'</nav>'
    )


def process_html(raw: str, seo_head: str, intro: str, store_nav: str = "") -> str:
    # Set lang + dir on <html>
    html = re.sub(r"<html[^>]*>", '<html lang="he" dir="rtl">', raw, count=1)
    # Replace entire <head> block (also strips cdn.tailwindcss.com script)
    html = re.sub(
        r"<head>.*?</head>",
        f"<head>\n{seo_head}\n</head>",
        html, count=1, flags=re.DOTALL,
    )
    # Inject store nav (sticky, pure HTML) then intro block, right after <body>
    inject = ""
    if store_nav:
        inject += store_nav + "\n"
    inject += intro + "\n"
    html = re.sub(r"<body>", f"<body>\n{inject}", html, count=1)
    return html


# ── Hub: cross-store sale calendar ────────────────────────────────────────────

HUB = {
    "title": "מתי הכי כדאי לקנות? לוח הסיילים החודשי — טרמינל X, סטורי, H&M ועוד",
    "description": "לוח סיילים חודשי על בסיס ניתוח מאות מבצעי SMS מ-6 רשתות אופנה: מתי כל רשת עושה סייל, כמה הוא שווה באמת, ומתי כדאי לחכות.",
    "h1": "מתי הכי כדאי לקנות? לוח הסיילים החודשי",
    "intro": "ניתחנו מאות הודעות SMS מ-6 רשתות אופנה כדי לבנות לוח סיילים שנתי — לכל חודש, מי באמת בסייל וכמה ההנחה שווה. הצבע מציין את עומק ההנחה.",
    # A keyword-rich lede line surfaced as real body text (Google indexes text,
    # not the calendar canvas). Weaves the queries shoppers actually type.
    "keywords_lede": "מתי יש סייל בטרמינל X, בסטורי, ב-H&M, בשופרא וב-ATA? לוח המבצעים השנתי מרכז את תאריכי הסיילים, עומק ההנחות, ומועדי בלאק פריידי, סוף עונה וסיילים של קיץ וחורף.",
}

# Visible FAQ shown at the bottom of the hub AND emitted as FAQPage schema, so
# the structured data always matches on-page content (Google's 2026 rule). Fixed
# editorial copy — kept in sync with the EN dictionary on the website side.
HUB_FAQ = [
    ("מתי הכי כדאי לקנות בגדים בסייל בישראל?",
     "ההנחות הכי עמוקות מגיעות בסוף עונה — בינואר–פברואר ובסוף הקיץ (יולי–אוגוסט) — וכן בתקופת בלאק פריידי ונובמבר. כדאי לחכות כ-3–4 שבועות מתחילת הסייל, כשההנחה הגורפת מעמיקה."),
    ("מתי הסייל הגדול של השנה?",
     "שתי נקודות השיא הן סוף עונת החורף (ינואר–פברואר) וסוף עונת הקיץ (יולי–אוגוסט), ובנוסף בלאק פריידי ומבצעי נובמבר — אז נראות ההנחות הגורפות הגדולות ביותר."),
    ("כמה זמן כדאי לחכות אחרי שמתחיל הסייל?",
     "בדרך כלל ההנחה הראשונית קטנה. אחרי כ-3–4 שבועות הרשתות מעמיקות את ההנחה כדי לפנות מלאי — וזה הזמן המשתלם ביותר לקנות."),
    ("באיזו רשת הסייל הכי שווה?",
     "זה משתנה בין הרשתות — הלוח החודשי שלמעלה מראה לכל רשת (טרמינל X, סטורי, H&M, שופרא ו-ATA) את החודשים עם ההנחה הגורפת הכי גדולה. ככלל, סוף עונה ובלאק פריידי הם הזמנים הכי שווים."),
    ("איך נאסף המידע על המבצעים?",
     "ניתחנו מאות הודעות SMS שיווקיות מהרשתות לאורך זמן, וחילצנו את עומק ההנחה הגורפת (פלאט) שניתנה בפועל בכל חודש — לא רק את תקרת הפרסום (עד X%)."),
]

# Abbreviated Hebrew month headers for the grid (index 1-12).
MONTH_ABBR_HE = {
    1: "ינו׳", 2: "פבר׳", 3: "מרץ", 4: "אפר׳", 5: "מאי", 6: "יוני",
    7: "יולי", 8: "אוג׳", 9: "ספט׳", 10: "אוק׳", 11: "נוב׳", 12: "דצמ׳",
}


def build_calendar_row(store: dict, data: dict) -> dict:
    """Per-store 12-month sale map — direct GROUP BY on raw campaigns.

    For each calendar month: max flat discount wins (shown colored); if no flat
    exists, the best up_to/stacked effective_pct is shown in gray as a ceiling.
    No pre-aggregated summaries used — campaigns is the source of truth.
    """
    from collections import defaultdict as _dd

    ss = data.get("shopping_summary", {})
    hv = (data.get("insight_schema") or {}).get("headline_verdict") or {}

    by_month: dict = _dd(lambda: {"flat": [], "upto": [], "n": 0})
    for c in data.get("campaigns", []):
        if not c.get("has_discount"):
            continue
        if c.get("scope") != "sitewide":  # calendar shows sitewide deals only — matches header
            continue
        pct = c.get("discount_pct")
        if pct is None:
            continue
        cal_m = int(c["first_seen"][5:7])
        by_month[cal_m]["n"] += 1
        kind = c.get("discount_kind")
        if kind == "flat":
            by_month[cal_m]["flat"].append(pct)
        elif kind in ("up_to", "stacked"):
            eff = c.get("effective_pct") or pct
            by_month[cal_m]["upto"].append(eff)

    months: dict[int, dict] = {}
    for cal_m, agg in by_month.items():
        if agg["flat"]:
            months[cal_m] = {"pct": max(agg["flat"]), "n": agg["n"], "is_upto": False}
        elif agg["upto"]:
            months[cal_m] = {"pct": int(max(agg["upto"])), "n": agg["n"], "is_upto": True}

    return {
        "slug": store["slug"],
        "nameHe": store["nameHe"],
        "nameEn": store["nameEn"],
        "insight": store.get("insight", ""),
        "real_flat": hv.get("real_best_flat_pct"),
        "pattern": ss.get("store_pattern"),
        "good_months": set(hv.get("good_months", [])),
        "next_window_month": hv.get("next_window_month"),
        "months": months,
    }


def _depth_color(pct):
    """Color ramp mirrors the JSX design tokens."""
    if pct is None:
        return None
    if pct >= 50:
        return "#B91C1C"   # accent — deep
    if pct >= 40:
        return "#1F3A8A"   # flat-blue — mid
    return "#A8A29E"        # light


def _calendar_cell(cell, is_current_month):
    """Render one grid <td>.
    Flat → colored chip (the actual guaranteed discount).
    Up-to/stacked → gray chip (ceiling; hover tooltip says so).
    """
    border = "border-bottom:1px solid #ECE9E2;"
    if cell is None:
        dot = '<span style="color:#D9D5CC;">·</span>'
        return f'<td style="text-align:center;padding:7px 4px;{border}">{dot}</td>'

    pct = cell["pct"]
    is_upto = cell.get("is_upto", False)
    n = cell.get("n", 0)
    kind_label = "עד" if is_upto else "פלאט"
    title = f'{kind_label} {pct}% · {n} קמפיינים'

    if is_upto:
        chip = (
            f'<span title="{title}" '
            f'style="display:inline-block;min-width:40px;padding:3px 7px;border-radius:6px;'
            f'font-size:11px;font-weight:500;font-family:ui-monospace,monospace;'
            f'color:#9A9994;background:#F4F4F5;border:1px solid #D4D4D8;">{pct}%</span>'
        )
    else:
        color = _depth_color(pct)
        chip = (
            f'<span title="{title}" '
            f'style="display:inline-block;min-width:40px;padding:3px 7px;border-radius:6px;'
            f'font-size:12px;font-weight:700;font-family:ui-monospace,monospace;'
            f'color:#fff;background:{color};">{pct}%</span>'
        )
    return f'<td style="text-align:center;padding:9px 6px;{border}">{chip}</td>'


def make_hub_head(tailwind_css: str, store_calendars: list[dict]) -> str:
    """SEO <head> for the hub: ItemList of store pages + FAQPage rich results."""
    canonical = f"{BASE_URL}/"
    title = HUB["title"]
    description = HUB["description"]

    item_list = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": title,
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "url": f"{BASE_URL}/{c['slug']}/",
                "name": c["nameHe"],
            }
            for i, c in enumerate(store_calendars)
        ],
    }

    faq = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {"@type": "Answer", "text": a},
            }
            for q, a in HUB_FAQ
        ],
    }

    jsonld = json.dumps(item_list, ensure_ascii=False, indent=2)
    faq_ld = json.dumps(faq, ensure_ascii=False, indent=2)

    return f"""\
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />

<title>{title}</title>
<meta name="description" content="{description}" />
<link rel="canonical" href="{canonical}" />
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large" />

<link rel="alternate" hreflang="he" href="{canonical}" />
<link rel="alternate" hreflang="x-default" href="{canonical}" />

<meta property="og:type" content="website" />
<meta property="og:site_name" content="Yogev Marom" />
<meta property="og:url" content="{canonical}" />
<meta property="og:title" content="{title}" />
<meta property="og:description" content="{description}" />
<meta property="og:image" content="{OG_IMAGE}" />
<meta property="og:image:width" content="1200" />
<meta property="og:image:height" content="630" />
<meta property="og:image:alt" content="{title}" />
<meta property="og:locale" content="he_IL" />

<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="{title}" />
<meta name="twitter:description" content="{description}" />
<meta name="twitter:image" content="{OG_IMAGE}" />

<script type="application/ld+json">
{jsonld}
</script>
<script type="application/ld+json">
{faq_ld}
</script>

<style>{tailwind_css}</style>
<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#FAF8F4}}</style>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300..900;1,9..144,300..900&family=Heebo:wght@400;500;600&display=swap" rel="stylesheet">"""


def make_hub_page(store_calendars: list[dict], tailwind_css: str, today: date) -> str:
    """Self-contained, RTL hub page with the month-by-month sale calendar."""
    head = make_hub_head(tailwind_css, store_calendars)
    nav = make_store_nav(STORES, current_slug=None)
    cur_m = today.month

    # ── "What's on sale this month" banner ──────────────────────────────────
    on_sale = [c for c in store_calendars if cur_m in c["good_months"]]
    if on_sale:
        names = ", ".join(c["nameHe"] for c in on_sale)
        banner_text = f"החודש ({MONTH_NUM_HE[cur_m]}) בסייל: <strong>{names}</strong>."
        banner_bg, banner_bd, banner_fg = "#ECFDF5", "#A7F3D0", "#047857"
    else:
        # No store in a window now — point to the nearest upcoming window per store.
        nexts = {}
        for c in store_calendars:
            nm = c["next_window_month"]
            if nm:
                nexts.setdefault(nm, []).append(c["nameHe"])
        if nexts:
            soonest = min(nexts, key=lambda m: (m - cur_m) % 12)
            who = ", ".join(nexts[soonest])
            banner_text = (f"החודש ({MONTH_NUM_HE[cur_m]}) אין סייל גדול. "
                           f"הסייל הקרוב — ב{MONTH_NUM_HE[soonest]}: <strong>{who}</strong>.")
        else:
            banner_text = f"החודש ({MONTH_NUM_HE[cur_m]}) אין סייל גדול בולט."
        banner_bg, banner_bd, banner_fg = "#FEF3C7", "#FDE68A", "#92400E"

    # ── Calendar grid (RTL table: month stub col sticky-right + 6 equal store cols)
    # In direction:rtl, the first <th> in HTML renders on the right.
    # Month col is first in HTML → sticks to the right edge (correct for Hebrew RTL).
    # Store columns fill the remaining width equally via table-layout:fixed.
    head_cells = "".join(
        f'<th style="padding:14px 8px 12px;font-size:12px;font-weight:700;'
        f'font-family:ui-monospace,monospace;letter-spacing:.04em;'
        f'color:#16161A;border-bottom:2px solid #D9D5CC;text-align:center;">'
        f'{c["nameEn"].upper()}'
        + (f'<div style="font-size:10px;font-weight:500;color:#9A9994;margin-top:4px;'
           f'letter-spacing:0;font-family:\'Heebo\',sans-serif;">עד {c["real_flat"]}%</div>'
           if c["real_flat"] else "")
        + "</th>"
        for c in store_calendars
    )
    rows = []
    for m in range(1, 13):
        is_cur = m == cur_m
        row_bg = "background:#FFFBEB;" if is_cur else ""
        cur_mark = " ●" if is_cur else ""
        month_cell = (
            f'<th style="padding:10px 16px;text-align:right;white-space:nowrap;'
            f'font-size:13px;font-weight:600;'
            f'color:{"#92400E" if is_cur else "#52525B"};'
            f'border-bottom:1px solid #ECE9E2;position:sticky;right:0;'
            f'background:{"#FFFBEB" if is_cur else "#FAF8F4"};'
            f'box-shadow:-2px 0 4px rgba(0,0,0,0.04);">'
            f'{MONTH_NUM_HE[m]}{cur_mark}</th>'
        )
        cells = "".join(
            _calendar_cell(c["months"].get(m), is_cur) for c in store_calendars
        )
        rows.append(f'<tr style="{row_bg}">{month_cell}{cells}</tr>')

    legend = (
        '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;'
        'margin:0 0 14px 0;font-size:11px;color:#52525B;font-family:\'Heebo\',sans-serif;">'
        '<span style="font-size:10px;letter-spacing:.08em;text-transform:uppercase;'
        'color:#9A9994;font-family:ui-monospace,monospace;">הנחה פלאט:</span>'
        '<span style="display:inline-flex;align-items:center;gap:5px;">'
        '<span style="width:14px;height:14px;border-radius:4px;background:#B91C1C;display:inline-block;"></span>50%+</span>'
        '<span style="display:inline-flex;align-items:center;gap:5px;">'
        '<span style="width:14px;height:14px;border-radius:4px;background:#1F3A8A;display:inline-block;"></span>40–49%</span>'
        '<span style="display:inline-flex;align-items:center;gap:5px;">'
        '<span style="width:14px;height:14px;border-radius:4px;background:#A8A29E;display:inline-block;"></span>&lt;40%</span>'
        '<span style="color:#D9D5CC;margin:0 2px;">|</span>'
        '<span style="display:inline-flex;align-items:center;gap:5px;">'
        '<span style="width:14px;height:14px;border-radius:4px;background:#F4F4F5;'
        'border:1px solid #D4D4D8;display:inline-block;"></span>עד X% (תקרה בלבד)</span>'
        '</div>'
    )

    grid = (
        f'{legend}'
        '<div style="overflow-x:auto;border:1px solid #D9D5CC;border-radius:10px;background:#fff;">'
        '<table style="border-collapse:collapse;width:100%;min-width:580px;'
        'direction:rtl;table-layout:fixed;">'
        '<thead><tr>'
        '<th style="width:96px;padding:14px 16px 12px;text-align:right;'
        'font-size:10px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;'
        'color:#9A9994;font-family:ui-monospace,monospace;border-bottom:2px solid #D9D5CC;'
        'position:sticky;right:0;background:#FAF8F4;'
        'box-shadow:-2px 0 4px rgba(0,0,0,0.04);">חודש</th>'
        f'{head_cells}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table></div>'
    )

    # ── Store cards ──────────────────────────────────────────────────────────
    cards = []
    for c in store_calendars:
        flat_str = (f'<span style="font-family:ui-monospace,monospace;font-weight:700;'
                    f'color:#1F3A8A;">עד {c["real_flat"]}% פלאט</span>'
                    if c["real_flat"] else "")
        cards.append(
            f'<a href="{c["slug"]}.html" '
            f'style="display:block;text-decoration:none;background:#fff;'
            f'border:1px solid #D9D5CC;border-radius:10px;padding:16px 18px;transition:border-color .15s;" '
            f'onmouseover="this.style.borderColor=\'#B91C1C\'" '
            f'onmouseout="this.style.borderColor=\'#D9D5CC\'">'
            f'<div style="font-size:16px;font-weight:600;color:#16161A;margin-bottom:4px;">{c["nameHe"]}</div>'
            f'<div style="font-size:13px;color:#52525B;line-height:1.6;margin-bottom:8px;">{c["insight"]}</div>'
            f'<div style="font-size:12px;">{flat_str}</div>'
            f'</a>'
        )
    cards_html = (
        '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));'
        f'gap:14px;margin-top:16px;">{"".join(cards)}</div>'
    )

    # ── FAQ (visible) — same Q&A that backs the FAQPage schema in <head> ──────
    faq_rows = "".join(
        f'<div style="margin:0 0 18px;">'
        f'<h3 style="font-size:15px;font-weight:600;color:#16161A;margin:0 0 6px;">{q}</h3>'
        f'<p style="font-size:14px;color:#52525B;line-height:1.7;margin:0;">{a}</p>'
        f'</div>'
        for q, a in HUB_FAQ
    )

    # ── Assemble ─────────────────────────────────────────────────────────────
    body = f"""\
{nav}
<main style="max-width:1000px;margin:0 auto;padding:28px 20px 60px;direction:rtl;text-align:right;font-family:'Heebo',system-ui,sans-serif;">
  <p style="font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:#9A9994;font-family:ui-monospace,monospace;margin:0 0 8px;">לוח סיילים · ניתוח מבצעי SMS</p>
  <h1 style="font-size:28px;font-weight:600;color:#16161A;line-height:1.3;margin:0 0 12px;font-family:'Fraunces',serif;">{HUB["h1"]}</h1>
  <p style="font-size:15px;color:#52525B;line-height:1.7;margin:0 0 12px;max-width:680px;">{HUB["intro"]}</p>
  <p style="font-size:14px;color:#52525B;line-height:1.7;margin:0 0 18px;max-width:680px;">{HUB["keywords_lede"]}</p>

  <div style="background:{banner_bg};border:1px solid {banner_bd};border-radius:10px;padding:14px 18px;margin:0 0 26px;color:{banner_fg};font-size:14px;line-height:1.6;">{banner_text}</div>

  <h2 style="font-size:18px;font-weight:600;color:#16161A;margin:0 0 14px;">לוח השנה — מתי כל רשת בסייל</h2>
  {grid}

  <h2 style="font-size:18px;font-weight:600;color:#16161A;margin:30px 0 4px;">כל הרשתות</h2>
  {cards_html}

  <h2 style="font-size:18px;font-weight:600;color:#16161A;margin:34px 0 14px;">שאלות נפוצות</h2>
  {faq_rows}

  <p style="font-size:12px;color:#9A9994;margin-top:36px;line-height:1.7;border-top:1px solid #ECE9E2;padding-top:16px;">
    מבוסס על ניתוח אוטומטי של היסטוריית מבצעי ה-SMS של כל רשת. ההנחות הן עומק ההנחה הגורפת שנצפתה בפועל, לא תקרת הפרסום. עודכן לאחרונה: {TODAY}.<br>
    מאת <a href="https://www.yogevmarom.com" style="color:#B91C1C;text-decoration:none;">Yogev Marom</a>.
  </p>
</main>"""

    return (
        f'<!doctype html>\n<html lang="he" dir="rtl">\n<head>\n{head}\n</head>\n'
        f'<body>\n{body}\n</body>\n</html>'
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    filter_slugs = set(sys.argv[1:]) if len(sys.argv) > 1 else set()
    stores = [s for s in STORES if not filter_slugs or s["slug"] in filter_slugs]

    PUBLISHED.mkdir(exist_ok=True)

    print("  compiling Tailwind CSS...")
    tailwind_css = compile_tailwind_css()
    print(f"  Tailwind CSS: {len(tailwind_css) // 1024} KB inlined")

    manifest_stores = []
    store_calendars = []

    for store in stores:
        slug = store["slug"]
        json_path = OUTPUT / store["json"]
        html_path = OUTPUT / store["html"]

        if not json_path.exists():
            print(f"  SKIP {slug} — {store['json']} not found (run extract.py first)")
            continue
        if not html_path.exists():
            print(f"  SKIP {slug} — {store['html']} not found (run build_artifact.py first)")
            continue

        data = json.load(open(json_path, encoding="utf-8"))
        raw_html = html_path.read_text(encoding="utf-8")

        body_insight = derive_body_insight(data, store["nameHe"])
        seo_head = make_seo_head(store, tailwind_css)
        intro = make_intro_block(store, body_insight)
        store_nav = make_store_nav(STORES, slug)
        final_html = process_html(raw_html, seo_head, intro, store_nav)

        out_path = PUBLISHED / f"{slug}.html"
        out_path.write_text(final_html, encoding="utf-8")
        print(f"  -> published/{slug}.html  ({len(final_html) // 1024} KB)")

        store_calendars.append(build_calendar_row(store, data))

        manifest_stores.append({
            "slug": slug,
            "file": f"{slug}.html",
            "nameHe": store["nameHe"],
            "nameEn": store["nameEn"],
            "title": store["title"],
            "description": store["description"],
            "insight": store["insight"],
            "updated": TODAY,
        })

    manifest = {
        "tool": "sale-tracker",
        "updated": TODAY,
        "hub": {
            "h1": "מתי הכי כדאי לקנות?",
            "intro": "ניתוח של מאות מבצעי SMS מהרשתות הגדולות — מתי באמת משתלם לקנות.",
        },
        "stores": manifest_stores,
    }
    (PUBLISHED / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  -> published/manifest.json  ({len(manifest_stores)} stores)")

    # ── Hub page: cross-store sale calendar ──────────────────────────────────
    # Only build the full hub when publishing the complete set (no slug filter),
    # so `python publish.py story` doesn't emit a partial calendar.
    if store_calendars and not filter_slugs:
        hub_html = make_hub_page(store_calendars, tailwind_css, date.today())
        (PUBLISHED / "index.html").write_text(hub_html, encoding="utf-8")
        print(f"  -> published/index.html  (hub, {len(hub_html) // 1024} KB, "
              f"{len(store_calendars)} stores)")


if __name__ == "__main__":
    main()
