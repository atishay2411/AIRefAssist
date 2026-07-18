import os, re, json, hashlib
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

# Crossref/NCBI etiquette: identify the operator via a contact address.
CONTACT_EMAIL = os.getenv("REFASSIST_CONTACT_EMAIL", "")
DEFAULT_UA = (
    f"refassist/1.0 (mailto:{CONTACT_EMAIL})" if CONTACT_EMAIL else "refassist/1.0"
)
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

# IEEE month forms: abbreviated with a period, except May/June/July.
MONTHS_NAME = {
    "1": "Jan.", "2": "Feb.", "3": "Mar.", "4": "Apr.",
    "5": "May", "6": "June", "7": "July", "8": "Aug.",
    "9": "Sept.", "10": "Oct.", "11": "Nov.", "12": "Dec."
}

try:
    from rapidfuzz import fuzz
    RF_AVAILABLE = True
except Exception:
    fuzz = None
    RF_AVAILABLE = False

_THIS_YEAR = datetime.now(timezone.utc).year

def safe_json_load(s: Any) -> Optional[Dict[str, Any]]:
    if s is None: return None
    if isinstance(s, dict): return s
    sx = s.decode("utf-8","ignore") if isinstance(s,(bytes,bytearray)) else str(s)
    sx = sx.strip()
    try:
        if sx.startswith("{"): return json.loads(sx)
    except Exception: ...
    i, n = 0, len(sx)
    while i < n and sx[i] != "{": i += 1
    if i >= n: return None
    stack=0; in_str=False; esc=False; start=None
    for j in range(i, n):
        ch = sx[j]
        if in_str:
            if esc: esc=False
            elif ch=="\\": esc=True
            elif ch=='"': in_str=False
        else:
            if ch=='"': in_str=True
            elif ch=="{":
                if stack==0: start=j
                stack+=1
            elif ch=="}":
                stack-=1
                if stack==0 and start is not None:
                    cand = sx[start:j+1]
                    try: return json.loads(cand)
                    except Exception: start=None
    return None

def normalize_text(x: Any) -> str:
    if x is None: return ""
    s = re.sub(r"\s+"," ", str(x).strip())
    return s

def norm_for_compare(x: Any) -> str:
    s = normalize_text(x).lower()
    s = re.sub(r"[^\w\s]"," ", s)
    s = re.sub(r"\s+"," ", s).strip()
    return s

def token_similarity(a: str, b: str) -> float:
    a = norm_for_compare(a); b = norm_for_compare(b)
    if not a or not b: return 0.0
    if RF_AVAILABLE and fuzz is not None: return fuzz.token_sort_ratio(a, b) / 100.0
    sa, sb = set(a.split()), set(b.split())
    inter = sa & sb
    union = sa | sb
    return len(inter) / max(1, len(union))

_ET_AL_RE = re.compile(r",?\s*\bet\.?\s+al\.?\s*$", re.I)

def _clean_author(x: Any) -> str:
    """Normalize one author name; 'et al.' fragments and punctuation-only
    tokens (stray quotes from mangled input) yield ''. """
    s = _ET_AL_RE.sub("", normalize_text(x)).strip("\"'“”‘’ ")
    # ", and E. Giuriani" splits to "and E. Giuriani" — the conjunction must
    # not survive as a fake first initial ("A. E. Giuriani").
    s = re.sub(r"^(?:and|&)\s+", "", s, flags=re.I)
    # DBLP disambiguates homonyms with a numeric suffix ("Stuart Russell 0001")
    # — never part of a real name.
    s = re.sub(r"\s+\d{4}$", "", s)
    if s.strip(". ").lower() in {"et al", "et", "al", "others", "and"}:
        return ""
    if not re.search(r"[A-Za-z]", s):
        return ""
    return s.strip()

def authors_to_list(a: Any) -> List[str]:
    if not a: return []
    if isinstance(a, list):
        return [c for c in (_clean_author(x) for x in a) if c]
    parts = re.split(r",\s*|\s+&\s+| and ", str(a))
    return [c for c in (_clean_author(p) for p in parts) if c]

def _initials(given: str) -> List[str]:
    parts = re.split(r"\s+", given.strip()); out=[]
    for p in parts:
        if not p: continue
        hy = p.split("-")
        if len(hy)>1: out.append("-".join([h[0].upper()+"." for h in hy if h]))
        elif re.match(r"^[A-Za-z]\.$", p): out.append(p.upper())
        elif p.lower().rstrip(".") in SUFFIXES: out.append(p.capitalize().rstrip(".")+".")
        else: out.append(p[0].upper()+".")
    return out

