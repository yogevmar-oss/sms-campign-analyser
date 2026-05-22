"""
Brand vocabulary and normalization.

Strategy:
- Maintain a global brand list organized BY CATEGORY (sportswear, designer, beauty, …).
  All stores share this vocabulary.
- Auto-detect brand candidates per PDF (ALL-CAPS Latin tokens not in stopwords)
- Normalize case + fix Hebrew RTL-flipping of multi-word brands ("BALANCE NEW" → "NEW BALANCE")
- Scan for Hebrew transliterations of brands (סקוצ׳ → SCOTCH AND SODA, וג׳ה → VEJA, …)
  using word-boundary matching to avoid substring false positives.

Adding new brands:
- Single Latin word (e.g. "NIKE"): drop into the relevant single-word category set.
- Multi-word Latin (e.g. "NEW BALANCE"): append to MULTI_WORD_BRANDS.
- Source-typo or alternate spelling: add to BRAND_ALIASES.
- Variant of a parent brand (e.g. "TERMINAL X KIDS" → "TERMINAL X"): BRAND_FAMILIES.
- Hebrew transliteration: add to HEBREW_BRAND_ALIASES.
"""

import re
from typing import List, Set, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# MULTI-WORD BRANDS (matched before single-word so we don't split them)
# ─────────────────────────────────────────────────────────────────────────────
MULTI_WORD_BRANDS = [
    # Sportswear & athletic
    "NEW BALANCE",
    "UNDER ARMOUR",
    # Mainstream fashion
    "AMERICAN EAGLE",
    "JACK & JONES",
    "JACK JONES",
    "VERO MODA",
    "UNITED COLORS OF BENETTON",
    "SCOTCH AND SODA",
    "SCOTCH & SODA",
    # Footwear & outdoor
    "THE NORTH FACE",
    "TEVA NAOT",
    "DR MARTENS",
    "DR. MARTENS",
    # Premium / designer
    "RAY BAN",
    "SUNGLASS HUT",
    "YVES SAINT LAURENT",
    "SAINT LAURENT",
    "CALVIN KLEIN",
    "TOMMY HILFIGER",
    "POLO RALPH LAUREN",
    "RALPH LAUREN",
    # Beauty
    "BOBBI BROWN",
    "BOBBI BORWN",
    "ESTEE LAUDER",
    "L'OREAL PROFESSIONNEL",
    "LOREAL PROFESSIONNEL",
    "OLIÈRE PARIS",
    "OLIERE PARIS",
    # Jewelry
    "SHLOMIT OFIR",
    "THE DUCHESSES",
    # Home & lifestyle
    "FOOD APPEAL",
    "BUY CARPET",
    # Store-specific (TerminalX house brands)
    "TERMINAL X",
    "TERMINAL X KIDS",
    "TERMINAL X WOMEN",
    "TERMINAL X MEN",
    "TERMINAL X WOMEN & MEN",
    "TX ESSENTIALS",
    "QUESTION MARK",
]

