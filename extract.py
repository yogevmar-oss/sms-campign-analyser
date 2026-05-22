"""
SMS discount extractor.

Reads an iPhone SMS-export PDF (one retail store, one or more years of messages)
and outputs structured JSON describing each promotional message.

Usage:
    python extract.py --pdf PATH --store NAME --export-date YYYY-MM-DD [--extra-brands B1,B2,...] [--out PATH]

Output schema (one record per SMS) — see references/schema.md for full spec.
"""

import argparse
import json
import logging
import re
import sys
import hashlib
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pypdf

# Import sibling module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from brands import detect_brands, discover_brand_candidates  # noqa: E402
from analyze import (  # noqa: E402
    assign_campaign_ids, build_campaigns, compute_sale_moments,
    compute_brand_recommendations, compute_monthly, compute_verdict,
    compute_shopping_summary,
)

logging.getLogger("pypdf").setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}
WEEKDAY_MAP = {  # Monday=0 .. Sunday=6
    "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
    "Friday": 4, "Saturday": 5, "Sunday": 6,
}

# iPhone SMS date header formats:
#   "Tue, 3 Mar at 11:47"           — full date, no year (within last ~12 months)
#   "8 Apr 2025 at 19:22"           — full date WITH year (older than ~12 months)
#   "Saturday 21:41"                — weekday + time (recent, within ~1 week)
#   "Today 20:12"                   — same day as export
#   "Yesterday 14:00"               — day before export
HEADER_FULL = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+(\d{1,2})\s+([A-Z][a-z]{2})\s+at\s+(\d{1,2}):(\d{2})\s*$"
)
HEADER_FULL_WITH_YEAR = re.compile(
    r"^(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})\s+at\s+(\d{1,2}):(\d{2})\s*$"
)
HEADER_WEEKDAY = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{1,2}):(\d{2})\s*$"
)
HEADER_TODAY = re.compile(r"^(Today|Yesterday)\s+(\d{1,2}):(\d{2})\s*$")


def _resolve_full_date(day: int, month: int, hour: int, minute: int, export_date: date) -> datetime:
    """Choose year by walking backward from export_date until (year, month, day) <= export_date."""
    year = export_date.year
    for _ in range(5):  # safety bound
        try:
            cand = datetime(year, month, day, hour, minute)
        except ValueError:
            year -= 1
            continue
        if cand.date() <= export_date:
            return cand
        year -= 1
    # Fallback: just use export_date.year (shouldn't reach here)
    return datetime(export_date.year, month, day, hour, minute)


def _resolve_weekday(weekday_name: str, hour: int, minute: int, export_date: date) -> datetime:
    """Find most recent past date matching this weekday (within 7 days of export_date)."""
    target_wd = WEEKDAY_MAP[weekday_name]
    for delta in range(0, 8):
        cand = export_date - timedelta(days=delta)
        if cand.weekday() == target_wd:
            return datetime(cand.year, cand.month, cand.day, hour, minute)
    # shouldn't happen
    return datetime(export_date.year, export_date.month, export_date.day, hour, minute)


def parse_date_header(line: str, export_date: date) -> Optional[datetime]:
    line = line.strip()
    if m := HEADER_FULL.match(line):
        day, mon, hour, minute = int(m.group(1)), MONTH_MAP[m.group(2)], int(m.group(3)), int(m.group(4))
        return _resolve_full_date(day, mon, hour, minute, export_date)
    if m := HEADER_FULL_WITH_YEAR.match(line):
        # Explicit year — no inference needed. Used by iPhone for messages older than ~12 months.
        day, mon, year, hour, minute = (int(m.group(1)), MONTH_MAP[m.group(2)],
                                         int(m.group(3)), int(m.group(4)), int(m.group(5)))
        try:
            return datetime(year, mon, day, hour, minute)
        except ValueError:
            return None
    if m := HEADER_WEEKDAY.match(line):
        return _resolve_weekday(m.group(1), int(m.group(2)), int(m.group(3)), export_date)
    if m := HEADER_TODAY.match(line):
        which, hour, minute = m.group(1), int(m.group(2)), int(m.group(3))
        target = export_date if which == "Today" else export_date - timedelta(days=1)
        return datetime(target.year, target.month, target.day, hour, minute)
    return None


# ---------------------------------------------------------------------------
# Expiration parsing — "עד 17.5 בחצות" or "עד 18.12.25 בחצות" style
# ---------------------------------------------------------------------------

# Match any date — D.M or D.M.YY or D.M.YYYY — and capture the parts.
# pypdf RTL visual extraction can place "בחצות" (at midnight) before, after, or
# between the date and the noise around it ("ה.18.12.25-, בחצות"), so we don't
# require strict adjacency in the regex; we just scan for dates and then check
# whether an expiry cue ("בחצות" / "עד היום" / "עד מחר") sits within 60 chars.
DATE_PATTERN = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?")
_EXPIRY_CUE = re.compile(r"בחצות|עד\s+היום|עד\s+מחר|תקף\s+עד")


