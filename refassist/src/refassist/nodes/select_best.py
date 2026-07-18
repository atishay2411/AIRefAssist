from collections import Counter, defaultdict
from typing import Any, Dict, List, Tuple, Optional
import re

from ..logging import logger
from ..state import PipelineState
from ..tools.utils import (
    normalize_text, token_similarity, authors_to_list,
    is_plausible_year, coerce_year, bare_doi
)

try:
    from ..tools.utils import _trace_year
except Exception:
    def _trace_year(*args, **kwargs): return


# ------------------------------
# Helpers
# ------------------------------

def _norm_author(author: str) -> str:
    """Comparison key: FIRST initial + surname.

    Citation styles disagree on middle initials ("A. J. Wakefield" vs
    "AJ Wakefield" vs "Andrew Wakefield"); keeping every initial makes
    equal authors compare unequal and wrongly trips the author gate.
    """
    parts = (author or "").strip().split()
    if not parts:
        return ""
    if parts[-1].lower() in {"al.", "et", "et."}:
        return ""
    first = parts[0][0].upper() + "." if len(parts) > 1 and parts[0][0].isalpha() else ""
    surname = parts[-1] if parts[-1] and parts[-1][0].isalpha() else ""
    return f"{first} {surname}".strip().lower()


def _author_set(authors) -> set:
    """Normalized comparison keys; empties (e.g. from 'et al.') are dropped so
    a set is either genuinely informative or genuinely empty."""
    return {k for k in (_norm_author(a) for a in authors_to_list(authors)) if k}


def _title_sim(a: str, b: str) -> float:
    return token_similarity(normalize_text(a), normalize_text(b))


def _first_page(s: str) -> str:
    m = re.search(r"\d+", normalize_text(s).replace("—", "-").replace("–", "-"))
    return m.group(0) if m else ""


def _norm_isbn(s) -> str:
    return re.sub(r"[\s\-]", "", str(s or "")).upper()


def _isbn_match(a, b):
    """True/False when both sides have an ISBN, None otherwise.
    Understands the ISBN-10 ↔ ISBN-13 (978-prefix) correspondence."""
    a, b = _norm_isbn(a), _norm_isbn(b)
    if not a or not b:
        return None
    if a == b:
        return True
    if len(a) == 13 and len(b) == 10:
        a, b = b, a
    if len(a) == 10 and len(b) == 13 and b.startswith("978"):
        return a[:9] == b[3:12]
    return False


def _coordinate_score(ex: Dict, c: Dict) -> float:
    """Agreement of a candidate with the USER'S volume/issue/first-page/ISBN.

    Two works routinely share a title and authors (Shannon 1948 Parts 1 and
    2; a book vs. an essay excerpted from it) — the user's bibliographic
    coordinates are then the only signal saying which one they cited. Used
    as a relative tiebreaker between candidates, never as an absolute gate,
    so a citation whose pages are simply wrong still gets corrected.
    """
    score = 0.0
    for key, agree, clash in (("volume", 1.0, -2.0), ("issue", 1.0, -2.0)):
        exv = normalize_text(ex.get(key, "")).lstrip("0")
        cv = normalize_text(c.get(key, "")).lstrip("0")
        if exv and cv:
            score += agree if exv == cv else clash
    exp, cp = _first_page(ex.get("pages", "")), _first_page(c.get("pages", ""))
    if exp and cp:
        score += 1.5 if exp == cp else -2.5
    m = _isbn_match(ex.get("isbn"), c.get("isbn"))
    if m is True:
        score += 4.0
    elif m is False:
        score -= 3.0
    return score


def _author_drop_penalty(ex_auths: set, c_auths: set) -> float:
    """A candidate whose author set is a STRICT subset of the citation's
    would 'correct' the reference by deleting co-authors (Russell & Norvig
    once became Russell alone). Penalize such candidates in ranking."""
    if ex_auths and c_auths and c_auths < ex_auths:
        return -1.5
    return 0.0


def _w(src: str) -> float:
    return {
        "ieeexplore": 1.2,
        "crossref": 1.0,
        "openalex": 0.9,
        "openlibrary": 0.8,
        "dblp": 0.9,
        "datacite": 0.9,
        "europepmc": 0.85,
        "googlebooks": 0.8,
        "unpaywall": 0.6,
        "doaj": 0.6,
        "biorxiv": 0.6,
        "semanticscholar": 0.7,
        "pubmed": 0.6,
        "arxiv": 0.4,
    }.get((src or "").lower(), 0.2)