# ─────────────────────────────────────────────────────────────────────────────
# SINGLE-WORD BRANDS — organized by category for maintainability
# ─────────────────────────────────────────────────────────────────────────────
_SPORTSWEAR_BRANDS = {
    "NIKE", "ADIDAS", "PUMA", "ASICS", "REEBOK", "FILA", "VANS",
    "CONVERSE", "COVERSE",
    "HOKA", "AUTRY",
    "BILLABONG", "QUIKSILVER", "ROXY", "RIPCURL",
    "COLUMBIA", "CARHARTT", "OBEY",
    "STRONGFUL",
}
_FOOTWEAR_BRANDS = {
    "BIRKENSTOCK", "HAVAIANAS", "TKEES", "UGG", "SPYDER",
    "VEJA", "ALOHAS", "PALLADIUM", "CROCS",
    "VAGABOND", "ARBEL", "LIYOM",
}
_MAINSTREAM_FASHION = {
    "MANGO", "ZARA", "BERSHKA", "STRADIVARIUS",
    "AERIE", "GUESS", "HOLLISTER",
    "BENETTON",
    "ALLSAINTS", "ECOALF",
    # H&M family: store displays "H&M" but pypdf RTL extraction often flips to "M&H".
    # COS is H&M Group's sister brand and is often called out as excluded from H&M promos.
    "H&M", "M&H", "COS",
}
_ISRAELI_RETAIL = {
    "FOX", "CASTRO", "FACTORY54", "RENUAR", "GOLF", "GOLBARY",
    "ADAH", "ITAY", "DAMARI",
}
_PREMIUM_DESIGNER = {
    "GUCCI", "OAKLEY", "PRADA", "ARMANI", "VALENTINO",
    "DIOR", "CHANEL", "YSL",
}
_BEAUTY = {
    "CLINIQUE", "MAC", "LANCOME", "LANCÔME",
}
_JEWELRY = {
    "TOUS", "IMPRESS", "ZABAN",
}
_HOME = {
    "HEMILTON",
}
_LIFESTYLE_BOUTIQUE = {
    "SUNKISSED",
    "CROSLEY",
}

SINGLE_WORD_BRANDS = (
    _SPORTSWEAR_BRANDS | _FOOTWEAR_BRANDS | _MAINSTREAM_FASHION |
    _ISRAELI_RETAIL | _PREMIUM_DESIGNER | _BEAUTY | _JEWELRY | _HOME |
    _LIFESTYLE_BOUTIQUE
)


# ─────────────────────────────────────────────────────────────────────────────
# HEBREW BRAND ALIASES — for stores that write brand names in Hebrew script
# Word-boundary matching prevents substring false positives.
# ─────────────────────────────────────────────────────────────────────────────
HEBREW_BRAND_ALIASES = {
    # Sportswear / sneakers
    "נייקי": "NIKE",
    "אדידס": "ADIDAS",
    "פומה": "PUMA",
    "קונברס": "CONVERSE",
    "אסיקס": "ASICS",
    "ריבוק": "REEBOK",
    "ניו באלאנס": "NEW BALANCE",
    "באלאנס ניו": "NEW BALANCE",          # RTL flip
    "הוקה": "HOKA",
    # Story's footwear lineup
    "וג׳ה": "VEJA",
    "וגה": "VEJA",
    "אלוהס": "ALOHAS",
    "פלדיום": "PALLADIUM",
    "ערבל": "ARBEL",                       # Story sneaker brand
    "ליום": "LIYOM",                       # Story sneaker brand (Latin form unconfirmed)
    "קרוסלי": "CROSLEY",
    "ואגאבונד": "VAGABOND",
    "ואגבונד": "VAGABOND",
    # Outdoor / workwear
    "קארהארט": "CARHARTT",
    "אוביי": "OBEY",
    "בירקנשטוק": "BIRKENSTOCK",
    "האווייאנס": "HAVAIANAS",
    "קרוקס": "CROCS",
    # Mainstream fashion in Hebrew
    "סקוצ׳": "SCOTCH AND SODA",
    "אולסיינטס": "ALLSAINTS",
    "מנגו": "MANGO",
    "זארה": "ZARA",
    # Israeli brands
    "פוקס": "FOX",
    "קסטרו": "CASTRO",
    "רנואר": "RENUAR",
}


# Canonical aliases: raw → normalized. Applied after detection.
BRAND_ALIASES = {
    "COVERSE": "CONVERSE",
    "BOBBI BORWN": "BOBBI BROWN",
    "LOREAL PROFESSIONNEL": "L'OREAL PROFESSIONNEL",
    "SCOTCH & SODA": "SCOTCH AND SODA",
    # M&H is the visual RTL extraction of "H&M" — normalize to the canonical form.
    "M&H": "H&M",
}