def parse_expiration(body: str, sent_at: datetime) -> Optional[datetime]:
    for m in DATE_PATTERN.finditer(body):
        # Establish a context window around the date and look for an expiry cue.
        ctx_start = max(0, m.start() - 60)
        ctx_end = min(len(body), m.end() + 60)
        if not _EXPIRY_CUE.search(body[ctx_start:ctx_end]):
            continue
        day, month = int(m.group(1)), int(m.group(2))
        year_str = m.group(3)
        if year_str:
            year = int(year_str)
            if year < 100:
                year += 2000
        else:
            # Bare D.M — default to sent_at's year, rolling forward if month
            # appears to wrap (e.g. SMS sent in Dec referencing 5.1 → next year).
            year = sent_at.year
            if month < sent_at.month - 6:
                year += 1
        try:
            return datetime(year, month, day, 23, 59)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Discount parsing
# ---------------------------------------------------------------------------

# Match any number+% (covers both English "20% OFF" and Hebrew "20% הנחה" in any visual order)
PCT_PATTERN = re.compile(r"(\d{1,2})\s*%")

# Hebrew keyword presence checks (visual-order safe — we just look for substring)
HEB = {
    "discount": "הנחה",          # "discount"
    "up_to": "עד",                # "up to"  (also appears in expiry — we filter by context)
    "extra": "אקסטרה",            # "extra"
    "site": "באתר",               # "in the site" (sitewide indicator)
    "brand": "המותג",             # "the brand <X>"
    "collection": "קולקציה",       # "collection" (often marketing-only when no %)
    "outlet": "אאוטלט",            # "outlet"
    "category_general": "קטגוריית", # category
    "stack_no": "ללא כפל",          # "without stacking" — can't combine with other promos
    "stack_yes": "כולל כפל",        # "with stacking" — CAN combine, real depth
    "vip_dreamcard": "DREAMCARD",
    "vip_word": "VIP",
    "new_member": "מצטרפים חדשים",
    "coupon": "קוד קופון",
    "code_simple": "קוד:",
    "midnight": "בחצות",
    "flash_he": "פלאש סייל",
    "flash_en": "FLASH SALE",
    "mid_season_he": "מיד סיזן",
    "mid_season_en": "MID SEASON",
    "white_days_en": "WHITE DAYS",
    "independence": "יום העצמאות",
    "passover": "פסח",
    "holiday": "החג",
    "kids": "ילדים",
    "women": "נשים",
    "men": "גברים",
    "swimwear": "בגדי ים",
    "sunglasses": "משקפי שמש",
    "fragrance": "בשמים",
    "sneakers_he": "סניקרס",
    "sandals_he": "סנדלים",
    "kippas_he": "כפכפים",
    "sportswear": "ספורט",
    "winter": "חורף",
    "summer": "קיץ",
    # Item categories — Story-style category-scoped promos ("20% off jeans")
    "hats": "כובעים",
    "jeans": "ג׳ינס",          # with gershayim U+05F3
    "jeans_alt": "ג'ינס",       # with apostrophe U+0027
    "dresses": "שמלות",
    "shirts": "חולצות",
    "sweater": "סוודר",
    "sweaters": "סוודרים",
    "coats": "מעילים",
    "coat": "מעיל",
    "shorts_he": "שורטס",
    "tshirts_he": "טישירטס",
    "resort": "ריזורט",
    "basics_he": "בייסיקס",
    "eyewear_he": "משקפי",
    "autumn_he": "סתיו",          # autumn — H&M uses "פריטי סתיו" (autumn items)
    "home_he": "הום",             # H&M Home line — appears as "כולל הום" / "כולל HOME"
}


def _has(body: str, key: str) -> bool:
    return HEB[key] in body


# Hebrew alphabet Unicode range — used for word-boundary checks since \b in Python's
# re module only treats ASCII letters as word characters.
HEBREW_LETTER = r"[\u05D0-\u05EA]"


def _has_hebrew_word(body: str, word: str) -> bool:
    """
    Hebrew word-boundary match. The matched word must not be preceded or followed
    by another Hebrew letter, otherwise "פורים" matches inside "אפורים" (gray plural),
    leading to false Purim event tags. Punctuation, whitespace, Latin, and digits
    all count as word boundaries; only Hebrew letters do not.
    """
    pattern = r"(?<!" + HEBREW_LETTER + r")" + re.escape(word) + r"(?!" + HEBREW_LETTER + r")"
    return bool(re.search(pattern, body))


def _has_phrase(body: str, phrase: str) -> bool:
    """
    Check whether a multi-word Hebrew phrase appears in the body, in either word order.
    pypdf extracts Hebrew text in visual (reversed) word order, so the logical phrase
    "יום העצמאות" appears as "העצמאות יום" in the extracted text.
    """
    if phrase in body:
        return True
    words = phrase.split()
    if len(words) > 1:
        reversed_phrase = " ".join(reversed(words))
        if reversed_phrase in body:
            return True
    return False