def _pages_richness(s: str) -> int:
    if not s:
        return 0
    s2 = normalize_text(s).replace("—", "-").replace("–", "-")
    nums = re.findall(r"\d+", s2)
    if "-" in s2 and len(nums) >= 2:
        try:
            if int(nums[0]) != int(nums[1]):
                return 2
        except Exception:
            # looks like a range but not strict ints; still richer than single
            return 2
    if len(nums) >= 1:
        return 1
    return 0


def _norm_type(t: str) -> str:
    t = normalize_text(t)
    t = t.replace(" ", "-")
    return t


def _container_name(rec: Dict) -> str:
    """
    Prefer explicit book_title if present (our normalizers may set it for book-chapter).
    Fallbacks to journal_name / container_title.
    """
    for k in ("book_title", "container_title", "journal_name"):
        v = rec.get(k)
        if v:
            return normalize_text(v)
    return ""


def _container_sim(a: str, b: str) -> float:
    return token_similarity(normalize_text(a), normalize_text(b))


# ------------------------------
# Voting
# ------------------------------
def _vote_field(cl: List[Dict], key: str) -> Tuple[str, float, Optional[str]]:
    """
    Weighted vote among candidate metadata fields.
    Special handling for 'pages' to avoid mixing across bad containers.
    """
    bucket: Dict[str, float] = defaultdict(float)
    source_for: Dict[str, str] = {}

    for c in cl:
        v = normalize_text(c.get(key, ""))
        if not v:
            continue

        w = _w(c.get("source"))
        bucket[v] += w
        if v not in source_for or w > _w(source_for.get(v, "")):
            source_for[v] = (c.get("source") or "")

    if not bucket:
        return "", 0.0, None

    # Special selection for 'pages': prefer the richest (longer, more detailed) range
    if key == "pages":
        best_val, best_w = None, -1.0
        best_rich, best_len = -1, -1
        for v, w in bucket.items():
            rich = _pages_richness(v)
            if (
                (w > best_w)
                or (w == best_w and rich > best_rich)
                or (w == best_w and rich == best_rich and len(v) > best_len)
            ):
                best_val, best_w = v, w
                best_rich, best_len = rich, len(v)
        return best_val or "", best_w, source_for.get(best_val or "")

    # Normal path for other keys
    best_val, best_w = max(bucket.items(), key=lambda kv: kv[1])
    return best_val, best_w, source_for.get(best_val)



def _vote_authors(cl: List[Dict]) -> Tuple[List[str], float, Optional[str]]:
    bucket: Dict[Tuple[str, ...], float] = defaultdict(float)
    raw_map: Dict[Tuple[str, ...], List[str]] = {}
    src_map: Dict[Tuple[str, ...], str] = {}
    for c in cl:
        raw = authors_to_list(c.get("authors", []))
        norm = tuple(a for a in [_norm_author(a) for a in raw] if a)
        if not norm:
            continue
        w = _w(c.get("source"))
        bucket[norm] += w
        if norm not in raw_map or len(raw_map[norm]) < len(raw):
            raw_map[norm] = raw
            src_map[norm] = c.get("source")
    if not bucket:
        return [], 0.0, None
    norm_best, w = max(bucket.items(), key=lambda kv: kv[1])
    return raw_map.get(norm_best, list(norm_best)), w, src_map.get(norm_best)


def _has_any_doi_agreement(cluster: List[Dict]) -> str:
    dois = [normalize_text(c.get("doi", "")).lower().replace("doi:", "") for c in cluster if c.get("doi")]
    dois = [d for d in dois if d]
    if not dois:
        return ""
    c = Counter(dois)
    doi, cnt = c.most_common(1)[0]
    if cnt >= 2 or any((ci.get("source") in {"crossref", "ieeexplore", "openalex"} and normalize_text(ci.get("doi")).lower().replace("doi:", "") == doi) for ci in cluster):
        return doi
    return ""


# ------------------------------
# Year logic
# ------------------------------

_PREPRINT_TYPES = {"posted-content", "preprint", "informal and other publications"}
_PREPRINT_SOURCES = {"arxiv", "biorxiv"}


