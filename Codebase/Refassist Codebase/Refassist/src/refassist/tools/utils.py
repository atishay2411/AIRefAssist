import re, json, hashlib
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

DEFAULT_UA = "ieee-ref-agent/1.0 (mailto:you@example.com)"
SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "v"}

# Fixed: remove duplicate key and use standard short month names
MONTHS_NAME = {
    "1": "Jan", "2": "Feb", "3": "Mar", "4": "Apr",
    "5": "May", "6": "Jun", "7": "Jul", "8": "Aug",
    "9": "Sep", "10": "Oct", "11": "Nov", "12": "Dec"
}

try:
    from rapidfuzz import fuzz
    RF_AVAILABLE = True
except Exception:
    fuzz = None
    RF_AVAILABLE = False

_THIS_YEAR = datetime.utcnow().year

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

def authors_to_list(a: Any) -> List[str]:
    if not a: return []
    if isinstance(a, list): return [normalize_text(x) for x in a if normalize_text(x)]
    parts = re.split(r",\s*|\s+&\s+| and ", str(a))
    return [normalize_text(p) for p in parts if normalize_text(p)]

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
    IEEE reference-list rule:
      - If there are 1–6 authors: list them all, using 'and' before the last.
      - If there are >=7 authors: list ONLY the first author followed by '*et al.*'
        (et al. is italicized in many IEEE guides).
    Sources: IEEE Reference Guide; Purdue OWL IEEE. 
    """
    items = [format_author_ieee(a) for a in auths if a]
    n = len(items)
    if n == 0:
        return ""
    if n >= 7:
        # First author + italicized et al.
        return f"{items[0]} *et al.*"
    if n == 1:
        return items[0]
    # 2–6 authors: comma-separated with ', and' before last
    return ", ".join(items[:-1]) + ", and " + items[-1]

def heuristic_abbrev(fullname: str) -> str:
    return ""

def format_doi_link(doi: str) -> str:
    d = normalize_text(doi).lower().strip()
    for prefix in ["https://doi.org/", "http://doi.org/", "doi:"]:
        if d.startswith(prefix):
            d = d[len(prefix):].strip()
    d = d.replace("http://", "").replace("https://", "").replace("doi.org/", "").strip()
    return f"https://doi.org/{d}" if d else ""

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
        "jul":"7","aug":"8","sep":"9","sept":"9","oct":"10","nov":"11","dec":"12"
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