def _strip_expiry_for_discount_analysis(body: str) -> str:
    """Remove the disclaimer/expiry line so 'עד 17.5 בחצות' doesn't confuse 'up_to' detection.

    In pypdf visual order this appears reversed — e.g. 'בחצות 17.5 עד' — so we strip from
    the 'עד' that precedes 'בחצות' through end of message. We try both orderings.
    """
    out = body
    # Visual-order pattern: "בחצות D.D ... עד" (the disclaimer line). Strip from עד onward.
    out = re.sub(r"בחצות\s+\d{1,2}\.\d{1,2}[^\n]*", "", out)
    # Also strip any remaining "עד \d+\.\d+" tail.
    out = re.sub(r"עד\s+\d{1,2}\.\d{1,2}[^\n]*", "", out)
    return out


def _effective_pct(base: Optional[int], extra: Optional[int]) -> Optional[int]:
    """
    Compound discount math: 70% base + 20% extra is 76% off, NOT 90%.
    Formula: 1 - (1 - base) * (1 - extra)
    For non-stacked deals (extra is None), returns base.
    """
    if base is None:
        return None
    if extra is None:
        return base
    return round(100 * (1 - (1 - base / 100) * (1 - extra / 100)))


def _has_discount_context(body: str) -> bool:
    """
    Decide whether the message is actually announcing a promo, vs just using a %
    metaphorically ("a summer wardrobe is 90% swimwear"). Without one of these
    markers present, any X% in the body is treated as descriptive, not a discount.

    Note: caller is responsible for NFKD-normalizing Unicode-fancy text first
    (so "𝐒𝐀𝐋𝐄" becomes "SALE" before matching).
    """
    # Hebrew discount markers
    hebrew_markers = ["הנחה", "אקסטרה", "מבצע", "כפל", "חיסכון", "סייל", "פלאש"]
    if any(kw in body for kw in hebrew_markers):
        return True
    # English markers — word-boundary so "OFFICIAL" doesn't trip "OFF"
    upper = body.upper()
    english_markers = [r"\bOFF\b", r"\bSALE\b", r"\bDEAL\b", r"\bDISCOUNT\b",
                       r"\bPROMO\b", r"\bFLASH\b", r"\bCYBER\b", r"\bBLACK FRIDAY\b"]
    for pat in english_markers:
        if re.search(pat, upper):
            return True
    # "ב-X%" / "ב X%" / "%X ב" / "%X-ב" — Hebrew "at" preposition with percentage.
    # Used for prices like "פריטים ב-30%" (items at 30%).
    if re.search(r"ב[-\s]+\d{1,3}\s*%", body):
        return True
    if re.search(r"%\s*\d{1,3}\s*[-\s]ב\b", body):
        return True
    # "על X%" / "X% על" — Hebrew "X% on [items]". Story-style boutique copy uses this
    # in place of the more formal "X% הנחה". Word-boundary lookarounds prevent matching
    # inside "מעל" (above) or "יעל" (the name Yael).
    if re.search(r"\bעל\s+\d{1,3}\s*%", body):
        return True
    if re.search(r"\d{1,3}\s*%\s+על\b", body):
        return True
    return False


def parse_discount(body: str) -> dict:
    """
    Extract discount info from a message body.

    Returns dict with: has_discount, discount_pct, discount_kind, stacked_extra_pct,
    stack_on_markdown, is_range.
    """
    EMPTY = {
        "has_discount": False,
        "discount_pct": None,
        "discount_kind": None,
        "stacked_extra_pct": None,
        "effective_pct": None,
        "stack_on_markdown": False,
        "is_range": False,
    }

    # Guard: without a discount-context word, any % in the message is descriptive,
    # not promotional. Prevents "מלתחה לקיץ זה 90% בגדי ים" (summer wardrobe is 90%
    # swimwear) from being parsed as a 90% off promo.
    if not _has_discount_context(body):
        return EMPTY

    headline = _strip_expiry_for_discount_analysis(body)

    pct_matches = PCT_PATTERN.findall(headline)
    if not pct_matches:
        return EMPTY

    nums = [int(n) for n in pct_matches]
    # Sort descending — the headline number is usually the largest (e.g., "70% + 20% extra")
    primary = max(nums)

    # Range pattern: "X%-Y% OFF" or "X%-Y% הנחה" — depth varies item-to-item, semantically up_to
    range_pattern = re.compile(r"(\d{1,2})\s*%\s*-\s*(\d{1,2})\s*%")
    range_match = range_pattern.search(headline)
    is_range = bool(range_match)
    if is_range:
        lo, hi = int(range_match.group(1)), int(range_match.group(2))
        primary = max(lo, hi)

    # Stacked: presence of "אקסטרה" (extra) → it's a base% + extra%
    is_stacked = _has(headline, "extra")
    stacked_extra = None
    if is_stacked and len(nums) >= 2:
        # The "extra" amount is the smaller one; primary is the headline ceiling
        stacked_extra = min(nums)
        # Re-establish primary as the ceiling (not the range max from above, which would conflict)
        primary = max(n for n in nums if n != stacked_extra) if len(set(nums)) > 1 else primary

    # "Up to" detection: the Hebrew word "עד" must appear IMMEDIATELY after the % in
    # visual scan order. That's how the pypdf-extracted text represents logical
    # "עד X% הנחה" (RTL → visual "הנחה X% עד"). A loose check on "עד" anywhere in
    # the message would falsely flag flat promos that just have "until Saturday"
    # phrasing ("עד יום שבת").
    up_to_pattern = re.compile(r"\d{1,2}\s*%\s+עד\b")
    is_up_to = bool(up_to_pattern.search(headline))

    # Determine kind. Order matters: stacked → range → up_to → flat
    if is_stacked:
        kind = "stacked"
    elif is_range or is_up_to:
        kind = "up_to"
    else:
        kind = "flat"

    # Real markdown stacking — "כולל כפל מבצעים" (allowed to stack on existing reductions)
    stack_on_md = _has(body, "stack_yes")

    return {
        "has_discount": True,
        "discount_pct": primary,
        "discount_kind": kind,
        "stacked_extra_pct": stacked_extra,
        "effective_pct": _effective_pct(primary, stacked_extra),
        "stack_on_markdown": stack_on_md,
        "is_range": is_range,
    }