def _is_preprint_record(c: Dict) -> bool:
    """A preprint/mirror record of a work that also exists as a publication."""
    if (c.get("source") or "").lower() in _PREPRINT_SOURCES:
        return True
    if _norm_type(c.get("type", "")) in {t.replace(" ", "-") for t in _PREPRINT_TYPES}:
        return True
    venue = normalize_text(c.get("journal_name") or c.get("container_title") or "").lower()
    return venue in ("arxiv", "corr", "biorxiv", "medrxiv")


def _best_year_from_subset(cands: List[Dict]) -> Tuple[str, str]:
    rows = []
    for c in cands:
        y = coerce_year(c.get("year", "") or "")
        if not is_plausible_year(y):
            continue
        try:
            yi = int(y)
        except Exception:
            continue
        rows.append((yi, (c.get("source") or "").lower(), _is_preprint_record(c)))
    if not rows:
        return "", ""

    trusted = [(y, s) for (y, s, _pre) in rows
               if s in {"crossref", "ieeexplore", "openalex", "openlibrary", "dblp",
                        "datacite", "europepmc", "googlebooks"}]
    if trusted:
        # Preprints predate the publication they became, so "earliest trusted"
        # (which defeats re-registered mirrors carrying LATER years) would hand
        # a proceedings paper its preprint's year. Prefer published records;
        # fall back to preprints only when nothing else is trusted.
        published = [(y, s) for (y, s, pre) in rows
                     if not pre and s in {"crossref", "ieeexplore", "openalex",
                                          "openlibrary", "dblp", "datacite",
                                          "europepmc", "googlebooks"}]
        pool = published or trusted
        chosen, src = min(pool, key=lambda x: x[0])
        _trace_year("year_trusted_earliest_subset", chosen_year=chosen, chosen_source=src)
        return str(chosen), src

    rows = [(y, s) for (y, s, _pre) in rows]

    years = [y for y, _ in rows]
    mode, _ = Counter(years).most_common(1)[0]
    near = [y for y in years if abs(y - mode) <= 1]
    if near:
        chosen = min(near)
        src = next((s for y, s in rows if y == chosen), "consensus")
        _trace_year("year_mode_window_subset", chosen_year=chosen, chosen_source=src)
        return str(chosen), src

    chosen, src = min(rows, key=lambda x: x[0])
    _trace_year("year_fallback_earliest_subset", chosen_year=chosen, chosen_source=src)
    return str(chosen), src


def _best_year(cluster: List[Dict], doi_agree: str = "") -> Tuple[str, str]:
    if doi_agree:
        same_doi = [c for c in cluster if normalize_text(c.get("doi", "")).lower().replace("doi:", "") == doi_agree]
        if same_doi:
            y, src = _best_year_from_subset(same_doi)
            if y:
                _trace_year("year_from_doi_subset", doi=doi_agree, chosen_year=y, chosen_source=src)
                return y, src
    return _best_year_from_subset(cluster)


# ------------------------------
# Type compatibility & gates
# ------------------------------

def _type_compatible(ex_type: str, cr_type: str) -> bool:
    ex_type = _norm_type(ex_type)
    cr_type = _norm_type(cr_type)
    if not ex_type or not cr_type:
        return True
    if ex_type in {"book"}:
        return cr_type in {
            "book", "monograph", "reference-book", "edited-book",
            "report", "book-series", "book-track", "proceedings"
        }
    if ex_type in {"book-chapter", "chapter", "incollection"}:
        return cr_type in {"book-chapter", "chapter", "incollection"}
    if ex_type in {"journal-article", "article"}:
        return cr_type in {"journal-article", "journal-issue", "journal"}
    if ex_type in {"conference-paper", "paper-conference", "proceedings-article", "conference"}:
        # book-chapter included: LNCS-style proceedings register papers as chapters.
        # posted-content deliberately excluded — preprint mirrors of conference
        # papers carry wrong years/DOIs.
        return cr_type in {
            "proceedings-article", "proceedings", "paper-conference",
            "conference-paper", "book-chapter",
        }
    # Otherwise be permissive
    return True


def _year_gap_score(ex_year: str, cand_year: Any) -> float:
    """Positive bump for close years, growing penalty for distant ones."""
    try:
        gap = abs(int(str(ex_year).strip()[:4]) - int(coerce_year(cand_year)))
    except Exception:
        return 0.0
    if gap <= 1:
        return 0.25
    return -min(gap, 10) * 0.15


