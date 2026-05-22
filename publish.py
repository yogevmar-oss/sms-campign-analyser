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
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "output"
PUBLISHED = HERE / "published"
BASE_URL = "https://www.yogevmarom.com/tools/sale-tracker"
OG_IMAGE = "https://www.yogevmarom.com/uploads/og-cover-v2.jpg"
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
        "slug": "soho",
        "json": "SOHO_v3.json",
        "html": "SOHO_explorer.html",
        "nameHe": "סוהו",
        "nameEn": "SOHO",
        "title": "מתי הכי כדאי לקנות בסוהו? — לוח מבצעים והנחות",
        "description": "ניתוח מבצעי ה-SMS של סוהו: חנות קלירנס עונתית — הנחות של 70% מרוכזות בעיקר באפריל ובדצמבר.",
        "insight": "הכי משתלם: אפריל ודצמבר — 70% קלירנס",
        "h1": "מתי הכי כדאי לקנות בסוהו?",
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


# ── Body insight (longer sentence for the visible HTML block) ─────────────────

def derive_body_insight(data: dict, name_he: str) -> str:
    """Return a Hebrew sentence surfacing the key buying signal — shown as body
    text above the dashboard so Google can index it."""
    ss = data.get("shopping_summary", {})
    pattern = ss.get("store_pattern", "normal")
    windows = ss.get("buy_windows", [])
    peak_months = ss.get("peak_months", [])
    routine = ss.get("routine_baseline_pct")

    if pattern == "seasonal" and peak_months:
        months_he = " ו".join(MONTH_HE.get(m, m) for m in peak_months[:2])
        max_pct = windows[0]["max_pct"] if windows else "?"
        return (f"ב-{name_he} ההנחות מגיעות לשיא ב{months_he} — עד {max_pct}% הנחה. "
                f"מחוץ לתקופות אלו, כמעט ולא יוצאים מבצעים.")

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
                    f"עד {w['max_pct']}% הנחה.{routine_str}")
        return (f"הזמן הכי טוב לקנות ב-{name_he}: {label_he} — "
                f"עד {w['max_pct']}% הנחה.{routine_str}")

    return f"ניתוח היסטוריית מבצעי ה-SMS של {name_he}."


# ── SEO head (follows HANDOFF.md §2b exactly) ─────────────────────────────────

def make_seo_head(store: dict) -> str:
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

<style>*{{box-sizing:border-box;margin:0;padding:0}}body{{background:#FAF8F4}}#root{{direction:ltr}}</style>
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

def process_html(raw: str, seo_head: str, intro: str) -> str:
    # Set lang + dir on <html>
    html = re.sub(r"<html[^>]*>", '<html lang="he" dir="rtl">', raw, count=1)
    # Replace entire <head> block
    html = re.sub(
        r"<head>.*?</head>",
        f"<head>\n{seo_head}\n</head>",
        html, count=1, flags=re.DOTALL,
    )
    # Inject intro block immediately after <body>
    html = re.sub(r"<body>", f"<body>\n{intro}\n", html, count=1)
    return html


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    filter_slugs = set(sys.argv[1:]) if len(sys.argv) > 1 else set()
    stores = [s for s in STORES if not filter_slugs or s["slug"] in filter_slugs]

    PUBLISHED.mkdir(exist_ok=True)
    manifest_stores = []

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
        seo_head = make_seo_head(store)
        intro = make_intro_block(store, body_insight)
        final_html = process_html(raw_html, seo_head, intro)

        out_path = PUBLISHED / f"{slug}.html"
        out_path.write_text(final_html, encoding="utf-8")
        print(f"  -> published/{slug}.html  ({len(final_html) // 1024} KB)")

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


if __name__ == "__main__":
    main()