# ---------------------------------------------------------------------------
# Scope, gating, event tags
# ---------------------------------------------------------------------------

def detect_scope(body: str, brands: list[str]) -> str:
    if _has(body, "outlet") or "OUTLET" in body.upper():
        return "outlet"
    # Category-only (no brands): "20% OFF SNEAKERS & SPORTSWEAR" or "30% הנחה על כל הג'ינסים"
    cat_keywords_he = [
        "sneakers_he", "sandals_he", "sunglasses", "swimwear", "fragrance",
        "hats", "jeans", "jeans_alt", "dresses", "shirts",
        "sweater", "sweaters", "coats", "coat", "shorts_he", "tshirts_he",
        "resort", "basics_he",
    ]
    has_category = any(_has(body, k) for k in cat_keywords_he) or any(
        kw in body.upper() for kw in ("SNEAKERS", "SPORTSWEAR", "SANDALS", "SWIMWEAR",
                                       "HATS", "JEANS", "DRESSES", "T-SHIRTS", "SHORTS")
    )
    if has_category and len(brands) <= 1:
        return "category"
    if len(brands) == 0:
        if _has(body, "site"):
            return "sitewide"
        return "sitewide"  # default for no-brand promos with a %
    if len(brands) == 1:
        return "single_brand"
    return "multi_brand"


def detect_categories(body: str) -> list[str]:
    cats = []
    cat_map_he = {
        "sneakers": ("sneakers_he", "SNEAKERS"),
        "sandals": ("sandals_he", "SANDALS"),
        "swimwear": ("swimwear", "SWIMWEAR"),
        "sunglasses": ("sunglasses", "SUNGLASSES"),
        "fragrance": ("fragrance", "FRAGRANCE"),
        "sportswear": ("sportswear", "SPORTSWEAR"),
        "hats": ("hats", "HATS"),
        "jeans": ("jeans", "JEANS"),
        "dresses": ("dresses", "DRESSES"),
        "shirts": ("shirts", "SHIRTS"),
        "sweaters": ("sweaters", "SWEATERS"),
        "coats": ("coats", "COATS"),
        "shorts": ("shorts_he", "SHORTS"),
        "tshirts": ("tshirts_he", "T-SHIRTS"),
        "resort": ("resort", "RESORT"),
        "basics": ("basics_he", "BASICS"),
        "autumn": ("autumn_he", "AUTUMN"),
        "home": ("home_he", "HOME"),
        "winter": ("winter", "WINTER"),
        "summer": ("summer", "SUMMER"),
        "kids": ("kids", "KIDS"),
        "women": ("women", "WOMEN"),
        "men": ("men", "MEN"),
        "denim": ("jeans_alt", "DENIM"),  # English "DENIM" treated as a jeans-adjacent category
    }
    upper = body.upper()
    for cat, (he_key, en_kw) in cat_map_he.items():
        # jeans needs both apostrophe variants
        if cat == "jeans":
            if _has(body, "jeans") or _has(body, "jeans_alt") or "JEANS" in upper:
                cats.append(cat)
                continue
        # basics: also catch English "BASIC" singular (H&M uses "מחלקות ה- BASIC")
        if cat == "basics":
            if _has(body, "basics_he") or "BASICS" in upper or re.search(r"\bBASIC\b", upper):
                cats.append(cat)
                continue
        if _has(body, he_key) or en_kw in upper:
            cats.append(cat)
    return cats