def _has_min_author_overlap(ex_auths: set, cand_auths: set, min_frac: float = 0.34) -> bool:
    if not ex_auths:
        return True
    if not cand_auths:
        return False
    overlap = len(ex_auths & cand_auths) / max(len(ex_auths), 1)
    return overlap >= min_frac


# ------------------------------
# Precise Crossref (entity gated)
# ------------------------------

def _try_precise_crossref(ex: dict, cands: List[Dict]) -> Optional[Dict]:
    ex_type = normalize_text(ex.get("type", ""))
    ex_title = normalize_text(ex.get("title", ""))
    ex_auths = _author_set(ex.get("authors", []))
    ex_doi = normalize_text(ex.get("doi", "")).lower().replace("doi:", "")
    ex_year = normalize_text(ex.get("year", ""))

    best = None
    best_score = -1.0     # tiebreak score (base + coordinates)
    best_base = -1.0      # quality score (floor is judged on this alone)

    for c in cands:
        if (c.get("source") or "").lower() != "crossref":
            continue
        logger.debug("[select_best] Crossref candidate %s type=%s vs ex_type=%s",
                     c.get("doi"), c.get("type"), ex_type)

        cr_type = normalize_text(c.get("type", ""))
        if not _type_compatible(ex_type, cr_type):
            logger.debug("[select_best] Skipping Crossref %s (type mismatch %s vs %s)",
                         c.get("doi"), ex_type, cr_type)
            continue

        t_sim = token_similarity(ex_title, normalize_text(c.get("title", ""))) if ex_title else 0.0
        c_auths = _author_set(c.get("authors", []))
        a_ok = _has_min_author_overlap(ex_auths, c_auths)
        c_doi = normalize_text(c.get("doi", "")).lower().replace("doi:", "")
        doi_exact = 1.0 if ex_doi and c_doi and (ex_doi == c_doi) else 0.0

        y_score = _year_gap_score(ex_year, c.get("year")) if (ex_year and c.get("year")) else 0.0

        # Require DOI exact OR (title+author gate)
        if not ((doi_exact == 1.0) or (t_sim >= 0.96 and a_ok)):
            continue

        base = (t_sim * 3.0) + (doi_exact * 5.0) + (y_score * 2.0)
        if a_ok:
            base += 2.0
        # Coordinates and author-completeness tiebreak between candidates
        # that both pass the identity gate (Shannon Part 1 vs Part 2 share
        # title, authors, year AND volume — only issue/pages differ).
        score = base + _coordinate_score(ex, c) + _author_drop_penalty(ex_auths, c_auths)
        if score > best_score:
            best_score = score
            best_base = base
            best = c

    # Minimum-score gate on the BASE score: a genuine match scores ~4.3+ even
    # with a year off by two; citation-farm mirrors fall well below via the
    # year-gap penalty. Judged without coordinate penalties so a citation
    # whose pages are wrong (the thing we correct) still clears it.
    if best is not None and best_base < 3.5:
        logger.debug("[select_best] Rejecting weak precise-Crossref match %s (base=%.2f)",
                     best.get("doi"), best_base)
        best = None

    if best is not None:
        logger.debug("[select_best] Precise Crossref match → %s (score=%.2f)", best.get("doi"), best_score)
        return best
    return None