def format_author_ieee(name: str) -> str:
    n = normalize_text(name)
    if not n: return ""
    if "," in n:
        last, given = [p.strip() for p in n.split(",", 1)]
    else:
        toks = n.split()
        if len(toks) == 1: return toks[0]
        last = toks[-1]; given=" ".join(toks[:-1])
    init = " ".join(_initials(given))
    last_tokens = last.split()
    if last_tokens and last_tokens[-1].lower().rstrip(".") in SUFFIXES:
        suf = last_tokens[-1].capitalize().rstrip(".")+"."
        last = " ".join(last_tokens[:-1])
        return f"{init} {last}, {suf}".strip(", ")
    return f"{init} {last}".strip()

def format_authors_ieee_list(auths: List[str]) -> str:
    """
    IEEE reference-list rule (IEEE Reference Guide):
      - 1 author: as-is.
      - 2 authors: 'A. Author and B. Author' — no comma before 'and'.
      - 3–6 authors: 'A. Author, B. Author, and C. Author' (serial comma).
      - >=7 authors: first author followed by 'et al.' (roman, not italic).
    """
    items = [format_author_ieee(a) for a in auths if a]
    n = len(items)
    if n == 0:
        return ""
    if n >= 7:
        return f"{items[0]} et al."
    if n == 1:
        return items[0]
    if n == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]

def heuristic_abbrev(fullname: str) -> str:
    return ""

def bare_doi(doi: str) -> str:
    """Strip URL/`doi:` prefixes down to the bare 10.x/y identifier."""
    d = normalize_text(doi).lower().strip()
    for prefix in ["https://doi.org/", "http://doi.org/", "doi:"]:
        if d.startswith(prefix):
            d = d[len(prefix):].strip()
    return d.replace("http://", "").replace("https://", "").replace("doi.org/", "").strip()

def format_doi_link(doi: str) -> str:
    d = bare_doi(doi)
    return f"https://doi.org/{d}" if d else ""

def format_doi_ieee(doi: str) -> str:
    """IEEE reference style renders DOIs as 'doi: 10.x/y', not as a URL."""
    d = bare_doi(doi)
    return f"doi: {d}" if d else ""

def normalize_pages(p: str) -> Tuple[str, bool]:
    """
    Normalize 'pages' and flag e-locations.

    Returns (normalized_pages, is_elocation):

    - Convert en/em dashes to '-'.
    - If it contains a numeric range like '5338-5346' => (same, False).
    - If it’s a single numeric page '5338'        => ('5338', False).
    - If it’s alphanumeric (e.g., 'e1234', 'A12') => (same, True).
    - Otherwise                                    => (as-is, False).
    """
    p = normalize_text(p).replace("—","-").replace("–","-")
    if not p:
        return "", False
    if "-" in p:
        return p, False
    if re.fullmatch(r"\d+", p):
        return p, False
    if re.fullmatch(r"[A-Za-z]\d+[A-Za-z]?", p) or re.search(r"[A-Za-z]", p):
        return p, True
    return p, False

def normalize_month_field(m: Any) -> str:
    s = normalize_text(m)
    if not s: return ""
    m_map = {
        "jan":"1","feb":"2","mar":"3","apr":"4","may":"5","jun":"6",
        "jul":"7","aug":"8","sep":"9","sept":"9","oct":"10","nov":"11","dec":"12",
        "january":"1","february":"2","march":"3","april":"4","june":"6",
        "july":"7","august":"8","september":"9","october":"10",
        "november":"11","december":"12",
    }
    sl = s.strip(". ").lower()
    if sl in m_map: return m_map[sl]
    if re.fullmatch(r"0?[1-9]|1[0-2]", sl): return str(int(sl))
    return s

def fingerprint_state(ex: Dict[str, Any], best: Dict[str, Any], sugg: Dict[str, Any]) -> str:
    payload = json.dumps({"ex": ex, "best": best, "sugg": sugg}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8","ignore")).hexdigest()

def safe_str(v: Any) -> str:
    try:
        if v is None: return ""
        return str(v).strip()
    except Exception:
        return ""

def isbn_valid(isbn: Any) -> bool:
    """ISBN-10 / ISBN-13 checksum validation (hyphens/spaces ignored)."""
    s = re.sub(r"[\s\-]", "", str(isbn or "")).upper()
    if len(s) == 10 and re.fullmatch(r"\d{9}[\dX]", s):
        total = sum((10 - i) * (10 if ch == "X" else int(ch)) for i, ch in enumerate(s))
        return total % 11 == 0
    if len(s) == 13 and s.isdigit():
        total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(s))
        return total % 10 == 0
    return False


DOI_SYNTAX_RE = re.compile(r"10\.\d{4,9}/\S+")

# ---- NEW: stronger year helpers ----
def is_plausible_year(y: Any) -> bool:
    try:
        yi = int(str(y).strip()[:4])
    except Exception:
        return False
    return 1800 <= yi <= (_THIS_YEAR + 1)

def coerce_year(y: Any) -> str:
    s = normalize_text(y)
    if not s: return ""
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", s)
    if not m: return ""
    return m.group(1)