def detect_gating(body: str) -> tuple[str, Optional[str], Optional[str]]:
    """Returns (gating_type, coupon_code, gating_label)."""
    coupon_code = _extract_coupon_code(body)

    if _has(body, "vip_dreamcard"):
        return "vip_card", coupon_code, "DREAMCARD VIP"
    if _has(body, "vip_word"):
        return "vip_card", coupon_code, "VIP"
    if _has_phrase(body, "מצטרפים חדשים"):
        return "new_member", coupon_code, None
    if coupon_code:
        return "coupon", coupon_code, None
    return "open", None, None


# Tokens that look code-shaped but are never coupon codes.
_COUPON_STOPWORDS = {
    "SHOP", "NOW", "MORE", "OFF", "NEW", "DROP", "BUY", "READY", "TAKE", "LATER", "REGRET",
    "VIP", "DREAMCARD", "PARIS", "SALE", "DAY", "DAYS", "AND", "OR", "THE",
    "NIKE", "ADIDAS", "MANGO", "TERMINAL", "BILLABONG", "AMERICAN", "EAGLE", "BALANCE",
    "JACK", "JONES", "ESTEE", "LAUDER", "OLIERE", "TKEES", "PUMA", "ASICS", "CONVERSE",
    "TEVA", "NAOT", "HAVAIANAS", "BIRKENSTOCK", "GUCCI", "OAKLEY", "PRADA", "ARMANI",
    "VALENTINO", "LANCOME", "YVES", "SAINT", "LAURENT", "RAY", "BAN", "SUNGLASS", "HUT",
    "MAC", "AERIE", "FOX", "COLUMBIA", "ADAH", "ITAY", "BRANDS", "BOBBI", "BROWN", "BORWN",
    "CLINIQUE", "ESTÉE", "QUESTION", "MARK", "ESSENTIALS", "WOMEN", "MEN", "KIDS",
    "SNEAKERS", "SPORTSWEAR", "SUMMER", "WINTER", "WHITE", "ARE", "COMING", "CALLING",
    "SCENT", "TX", "GIFT", "FREE", "ADISTAR", "CONTROL",
    "TEXT", "MESSAGE", "SMS", "HOLIDAY", "OUTDOOR", "LIFESTYLE",
}


def _extract_coupon_code(body: str) -> Optional[str]:
    """
    Find a coupon code in the body. Works regardless of whether the code appears before
    or after the Hebrew "קוד" / "קוד קופון" / "למימוש" / English "code" keyword (RTL visual
    order flips this, and the code is often on the line BELOW the keyword).

    Three shapes recognized (in priority order):
      1. Hyphenated alphanumeric block — e.g. IL412-L4LZ-8GP4-DZ34, OCT-12YY-4B4B-WSSC
      2. Alphanumeric word starting with a letter — e.g. FREESHIP, AB12CD
      3. Pure numeric ≥11 digits — e.g. 00171091971365 (Israeli phones are 10 digits,
         so we exclude them by length floor)
    """
    # "למימוש" = "for redemption" — precedes coupons in H&M-style format
    keyword_re = re.compile(r"(?:קוד\s*קופון|קוד|למימוש|coupon|code)", re.IGNORECASE)
    hyphenated_re = re.compile(r"\b([A-Z0-9]{2,}(?:-[A-Z0-9]{2,}){1,})\b")
    alpha_re = re.compile(r"\b([A-Z][A-Z0-9]{3,19})\b")
    numeric_re = re.compile(r"(?<!\d)(\d{11,20})(?!\d)")

    lines = body.splitlines()
    candidates: list[tuple[int, str]] = []  # (priority, token); lower = better

    def _scan(line: str) -> None:
        for m in hyphenated_re.finditer(line):
            tok = m.group(1)
            if any(seg in _COUPON_STOPWORDS for seg in tok.split("-")):
                continue
            candidates.append((0, tok))
        for m in alpha_re.finditer(line):
            tok = m.group(1)
            if tok in _COUPON_STOPWORDS:
                continue
            has_digit = bool(re.search(r"\d", tok))
            candidates.append((1 if has_digit else 2, tok))
        for m in numeric_re.finditer(line):
            candidates.append((3, m.group(1)))

    for i, line in enumerate(lines):
        if not keyword_re.search(line):
            continue
        # Scan the keyword line itself
        _scan(line)
        # AND the next line — H&M splits the code onto its own line
        if i + 1 < len(lines):
            _scan(lines[i + 1])

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