# ------------------------------
# Container consistency reconciliation
# ------------------------------
def _reconcile_container_consistency(ex: dict, best: Dict, provenance: Dict[str, str], is_crossref_exact: bool) -> None:
    """
    Prevent mixing fields from different containers (e.g., Panayi 1996 edited volume vs. OUP 1998 monograph).
    Extended to catch 'book' entries that actually behave like chapters (contain 'In:' or '(ed.)' cues).
    Mutates 'best' and 'provenance' in place.
    """
    btype = _norm_type(best.get("type", ""))

    # --- new: widen guard to suspicious "book" refs that contain chapter cues ---
    looks_chapterish = bool(
        re.search(r'\bIn:\b', ex.get("reference", "") or "", re.I) or
        re.search(r'\((ed|eds)\.\)', ex.get("reference", "") or "", re.I)
    )
    if btype not in {"book-chapter", "chapter", "incollection"} and not (btype == "book" and looks_chapterish):
        return

    ex_container = normalize_text(ex.get("book_title") or ex.get("container_title") or ex.get("journal_name") or "")
    best_container = _container_name(best)

    if not ex_container and not best_container:
        return

    sim = _container_sim(ex_container, best_container) if (ex_container and best_container) else 1.0
    if sim >= 0.90:
        # Align the visible "book_title" key for formatting
        if ex_container and not best.get("book_title"):
            best["book_title"] = ex_container
            provenance["book_title"] = provenance.get("book_title") or "extracted"
        elif best_container:
            best["book_title"] = best_container
            provenance["book_title"] = provenance.get("book_title") or "consensus"
        # Avoid mixing journal_name with book_title in output
        if "journal_name" in best and best["journal_name"] and best["journal_name"] != best["book_title"]:
            best["journal_name"] = ""
        return

    # Containers disagree → pick a single consistent mode
    logger.debug("[select_best] Container DISAGREE: ex='%s' vs best='%s' (sim=%.2f)",
                 ex_container, best_container, sim)

    doi_mode = bool(best.get("doi")) and is_crossref_exact

    if doi_mode:
        logger.debug("[select_best] Container resolution: DOI mode (trust Crossref container/pages/year)")
        if best_container:
            best["book_title"] = best_container
            provenance["book_title"] = "crossref-exact"
        for k in ("journal_name", "journal_abbrev"):
            if k in best and best[k] and best.get("book_title") and best[k] != best["book_title"]:
                best[k] = ""
    else:
        logger.debug("[select_best] Container resolution: Extracted mode (keep extracted container; drop DOI pages/year)")
        if ex_container:
            best["book_title"] = ex_container
            provenance["book_title"] = "extracted"
        for k in ("pages", "volume", "issue", "year", "month", "doi", "journal_name", "journal_abbrev"):
            if k in best:
                best[k] = ""
                provenance[k] = "cleared-container-mismatch"

    # --- new: if record type is 'book' but has chapter cues, downgrade type and clean chapter-only fields ---
    if btype == "book" and looks_chapterish:
        logger.debug("[select_best] Detected book with chapter cues → reclassify as 'book chapter'")
        best["type"] = "book chapter"
        provenance["type"] = "heuristic-downgrade"
        # clear implausible book-only fields
        for k in ("volume", "issue"):
            best.pop(k, None)
        # keep pages only if short (<40pp) to avoid entire book ranges
        if "pages" in best:
            span = best["pages"]
            nums = re.findall(r"\d+", span or "")
            if len(nums) >= 2 and (int(nums[-1]) - int(nums[0]) > 40):
                best["pages"] = ""
                provenance["pages"] = "cleared-long-span"



# ------------------------------
# Consensus (author-gated)
# ------------------------------