# Brand families: child → parent.
BRAND_FAMILIES = {
    "TERMINAL X KIDS": "TERMINAL X",
    "TERMINAL X WOMEN": "TERMINAL X",
    "TERMINAL X MEN": "TERMINAL X",
    "TERMINAL X WOMEN & MEN": "TERMINAL X",
    "TX ESSENTIALS": "TERMINAL X",
    "L'OREAL PROFESSIONNEL": "L'OREAL",
}


def normalize_brand(brand: str) -> str:
    return BRAND_ALIASES.get(brand, brand)


def brand_family(brand: str) -> str:
    return BRAND_FAMILIES.get(brand, brand)


# ─────────────────────────────────────────────────────────────────────────────
# Stopwords — things that look like brands but aren't
# ─────────────────────────────────────────────────────────────────────────────
STOPWORDS = {
    "SHOP", "NOW", "BUY", "SALE", "NEW", "OFF", "AND", "OR", "THE", "A", "AN",
    "MORE", "DROP", "NEW DROP", "READY", "TO", "TAKE", "LATER", "REGRET",
    "DAY", "DAYS", "WHITE", "BLACK", "FRIDAY", "CYBER", "SUMMER", "WINTER",
    "FALL", "SPRING", "HOLIDAY", "VIP", "DREAMCARD",
    "SMS", "TEXT", "MESSAGE",
    "BRANDS",
    "OUTLET",
    "LIFESTYLE", "OUTDOOR",
    "SCENT", "COMING", "CALLING",
    "ESSENTIALS",
    "MARK",
    "EAGLE", "BALANCE", "HUT", "BAN", "PARIS",
    "WOMEN", "MEN", "KIDS",
    "WIN", "WIN20", "XWINTER", "NEW20", "DREAM30", "ROC15VTF7V", "V7VTF15ROC",
    "PHASE", "BANG", "BUNG", "BOOM",
    "SAVE", "GIFT", "FREE", "VISA",
    "CONTROL", "ADISTAR",
    "ARE", "IS", "BE", "WAS", "WERE",
    "SNEAKERS", "SPORTSWEAR", "SANDALS", "SWIMWEAR", "FRAGRANCE",
    "HOME", "YOUR", "CART", "FOR", "WITH", "ONLY", "RAIN", "DOWN",
    "LAYERS", "ARRIVALS", "COLD", "COLORS", "DATE", "DON", "FAST", "FRESH",
    "FASHION", "HOW", "YOU", "GET", "READY", "PURIM", "WANTS", "HEART", "WHAT",
    "HOT", "SKI", "MEETS", "WHEN", "OUT", "OUR", "ALL", "HERE", "LET", "SEASON",
    "MONDAY", "WEEKEND", "OFFICIAL", "FACE", "NORTH",
    "DUCHESSES", "OFIR", "SHLOMIT",
    "CARPET", "FOOD", "APPEAL", "UNITED",
    "OUTWEAR", "PREPARE", "THERE", "HANGING", "LEAVE",
    "ITEMS", "PAIR", "PERFECT", "SALES", "SET",
    # Story-specific noise
    "OVER", "ACCESS", "AIN", "BARUCH", "COMPLICATED", "DISCOUNT", "EMILY", "ENDS",
    "FLASH", "GREEN", "HABA", "KNOW", "LOUDER", "NOTE", "NOVEMBER", "OUTLETS",
    "PLANS", "SELLING", "SHOPPING", "SINGLE", "STORY", "SHADE", "SCOTCH",
    "SODA", "STATUS", "CONNECTED", "ACTIVE",
}

DIACRITIC_MAP = str.maketrans({
    "É": "E", "È": "E", "Ê": "E", "Ë": "E",
    "Á": "A", "À": "A", "Â": "A", "Ä": "A",
    "Í": "I", "Ì": "I", "Î": "I", "Ï": "I",
    "Ó": "O", "Ò": "O", "Ô": "O", "Ö": "O",
    "Ú": "U", "Ù": "U", "Û": "U", "Ü": "U",
    "Ñ": "N", "Ç": "C",
})

