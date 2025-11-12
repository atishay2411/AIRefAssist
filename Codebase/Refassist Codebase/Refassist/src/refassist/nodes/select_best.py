from collections import Counter, defaultdict
from typing import Dict, List, Tuple, Optional
import re

from ..state import PipelineState
from ..tools.utils import (
    normalize_text, token_similarity, authors_to_list,
    is_plausible_year, coerce_year
)

try:
    from ..tools.utils import _trace_year
except Exception:
    def _trace_year(*args, **kwargs): return


# ------------------------------
# Helpers
# ------------------------------

def _norm_author(author: str) -> str:
    parts = (author or "").strip().split()
    if not parts:
        return ""
    if parts[-1].lower() in {"al.", "et", "et."}:
        return ""
    initials = [p[0].upper() + "." for p in parts[:-1] if p and p[0].isalpha()]
    surname = parts[-1] if parts[-1] and parts[-1][0].isalpha() else ""
    return (" ".join(initials + [surname])).strip().lower()


def _title_sim(a: str, b: str) -> float:
    return token_similarity(normalize_text(a), normalize_text(b))


def _w(src: str) -> float:
    return {
        "ieeexplore": 1.2,
        "crossref": 1.0,
        "openalex": 0.9,
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
        # Skip pages from mismatched containers (if flagged)
        if key == "pages":
            ex_cont = normalize_text(c.get("book_title") or c.get("container_title") or c.get("journal_name") or "")
            # Example safety check: ignore any candidates marked as bad container
            if ex_cont and any(
                "_BAD_CONTAINER_" in (
                    normalize_text(x.get("book_title") or "") +
                    normalize_text(x.get("journal_name") or "")
                ) for x in cl
            ):
                continue

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
        rows.append((yi, (c.get("source") or "").lower()))
    if not rows:
        return "", ""

    trusted = [(y, s) for (y, s) in rows if s in {"crossref", "ieeexplore", "openalex"}]
    if trusted:
        chosen, src = min(trusted, key=lambda x: x[0])
        _trace_year("year_trusted_earliest_subset", chosen_year=chosen, chosen_source=src)
        return str(chosen), src

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
    # Otherwise be permissive
    return True


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
    ex_auths = {_norm_author(a) for a in authors_to_list(ex.get("authors", []))}
    ex_doi = normalize_text(ex.get("doi", "")).lower().replace("doi:", "")
    ex_year = normalize_text(ex.get("year", ""))

    best = None
    best_score = -1.0

    for c in cands:
        if (c.get("source") or "").lower() == "crossref":
            print(f"[select_best] Crossref candidate {c.get('doi')} type={c.get('type')} vs ex_type={ex_type}")

        if (c.get("source") or "").lower() != "crossref":
            continue

        cr_type = normalize_text(c.get("type", ""))
        if not _type_compatible(ex_type, cr_type):
            print(f"[select_best] Skipping Crossref {c.get('doi')} (type mismatch {ex_type} vs {cr_type})")
            continue

        t_sim = token_similarity(ex_title, normalize_text(c.get("title", ""))) if ex_title else 0.0
        c_auths = {_norm_author(a) for a in authors_to_list(c.get("authors", []))}
        a_ok = _has_min_author_overlap(ex_auths, c_auths)
        c_doi = normalize_text(c.get("doi", "")).lower().replace("doi:", "")
        doi_exact = 1.0 if ex_doi and c_doi and (ex_doi == c_doi) else 0.0

        y_match = 0.0
        if ex_year and c.get("year"):
            try:
                y_match = 1.0 if abs(int(ex_year) - int(coerce_year(c.get("year")))) <= 1 else 0.0
            except Exception:
                pass

        # Require DOI exact OR (title+author gate)
        if not ((doi_exact == 1.0) or (t_sim >= 0.96 and a_ok)):
            continue

        score = (t_sim * 3.0) + (doi_exact * 5.0) + (y_match * 0.5)
        if a_ok:
            score += 2.0
        if score > best_score:
            best_score = score
            best = c

    if best is not None:
        print(f"[select_best] Precise Crossref match → {best.get('doi')} (score={best_score:.2f})")
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
    print(f"[select_best] Container DISAGREE: ex='{ex_container}' vs best='{best_container}' (sim={sim:.2f})")

    doi_mode = bool(best.get("doi")) and is_crossref_exact

    if doi_mode:
        print("[select_best] Container resolution: DOI mode (trust Crossref container/pages/year)")
        if best_container:
            best["book_title"] = best_container
            provenance["book_title"] = "crossref-exact"
        for k in ("journal_name", "journal_abbrev"):
            if k in best and best[k] and best.get("book_title") and best[k] != best["book_title"]:
                best[k] = ""
    else:
        print("[select_best] Container resolution: Extracted mode (keep extracted container; drop DOI pages/year)")
        if ex_container:
            best["book_title"] = ex_container
            provenance["book_title"] = "extracted"
        for k in ("pages", "volume", "issue", "year", "month", "doi", "journal_name", "journal_abbrev"):
            if k in best:
                best[k] = ""
                provenance[k] = "cleared-container-mismatch"

    # --- new: if record type is 'book' but has chapter cues, downgrade type and clean chapter-only fields ---
    if btype == "book" and looks_chapterish:
        print("[select_best] Detected book with chapter cues → reclassify as 'book chapter'")
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

    # Prefer precise Crossref only if entity-gated
    crossref_exact = _try_precise_crossref(ex, candidates)
    if crossref_exact:
        best = crossref_exact.copy()
        provenance = {k: "crossref-exact" for k in best.keys()}
        # Container consistency check (pre-return)
        _reconcile_container_consistency(ex, best, provenance, is_crossref_exact=True)
        print(f"[select_best] Using full Crossref record {best.get('doi')} as authoritative match")
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
    ex_auths = {_norm_author(a) for a in authors_to_list(ex.get("authors", []))}
    ex_doi = normalize_text(ex.get("doi", "")).lower().replace("doi:", "")
    ex_type = _norm_type(ex.get("type", ""))

    def entity_score(c: Dict) -> float:
        t_sim = token_similarity(ex_title, normalize_text(c.get("title", ""))) if ex_title else 0.0
        c_auths = {_norm_author(a) for a in authors_to_list(c.get("authors", []))}
        a_ok = _has_min_author_overlap(ex_auths, c_auths)
        c_doi = normalize_text(c.get("doi", "")).lower().replace("doi:", "")
        doi_exact = 1.0 if ex_doi and c_doi and ex_doi == c_doi else 0.0
        yr_bonus = 0.0
        try:
            exy, cy = int(str(ex.get("year", "")).strip() or "0"), int(str(c.get("year", "")).strip() or "0")
            if exy and cy and abs(exy - cy) <= 1:
                yr_bonus = 0.25
        except Exception:
            pass

        s = (t_sim * 3.0) + (doi_exact * 5.0) + yr_bonus
        if a_ok:
            s += 2.0
        # Penalize type mismatch
        if ex_type and not _type_compatible(ex_type, c.get("type", "")):
            s *= 0.5
        return s

    clusters.sort(key=lambda cl: max(entity_score(ci) for ci in cl), reverse=True)
    top = clusters[0]

    # **AUTHOR GATE**: if we have extracted authors, only keep members with min overlap OR same DOI
    if ex_auths:
        gated = []
        for ci in top:
            c_auths = {_norm_author(a) for a in authors_to_list(ci.get("authors", []))}
            doi_match = ex_doi and normalize_text(ci.get("doi", "")).lower().replace("doi:", "") == ex_doi
            if doi_match or _has_min_author_overlap(ex_auths, c_auths):
                gated.append(ci)
        if gated:
            top = gated
        else:
            # If nothing passes author gate, DO NOT override authors/year with candidate noise
            print("[select_best] Author-gate removed all candidates; falling back to extracted entities")
            best = {
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
            prov = {k: "extracted" for k in best.keys()}
            return best, [], prov

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

    consensus, matching_fields, prov = _consensus_record(ex, cands)
    state["best"], state["matching_fields"], state["provenance"] = consensus or {}, matching_fields or [], prov or {}

    # Debug: show final container vs. extracted to confirm no mixing
    try:
        ex_cont = normalize_text(ex.get("book_title") or ex.get("container_title") or ex.get("journal_name") or "")
        best_cont = normalize_text(state["best"].get("book_title") or state["best"].get("container_title") or state["best"].get("journal_name") or "")
        sim = _container_sim(ex_cont, best_cont) if (ex_cont and best_cont) else None
        print(f"[select_best] FINAL container: ex='{ex_cont}' vs best='{best_cont}' sim={sim}")
    except Exception:
        pass

    return state