def _consensus_record(ex: dict, candidates: List[Dict]) -> Tuple[Dict, List[str], Dict[str, str]]:
    if not candidates:
        return {}, [], {}

    # HARD DOI ANCHOR: the user supplied a DOI and at least one registry
    # confirmed it — restrict matching to exactly that work. A verified user
    # DOI must never be swapped for a "better" record (a stable Zenodo
    # concept DOI was once replaced by a release candidate's version DOI).
    anchor = bare_doi(ex.get("doi") or "").lower()
    if anchor:
        anchored = [c for c in candidates
                    if bare_doi(c.get("doi") or "").lower() == anchor]
        if anchored:
            candidates = anchored

    # Prefer precise Crossref only if entity-gated
    crossref_exact = _try_precise_crossref(ex, candidates)
    if crossref_exact:
        best = crossref_exact.copy()
        provenance = {k: "crossref-exact" for k in best.keys()}
        # Container consistency check (pre-return)
        _reconcile_container_consistency(ex, best, provenance, is_crossref_exact=True)
        logger.debug("[select_best] Using full Crossref record %s as authoritative match", best.get("doi"))
        return best, [], provenance

    # Cluster by title
    clusters: List[List[Dict]] = []
    THRESH = 0.92
    for c in candidates:
        placed = False
        for cl in clusters:
            if _title_sim(c.get("title", ""), cl[0].get("title", "")) >= THRESH:
                cl.append(c)
                placed = True
                break
        if not placed:
            clusters.append([c])

    ex_title = normalize_text(ex.get("title", ""))
    ex_auths = _author_set(ex.get("authors", []))
    ex_doi = normalize_text(ex.get("doi", "")).lower().replace("doi:", "")
    ex_type = _norm_type(ex.get("type", ""))

    def entity_score(c: Dict) -> float:
        t_sim = token_similarity(ex_title, normalize_text(c.get("title", ""))) if ex_title else 0.0
        c_auths = _author_set(c.get("authors", []))
        a_ok = _has_min_author_overlap(ex_auths, c_auths)
        c_doi = normalize_text(c.get("doi", "")).lower().replace("doi:", "")
        doi_exact = 1.0 if ex_doi and c_doi and ex_doi == c_doi else 0.0
        ex_year_s = str(ex.get("year", "")).strip()
        yr_score = _year_gap_score(ex_year_s, c.get("year")) if (ex_year_s and c.get("year")) else 0.0

        s = (t_sim * 3.0) + (doi_exact * 5.0) + yr_score
        if a_ok:
            s += 2.0
        # Penalize type mismatch
        if ex_type and not _type_compatible(ex_type, c.get("type", "")):
            s *= 0.5
        # User-supplied identifiers anchor the choice between look-alike
        # clusters (the cited ISBN once pointed at the right book while an
        # unrelated same-titled essay won on title alone).
        s += _coordinate_score(ex, c) + _author_drop_penalty(ex_auths, c_auths)
        return s

    clusters.sort(key=lambda cl: max(entity_score(ci) for ci in cl), reverse=True)
    top = clusters[0]

    # Anchor requirement: with no extracted title AND no DOI there is nothing
    # to validate a match against — search results are then pure noise (a
    # garbled book citation once merged in an unrelated journal article).
    if not ex_title and not ex_doi:
        logger.debug("[select_best] No title/DOI anchor extracted — refusing all matches")
        return {}, [], {}

    # Title floor: the winning cluster must actually resemble the citation.
    # Typos survive easily (token similarity ~0.9) — unrelated works do not.
    # When the user supplied an ISBN and no cluster member carries a matching
    # one, the bar rises: a title-only match against an identified book is
    # exactly how "Artificial Intelligence: A Modern Approach" once became an
    # unrelated essay titled "Artificial Intelligence".
    if ex_title:
        top_sim = max((_title_sim(ex_title, ci.get("title", "")) for ci in top), default=0.0)
        floor = 0.5
        if _norm_isbn(ex.get("isbn")) and not any(
                _isbn_match(ex.get("isbn"), ci.get("isbn")) for ci in top):
            floor = 0.85
        if top_sim < floor and not any(
                ex_doi and normalize_text(ci.get("doi", "")).lower().replace("doi:", "") == ex_doi
                for ci in top):
            logger.debug("[select_best] Best cluster title similarity %.2f < %.2f — "
                         "refusing match", top_sim, floor)
            return {}, [], {}

    def _extracted_fallback(reason: str):
        # No trustworthy match: NEVER override the user's data with candidate
        # noise; report the reference as unverified instead.
        logger.debug("[select_best] %s; falling back to extracted entities", reason)
        fb = {
            "source": "extracted",
            "title": ex.get("title", ""),
            "authors": authors_to_list(ex.get("authors", [])),
            "year": ex.get("year", ""),
            "type": ex.get("type", ""),
            "publisher": ex.get("publisher", ""),
            "location": ex.get("location", ""),
            "edition": ex.get("edition", ""),
            "isbn": ex.get("isbn", ""),
            "doi": ex.get("doi", ""),
            "book_title": ex.get("book_title", ""),
        }
        return fb, [], {k: "extracted" for k in fb.keys()}

    # **AUTHOR GATE**: if we have extracted authors, only keep members with min overlap OR same DOI
    if ex_auths:
        gated = []
        for ci in top:
            c_auths = _author_set(ci.get("authors", []))
            doi_match = ex_doi and normalize_text(ci.get("doi", "")).lower().replace("doi:", "") == ex_doi
            if doi_match or _has_min_author_overlap(ex_auths, c_auths):
                gated.append(ci)
        if gated:
            top = gated
        else:
            return _extracted_fallback("Author-gate removed all candidates")

    # **COORDINATE FILTER**: two same-titled works by the same authors (the
    # two parts of Shannon 1948) land in ONE cluster; voting then blends
    # their volumes/issues/pages/DOIs. When at least one member agrees with
    # the user's coordinates and others contradict them, only the agreeing
    # members may vote. When NO member agrees (the user's coordinates are
    # simply wrong), nothing is dropped and correction proceeds as before.
    coord_scores = [(ci, _coordinate_score(ex, ci)) for ci in top]
    if any(s > 0 for _, s in coord_scores) and any(s < 0 for _, s in coord_scores):
        kept = [ci for ci, s in coord_scores if s >= 0]
        if kept:
            logger.debug("[select_best] Coordinate filter dropped %d cluster member(s) "
                         "contradicting the cited volume/issue/pages/ISBN",
                         len(top) - len(kept))
            top = kept

    # Drop type-incompatible members (e.g. posted-content mirrors of conference
    # papers) before voting so their DOIs/years can't win the consensus.
    if ex_type:
        compat = [ci for ci in top if _type_compatible(ex_type, ci.get("type", ""))]
        if compat and len(compat) < len(top):
            logger.debug("[select_best] Dropped %d type-incompatible cluster member(s)",
                         len(top) - len(compat))
            top = compat

    # Year plausibility: a citation's year is rarely off by more than a few
    # years, but re-registered mirrors carry years a decade later. Keep a
    # far-year member only when its DOI matches the citation's.
    ex_year_hint = coerce_year(ex.get("year") or "")
    if ex_year_hint:
        def _year_plausible(ci: Dict) -> bool:
            cy = coerce_year(ci.get("year") or "")
            if not cy:
                return True
            if ex_doi and normalize_text(ci.get("doi", "")).lower().replace("doi:", "") == ex_doi:
                return True
            return abs(int(cy) - int(ex_year_hint)) <= 5
        plaus = [ci for ci in top if _year_plausible(ci)]
        if plaus:
            if len(plaus) < len(top):
                logger.debug("[select_best] Dropped %d year-implausible cluster member(s)",
                             len(top) - len(plaus))
                top = plaus
        else:
            # EVERY member contradicts the cited year by >5 years and none
            # matches the input DOI — a cluster made entirely of re-registered
            # mirrors (we have met one). An all-implausible cluster is not
            # evidence; it must not win by default.
            return _extracted_fallback("Entire cluster is year-implausible")

    best: Dict = {"source": "consensus"}
    provenance: Dict[str, str] = {}

    doi_agree = _has_any_doi_agreement(top)
    if doi_agree:
        best["doi"] = doi_agree
        provenance["doi"] = "doi-agreement"
    else:
        v, _, src = _vote_field(top, "doi")
        best["doi"] = v
        provenance["doi"] = src or ""

    v, _, src = _vote_field(top, "title")
    best["title"] = v
    provenance["title"] = src or ""

    a, _, src = _vote_authors(top)
    best["authors"] = a
    provenance["authors"] = src or ""

    _trace_year("year_consensus_inputs",
                cluster=[{"source": c.get("source"), "year": c.get("year"), "doi": c.get("doi")} for c in top])
    y, ysrc = _best_year(top, doi_agree=best.get("doi", ""))
    best["year"] = y
    provenance["year"] = ysrc or (provenance.get("doi") or "") or "consensus"

    for k in ("journal_name", "journal_abbrev", "conference_name", "volume", "issue",
              "pages", "month", "publisher", "location", "edition", "isbn", "url", "type", "book_title", "container_title"):
        v, _, src = _vote_field(top, k)
        best[k] = v
        provenance[k] = src or ""

    # Container consistency (consensus path)
    _reconcile_container_consistency(ex, best, provenance, is_crossref_exact=False)

    _trace_year("select_best_consensus",
                consensus_year=best.get("year"),
                consensus_source=provenance.get("year"))
    return best, [], provenance