HEBREW_LETTER = r"[\u05D0-\u05EA]"


def _strip_diacritics(s: str) -> str:
    return s.upper().translate(DIACRITIC_MAP)


def _all_multi_word_variants() -> List[Tuple[str, str]]:
    variants = []
    for brand in MULTI_WORD_BRANDS:
        canonical = brand.upper()
        words = canonical.split()
        if len(words) == 1:
            continue
        forward = r"\b" + r"\s+".join(re.escape(w) for w in words) + r"\b"
        reversed_pat = r"\b" + r"\s+".join(re.escape(w) for w in reversed(words)) + r"\b"
        variants.append((canonical, forward))
        if forward != reversed_pat:
            variants.append((canonical, reversed_pat))
    variants.sort(key=lambda x: -len(x[0]))
    return variants


def detect_brands(text: str, extra_brands: Set[str] | None = None) -> List[str]:
    """Find all brand mentions. Returns canonical names in order of appearance, deduplicated."""
    found: List[str] = []
    seen: Set[str] = set()

    norm_text = _strip_diacritics(text)

    # 1) Multi-word Latin brands first
    variants = _all_multi_word_variants()
    masked = norm_text
    for canonical, pattern in variants:
        for m in re.finditer(pattern, masked, flags=re.IGNORECASE):
            if canonical not in seen:
                found.append(canonical)
                seen.add(canonical)
            masked = masked[:m.start()] + (" " * (m.end() - m.start())) + masked[m.end():]

    # 2) Single-word Latin brands
    single_set = SINGLE_WORD_BRANDS | (extra_brands or set())
    single_set = {_strip_diacritics(b) for b in single_set}
    for brand in single_set:
        pattern = r"(?<![A-Z0-9])" + re.escape(brand) + r"(?![A-Z0-9])"
        if re.search(pattern, masked, flags=re.IGNORECASE):
            if brand not in seen:
                found.append(brand)
                seen.add(brand)

    # 3) Hebrew transliterations (word-boundary checked)
    for hebrew_form, canonical in HEBREW_BRAND_ALIASES.items():
        pattern = (r"(?<!" + HEBREW_LETTER + r")"
                   + re.escape(hebrew_form)
                   + r"(?!" + HEBREW_LETTER + r")")
        if re.search(pattern, text):
            if canonical not in seen:
                found.append(canonical)
                seen.add(canonical)

    # Normalize aliases, deduplicate
    normalized: List[str] = []
    seen_norm: Set[str] = set()
    for b in found:
        nb = normalize_brand(b)
        if nb not in seen_norm:
            normalized.append(nb)
            seen_norm.add(nb)
    return normalized


def discover_brand_candidates(text: str) -> List[str]:
    tokens = re.findall(r"\b[A-Z][A-ZÉÈÊËÁÀÂÄÍÌÎÏÓÒÔÖÚÙÛÜÑÇ&]{2,}\b", text)
    counts: dict[str, int] = {}
    for t in tokens:
        norm = _strip_diacritics(t)
        if norm in STOPWORDS:
            continue
        counts[norm] = counts.get(norm, 0) + 1
    return sorted(counts.keys(), key=lambda k: (-counts[k], k))


if __name__ == "__main__":
    samples = [
        "20% הנחה ב-NEW BALANCE רק עד יום שבת בחצות",
        "20% הנחה ב-BALANCE NEW רק עד יום שבת בחצות",
        "NIKE, ADIDAS, TERMINAL X, MANGO, AMERICAN EAGLE, BILLABONG & MORE",
        "אלוהס וג׳ה פלדיום",
        "סקוצ׳ של החדשה האביב קולקציית",
        "אפורים השמיים אבל את",  # MUST NOT match "פורים"
    ]
    for s in samples:
        print(f"  IN : {s}")
        print(f"  OUT: {detect_brands(s)}")