def detect_event_tag(body: str) -> Optional[str]:
    upper = body.upper()
    if _has_phrase(body, "יום העצמאות"):
        return "independence_day"
    if _has_phrase(body, "חג הרווקים") or "SINGLES" in upper and "DAY" in upper:
        return "singles_day"
    if _has(body, "passover") or ("CHAG" in upper and ("PASSOVER" in upper or "PESACH" in upper)):
        return "passover"
    # Purim — use word-boundary check; "פורים" otherwise matches "אפורים" (gray, plural)
    if "PURIM" in upper or _has_hebrew_word(body, "פורים"):
        return "purim"
    if ("V-DAY" in upper or "VALENTINE" in upper or
        _has_hebrew_word(body, "ולנטיינס") or _has_hebrew_word(body, "ולנטיין")):
        return "valentines_day"
    if _has(body, "white_days_en") or "WHITE DAYS" in upper:
        return "white_sale"
    if _has_hebrew_word(body, "סיזן") or _has(body, "mid_season_en") or _has_phrase(body, "אמצע עונה") or _has_phrase(body, "עונה אמצע"):
        return "mid_season"
    # Flash sale — English "FLASH" / "פלאש סייל" — and Hebrew "נייט סייל" (night sale)
    # which H&M uses for short evening promos. Bundled here since it's the same shape.
    if _has_phrase(body, "פלאש סייל") or _has(body, "flash_en") or _has_phrase(body, "נייט סייל") or _has_phrase(body, "סייל נייט") or "NIGHT SALE" in upper:
        return "flash_sale"
    # Black Friday — English or Hebrew transliteration "בלאק פריידי" (visual: "פריידי בלאק")
    if "BLACK FRIDAY" in upper or _has_phrase(body, "בלאק פריידי") or _has_phrase(body, "פריידי בלאק"):
        return "black_friday"
    # Cyber Monday — English or Hebrew transliteration "סייבר מאנדיי" (visual flip: "מאנדיי סייבר")
    if "CYBER" in upper or _has_phrase(body, "סייבר מאנדיי") or _has_phrase(body, "מאנדיי סייבר"):
        return "cyber_monday"
    if "TERMINAL X DAY" in upper:
        return "store_day"  # store-branded event
    # TerminalX-specific November campaign (and a generic catch for other "Crazy <Month>")
    if "CRAZYNOVEMBER" in upper.replace(" ", "") or "טירוף נובמבר" in body or "נובמבר טירוף" in body:
        return "crazy_november"
    return None


def detect_free_shipping(body: str) -> dict:
    """
    Detect free-shipping promos. Stores often run these as parallel perks to %-off deals.

    A message counts as free-shipping if it has a "חינם" (free) token within 3 words of
    any token containing "משלוח" (shipping). This handles the natural variations:
        "משלוח חינם"                   — basic form
        "חינם משלוח"                   — RTL visual flip
        "משלוחי אקספרס חינם"           — plural construct + adjective between
        "חינם ומשלוח"                  — copula between
        "חינם המשלוחים"                — definite article prefix
        "ומשלוח חינם"                  — "and shipping is free"
    Excludes: "חינם קעקוע" (free tattoo), "חינם רכישה" (free with purchase) — no משלוח nearby.

    Returns has_free_shipping bool + optional NIS threshold.
    """
    tokens = re.split(r"\s+", body)
    chinam_idx = [i for i, t in enumerate(tokens) if "חינם" in t]
    mishloach_idx = [i for i, t in enumerate(tokens) if "משלוח" in t]

    has = False
    for ci in chinam_idx:
        for mi in mishloach_idx:
            if abs(ci - mi) <= 3:
                has = True
                break
        if has:
            break

    if not has:
        return {"has_free_shipping": False, "free_shipping_threshold_nis": None}

    # Threshold extraction. The "above ₪X" expression appears in several orders due to
    # logical-vs-visual RTL: "מעל ₪499" (logical) → "₪499 מעל" or "499 ₪ מעל" (visual).
    threshold = None
    threshold_patterns = [
        r"מעל\s*₪?\s*(\d{2,4})",
        r"(\d{2,4})\s*₪\s*מעל",
        r"₪\s*(\d{2,4})\s*מעל",
        r"מעל\s*(\d{2,4})\s*ש[״\"\u05F4]?ח",
        r"ש[״\"\u05F4]?ח\s*(\d{2,4})\s*מעל",
        r"(\d{2,4})\s*ש[״\"\u05F4]?ח\s*מעל",
    ]
    for pat in threshold_patterns:
        m = re.search(pat, body)
        if m:
            val = int(m.group(1))
            if 50 <= val <= 2000:
                threshold = val
                break

    return {"has_free_shipping": True, "free_shipping_threshold_nis": threshold}


def is_marketing_only(body: str, has_discount: bool) -> bool:
    """No discount AND content is just announcing a new collection / arrival."""
    if has_discount:
        return False
    # "קולקציה חדשה" / "נחתה באתר" / etc. — pure marketing
    marketing_cues = ["קולקציה", "נחתה", "דרופ חדש", "NEW DROP", "SUMMER IS CALLING", "WHITE DAYS ARE COMING"]
    return any(cue in body for cue in marketing_cues)


# ---------------------------------------------------------------------------
# Message segmentation
# ---------------------------------------------------------------------------