# ------------------------------
# Entry
# ------------------------------

def select_best(state: PipelineState) -> PipelineState:
    ex = state["extracted"]
    cands = state.get("candidates", [])
    if not cands:
        state["best"], state["matching_fields"], state["provenance"] = {}, [], {}
        return state

    # Refine the reference type now that online candidates exist — the initial
    # classification only had the LLM vote and textual cues to work with.
    # Only candidates matching on BOTH title and authors get a vote, so
    # same-titled records of a different work can't outvote the correct
    # classification (e.g. chapters titled "Deep Learning" vs. the book).
    from ..tools.type_reconcile import reconcile_type
    ex_title_for_vote = normalize_text(ex.get("title", ""))
    ex_auths_for_vote = _author_set(ex.get("authors", []))

    ex_year_for_vote = coerce_year(ex.get("year") or "")

    def _may_vote(c: Dict) -> bool:
        if not ex_title_for_vote or _title_sim(ex_title_for_vote, c.get("title", "")) < 0.85:
            return False
        if ex_auths_for_vote:
            c_auths = _author_set(c.get("authors", []))
            if not _has_min_author_overlap(ex_auths_for_vote, c_auths):
                return False
        # Mirrors re-registered years later match on title+authors but not year.
        cy = coerce_year(c.get("year") or "")
        if ex_year_for_vote and cy and abs(int(cy) - int(ex_year_for_vote)) > 5:
            return False
        return True

    voting_cands = [c for c in cands if _may_vote(c)]
    refined = reconcile_type(
        candidates=voting_cands,
        llm_vote=state.get("_llm_type_vote"),
        reference=state.get("reference"),
    )
    if refined and refined != "other":
        state["type"] = refined

    # Version ambiguity: the same work published as BOTH a conference paper
    # and a journal article (extended version) matches on title+authors for
    # either — gates can't decide which one the user meant, so surface it
    # instead of silently merging metadata across versions.
    from ..tools.type_reconcile import TYPE_CANON
    canon_types = set()
    for c in voting_cands:
        t = _norm_type(c.get("type", ""))
        canon = TYPE_CANON.get(t, (c.get("type") or "").lower())
        if canon in ("conference paper", "journal article"):
            canon_types.add(canon)
    if len(canon_types) > 1:
        state["version_alternatives"] = sorted(canon_types)
        logger.debug("[select_best] Multiple published versions detected: %s", canon_types)

    # Matching context: the extracted fields rarely carry an explicit type or
    # the raw reference string, but the type gates and chapter cues need them.
    ex_match = dict(ex)
    if not ex_match.get("type") and state.get("type"):
        ex_match["type"] = state["type"]
    ex_match.setdefault("reference", state.get("reference", ""))

    consensus, matching_fields, prov = _consensus_record(ex_match, cands)
    state["best"], state["matching_fields"], state["provenance"] = consensus or {}, matching_fields or [], prov or {}

    # Fabricated-citation detection: when the author gate rejected everything
    # BUT a published work matches the title near-exactly with entirely
    # different authors, the citation is likely fabricated or misattributed
    # (AI-generated citations routinely invent authors for real titles).
    # That published work's retraction status matters too.
    if not state["best"] or state["best"].get("source") == "extracted":
        ex_title = normalize_text(ex.get("title", ""))
        ex_auths = _author_set(ex.get("authors", []))
        if ex_title and ex_auths:
            best_hit, best_sim = None, 0.0
            for c in cands:
                sim = _title_sim(ex_title, c.get("title", ""))
                if sim >= 0.93 and sim > best_sim:
                    c_auths = _author_set(c.get("authors", []))
                    if c_auths and not (ex_auths & c_auths):
                        best_hit, best_sim = c, sim
            if best_hit is not None:
                state["author_mismatch"] = {
                    "title": best_hit.get("title", ""),
                    "published_authors": authors_to_list(best_hit.get("authors", [])),
                    "cited_authors": authors_to_list(ex.get("authors", [])),
                    "source": best_hit.get("source", ""),
                    "doi": best_hit.get("doi", ""),
                    "year": best_hit.get("year", ""),
                    "retracted": bool(best_hit.get("retracted")),
                }
                logger.debug("[select_best] Author mismatch: title matches %s but "
                             "authors are disjoint", best_hit.get("doi") or "a published work")

    # Debug: show final container vs. extracted to confirm no mixing
    try:
        ex_cont = normalize_text(ex.get("book_title") or ex.get("container_title") or ex.get("journal_name") or "")
        best_cont = normalize_text(state["best"].get("book_title") or state["best"].get("container_title") or state["best"].get("journal_name") or "")
        sim = _container_sim(ex_cont, best_cont) if (ex_cont and best_cont) else None
        logger.debug("[select_best] FINAL container: ex='%s' vs best='%s' sim=%s", ex_cont, best_cont, sim)
    except Exception:
        pass

    return state