def segment_messages(raw_text: str, export_date: date) -> list[dict]:
    """
    Split full PDF text into individual messages.
    Each message has a date header line followed by body lines until the next header.
    Returns list of {"sent_at": datetime, "raw_text": str}.

    Dedupe note: iPhone SMS-export PDFs often repeat the entire conversation's text
    layer on every page (visual slicing only). We dedupe by (timestamp, body-hash)
    so the same message captured on N pages becomes one record.
    """
    lines = raw_text.splitlines()
    messages = []
    current_header_dt: Optional[datetime] = None
    current_body: list[str] = []

    def flush():
        if current_header_dt and current_body:
            body_text = "\n".join(current_body).strip()
            if body_text:
                messages.append({"sent_at": current_header_dt, "raw_text": body_text})

    for line in lines:
        dt = parse_date_header(line, export_date)
        if dt:
            flush()
            current_header_dt = dt
            current_body = []
        else:
            if current_header_dt:  # ignore lines before the first header
                current_body.append(line)
    flush()

    # Drop page-repetition duplicates: identical (timestamp, normalized-body) pairs.
    seen = set()
    deduped = []
    for m in messages:
        key = (
            m["sent_at"].isoformat(),
            hashlib.md5(re.sub(r"\s+", " ", m["raw_text"]).encode("utf-8")).hexdigest(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(m)
    return deduped


# ---------------------------------------------------------------------------
# Per-message processing
# ---------------------------------------------------------------------------

def process_message(msg: dict, extra_brands: set[str]) -> dict:
    body_original = msg["raw_text"]
    # NFKD normalization converts mathematical/styled Latin Unicode (𝐒𝐀𝐋𝐄, 𝘼𝙇𝙇𝙎𝘼𝙄𝙉𝙏𝙎)
    # to plain ASCII (SALE, ALLSAINTS). Hebrew is unaffected. All detection runs on the
    # normalized version so fancy Unicode brand names and event keywords get matched.
    body = unicodedata.normalize('NFKD', body_original)
    sent_at: datetime = msg["sent_at"]

    brands = detect_brands(body, extra_brands)
    discount = parse_discount(body)
    scope = detect_scope(body, brands)
    categories = detect_categories(body)
    gating, coupon, gating_label = detect_gating(body)
    event_tag = detect_event_tag(body)
    expiry = parse_expiration(body, sent_at)
    shipping = detect_free_shipping(body)

    duration_hours = None
    if expiry:
        duration_hours = round((expiry - sent_at).total_seconds() / 3600, 1)

    # brands_open_ended: "& MORE" in the brand list
    brands_open_ended = bool(re.search(r"&\s*MORE", body, flags=re.IGNORECASE))

    # is_flash_sale: redundant with event_tag but useful as a top-level signal
    is_flash = event_tag == "flash_sale"

    # Hash for de-duplication (same body within 24h is a near-duplicate)
    body_hash = hashlib.md5(re.sub(r"\s+", " ", body[:200]).encode("utf-8")).hexdigest()[:10]

    return {
        # provenance
        "sent_at": sent_at.isoformat(),
        "sent_date": sent_at.date().isoformat(),
        "expires_at": expiry.isoformat() if expiry else None,
        "duration_hours": duration_hours,
        "body_hash": body_hash,
        "raw_text": body_original,
        # discount
        "has_discount": discount["has_discount"],
        "discount_pct": discount["discount_pct"],
        "discount_kind": discount["discount_kind"],
        "stacked_extra_pct": discount["stacked_extra_pct"],
        "effective_pct": discount["effective_pct"],
        "stack_on_markdown": discount["stack_on_markdown"],
        "is_range": discount.get("is_range", False),
        # scope
        "scope": scope,
        "brands": brands,
        "brands_open_ended": brands_open_ended,
        "categories": categories,
        # gating
        "gating": gating,
        "coupon_code": coupon,
        "gating_label": gating_label,
        # perks
        "has_free_shipping": shipping["has_free_shipping"],
        "free_shipping_threshold_nis": shipping["free_shipping_threshold_nis"],
        # context
        "event_tag": event_tag,
        "is_flash_sale": is_flash,
        "is_marketing_only": is_marketing_only(body, discount["has_discount"]),
    }


def mark_duplicates(records: list[dict]) -> None:
    """Set is_duplicate=True for records with the same body_hash within 36h of an earlier copy."""
    seen: dict[str, datetime] = {}
    for r in records:
        h = r["body_hash"]
        sent = datetime.fromisoformat(r["sent_at"])
        if h in seen and (sent - seen[h]).total_seconds() < 36 * 3600:
            r["is_duplicate"] = True
        else:
            r["is_duplicate"] = False
            seen[h] = sent


# ---------------------------------------------------------------------------
# PDF reading
# ---------------------------------------------------------------------------

def read_pdf_text(pdf_path: Path) -> str:
    reader = pypdf.PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _read_pdf_export_date(pdf_path: Path) -> Optional[date]:
    """
    Read the PDF's CreationDate / ModDate metadata to infer the export date.
    iPhone Messages-exported PDFs reliably have these set to the export moment.
    Returns None if no usable date can be parsed.
    """
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        md = reader.metadata or {}
        for key in ("/CreationDate", "/ModDate"):
            v = md.get(key)
            if not v:
                continue
            s = str(v)
            # PDF format: "D:YYYYMMDDHHmmSS..." sometimes with TZ suffix
            m = re.match(r"D?:?(\d{4})(\d{2})(\d{2})", s)
            if m:
                y, mo, d = (int(x) for x in m.groups())
                return date(y, mo, d)
    except Exception:
        pass
    return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pdf", required=True, help="Path to the SMS export PDF")
    p.add_argument("--store", required=True, help="Store name (e.g., TERMINALX, CASTRO)")
    p.add_argument(
        "--export-date",
        default=None,
        help="Date the PDF was exported (YYYY-MM-DD). "
             "If omitted, inferred from the PDF's CreationDate/ModDate metadata. "
             "Used to walk backward and resolve missing years on iPhone date headers."
    )
    p.add_argument("--extra-brands", default="", help="Comma-separated brands to add to detector")
    p.add_argument("--out", default=None, help="Output JSON path (default: <store>.json)")
    args = p.parse_args()

    pdf_path = Path(args.pdf)
    if args.export_date:
        export_date = datetime.strptime(args.export_date, "%Y-%m-%d").date()
    else:
        export_date = _read_pdf_export_date(pdf_path)
        if export_date is None:
            sys.exit("ERROR: --export-date was not provided and PDF has no readable CreationDate. "
                     "Pass --export-date YYYY-MM-DD explicitly.")
        print(f"Using PDF metadata export date: {export_date.isoformat()}")
    extra_brands = {b.strip().upper() for b in args.extra_brands.split(",") if b.strip()}
    out_path = Path(args.out) if args.out else Path(f"{args.store}.json")

    raw_text = read_pdf_text(pdf_path)
    messages = segment_messages(raw_text, export_date)
    records = [process_message(m, extra_brands) for m in messages]
    mark_duplicates(records)
    assign_campaign_ids(records)

    # Build summary
    brand_freq: dict[str, int] = {}
    for r in records:
        for b in r["brands"]:
            brand_freq[b] = brand_freq.get(b, 0) + 1
    brand_vocab_sorted = sorted(brand_freq.items(), key=lambda kv: (-kv[1], kv[0]))
    brand_vocab_list = [{"brand": b, "mentions": c} for b, c in brand_vocab_sorted]

    candidate_brands = discover_brand_candidates(raw_text)
    detected_set = set(brand_freq.keys())
    constituent_words: set[str] = set()
    for b in detected_set:
        for w in re.split(r"[^A-Z0-9]+", b.upper()):
            if len(w) >= 3 and w not in ("THE", "AND"):
                constituent_words.add(w)
    # Also include source-side alias tokens (COVERSE → CONVERSE, BORWN → BROWN)
    from brands import BRAND_ALIASES
    for src in BRAND_ALIASES.keys():
        for w in re.split(r"[^A-Z0-9]+", src.upper()):
            if len(w) >= 3 and w not in ("THE", "AND"):
                constituent_words.add(w)
    unrecognized = [
        c for c in candidate_brands
        if c not in detected_set and c not in constituent_words
    ][:30]

    # ── Analytics layer ──────────────────────────────────────────────────
    campaigns = build_campaigns(records)
    sale_moments = compute_sale_moments(campaigns)
    brand_recs = compute_brand_recommendations(campaigns, brand_vocab_list)
    monthly = compute_monthly(campaigns)
    verdict = compute_verdict(sale_moments, monthly)
    shopping_summary = compute_shopping_summary(sale_moments, campaigns)

    output = {
        "store": args.store,
        "export_date": export_date.isoformat(),
        "message_count": len(records),
        "discount_message_count": sum(1 for r in records if r["has_discount"]),
        "duplicate_count": sum(1 for r in records if r["is_duplicate"]),
        "campaign_count": len(campaigns),
        "date_range": {
            "first": records[0]["sent_date"] if records else None,
            "last": records[-1]["sent_date"] if records else None,
        },
        "verdict": verdict,
        "shopping_summary": shopping_summary,
        "sale_moments": sale_moments,
        "brand_recommendations": brand_recs,
        "monthly": monthly,
        "brand_vocab": brand_vocab_list,
        "unrecognized_caps_tokens": unrecognized,
        "campaigns": campaigns,
        "messages": records,
    }

    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(records)} messages, {len(campaigns)} campaigns, "
          f"{output['discount_message_count']} discount records -> {out_path}")
    print(f"Date range: {output['date_range']['first']} -> {output['date_range']['last']}")
    print(f"\nVerdict: {verdict}")
    print(f"\nTop brands: {', '.join(b for b, _ in brand_vocab_sorted[:10])}")
    if unrecognized:
        print(f"Unrecognized ALL-CAPS tokens (review these): {', '.join(unrecognized[:15])}")


if __name__ == "__main__":
    main()