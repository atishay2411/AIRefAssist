from typing import Any, Dict, List, Tuple
from ..logging import logger
from ..state import PipelineState
from ..tools.utils import (
    normalize_text,
    authors_to_list,
    normalize_month_field,
    fingerprint_state,
    coerce_year,
    is_plausible_year,
)
import re

_ALWAYS_REWRITE = {"title", "authors", "year", "month", "doi"}  # critical truth fields


def _is_single_numeric_page(s: str) -> bool:
    s = normalize_text(s)
    return bool(s) and ("-" not in s) and bool(re.fullmatch(r"\d+", s))


def _extract_first_number(s: str) -> str:
    m = re.search(r"\d+", normalize_text(s))
    return m.group(0) if m else ""


def _is_range_with_two_numbers(s: str) -> bool:
    s = normalize_text(s).replace("—", "-").replace("–", "-")
    if "-" not in s:
        return False
    nums = re.findall(r"\d+", s)
    if len(nums) < 2:
        return False
    try:
        return int(nums[0]) != int(nums[1])
    except Exception:
        return True


def _coord_relation(ex0: Dict[str, Any], rec: Dict[str, Any]) -> Tuple[int, int]:
    """(agreements, contradictions) between the user's ORIGINAL
    volume/issue/first-page and a record's."""
    agrees = clashes = 0
    for k in ("volume", "issue"):
        a = normalize_text(ex0.get(k, "")).lstrip("0")
        b = normalize_text(rec.get(k, "")).lstrip("0")
        if a and b:
            agrees, clashes = (agrees + 1, clashes) if a == b else (agrees, clashes + 1)
    a = _extract_first_number(ex0.get("pages", ""))
    b = _extract_first_number(rec.get("pages", ""))
    if a and b:
        agrees, clashes = (agrees + 1, clashes) if a == b else (agrees, clashes + 1)
    return agrees, clashes


def _identity_conflict(ex0: Dict[str, Any], best: Dict[str, Any],
                       candidates: List[Dict[str, Any]]) -> bool:
    """True when best contradicts >=2 of the user's original coordinates AND
    some other same-titled candidate AGREES with >=2 of them — i.e. the
    user's coordinates describe a real record that isn't the one selected.
    A reference that is simply wrong has no candidate agreeing with it, so
    ordinary corrections are unaffected."""
    ex_title = normalize_text(ex0.get("title", ""))
    if not ex_title or not best:
        return False
    _, clashes = _coord_relation(ex0, best)
    if clashes < 2:
        return False
    from ..tools.utils import token_similarity
    for c in candidates or []:
        if token_similarity(ex_title, normalize_text(c.get("title", ""))) < 0.85:
            continue
        agrees, _ = _coord_relation(ex0, c)
        if agrees >= 2:
            return True
    return False


def _author_deletion_unproven(ex0: Dict[str, Any], ex: Dict[str, Any],
                              new_authors: Any, best: Dict[str, Any]) -> bool:
    """True when applying `new_authors` would DELETE cited co-authors without
    an exact DOI/ISBN match proving the citation really is the smaller-author
    record. Additions and reorderings pass freely."""
    orig = authors_to_list(ex0.get("authors") or ex.get("authors") or [])
    new = authors_to_list(new_authors)
    if not orig or not new:
        return False

    def keys(L):
        return {a.split()[-1].lower() for a in L if a.split()}
    if not (keys(new) < keys(orig)):
        return False
    exd = normalize_text(str(ex0.get("doi") or ex.get("doi") or "")).lower().replace("doi:", "")
    bed = normalize_text(str(best.get("doi") or "")).lower().replace("doi:", "")
    if exd and bed and exd == bed:
        return False
    ni = lambda s: re.sub(r"[\s\-]", "", str(s or "")).upper()
    ex_isbn, be_isbn = ni(ex0.get("isbn") or ex.get("isbn")), ni(best.get("isbn"))
    if ex_isbn and be_isbn and ex_isbn == be_isbn:
        return False
    return True


def _is_book_like(ex: Dict[str, Any], best: Dict[str, Any]) -> bool:
    t = normalize_text(ex.get("type") or best.get("type") or "")
    if "book" in t:
        return True
    title = normalize_text(ex.get("title") or best.get("title") or "")
    if any(w in title for w in ["manual", "handbook", "reproduction", "guide", "textbook"]):
        return True
    if (ex.get("publisher") or best.get("publisher")) and not (ex.get("journal_name") or best.get("journal_name")):
        return True
    return False


def apply_corrections(state: PipelineState) -> PipelineState:
    ex = dict(state["extracted"])
    best = state.get("best", {}) or {}
    prov = state.get("provenance", {}) or {}
    suggestions = state.get("suggestions", {}) or {}
    matching_fields = set(state.get("matching_fields", []))
    changes: List[Tuple[str, Any, Any]] = []
    audit = dict(state.get("audit", {}))

    # ---------- Thesis guard: theses are sole-authored by definition; the
    # supervisor appearing as co-author on database records must never be
    # promoted into the citation ----------
    if (state.get("type") or "").lower() == "thesis":
        protected_thesis = {"authors"}
    else:
        protected_thesis = set()

    # ---------- Book-like guard ----------
    if _is_book_like(ex, best):
        protected_fields = {"journal_name", "journal_abbrev", "volume", "issue", "pages"}
        logger.debug("[apply_corrections] Detected BOOK-LIKE record → protecting %s", protected_fields)
    else:
        protected_fields = set()
        logger.debug("[apply_corrections] Non-book type")

    # ---------- Identity-conflict guard ----------
    # The selected record contradicts the user's coordinates while another
    # candidate matches them: do not let it rewrite the coordinate fields.
    ex0 = state.get("_original_extracted") or {}
    if _identity_conflict(ex0, best, state.get("candidates", [])):
        protected_fields |= {"volume", "issue", "pages", "month", "doi"}
        state["identity_conflict"] = True
        logger.debug("[apply_corrections] IDENTITY CONFLICT — selected record "
                     "contradicts user coordinates that match another record; "
                     "protecting coordinate fields")

    # ---------- Monotonicity ----------
    # Fields the verification agents confirmed in an earlier round never get
    # rewritten to a different value by a later round's (possibly different)
    # best record. Empty fields may still be filled.
    locked = set(state.get("_locked_fields") or set())

    # ---------- Authoritative year protection ----------
    best_year = coerce_year(best.get("year") or "")
    ex_year = coerce_year(ex.get("year") or "")

    all_prov_sources = {normalize_text(v) for v in prov.values() if isinstance(v, str)}
    authoritative_present = bool(
        all_prov_sources
        & {"ieeexplore", "crossref", "crossref-exact", "openalex", "openlibrary",
           "semanticscholar", "doi-agreement", "earliest"}
    )

    if best_year and is_plausible_year(best_year) and authoritative_present:
        # Books: copyright year and catalog/release year routinely differ by
        # one (AIMA 4th ed. is 2020 in DBLP, 2021 on its own copyright page).
        # The user is looking at their copy — keep their year.
        if (ex_year and abs(int(ex_year) - int(best_year)) == 1
                and _is_book_like(ex, best)):
            audit.setdefault("year", "extracted")
            year_locked = True
        else:
            if ex_year and ex_year != best_year:
                logger.debug(
                    "[apply_corrections] Keeping authoritative year %s (authoritative sources: %s)",
                    best_year, all_prov_sources,
                )
            ex["year"] = best_year
            audit["year"] = "authoritative"
            year_locked = True
    else:
        year_locked = False

    # ---------- Main field sync ----------
    fields = [
        "title",
        "authors",
        "journal_name",
        "journal_abbrev",
        "volume",
        "issue",
        "pages",
        "doi",
        "year",
        "month",
        "conference_name",
        "publisher",
        "location",
        "edition",
        "isbn",
        "url",
        "type",
    ]

    for k in fields:
        if k in protected_fields or k in protected_thesis:
            continue
        if k == "year" and year_locked:
            continue

        bv = best.get(k)
        if not bv:
            continue

        prov_src = (prov.get(k) or "").lower()
        exv = normalize_text(ex.get(k, ""))
        bvn = normalize_text(bv)

        # Monotonicity: never rewrite a previously verified value
        if k in locked and exv and exv != bvn:
            logger.debug("[apply_corrections] Keeping %s — verified in an earlier round", k)
            continue

        # Never delete cited co-authors on similarity evidence alone
        if k == "authors" and _author_deletion_unproven(ex0, ex, bv, best):
            logger.debug("[apply_corrections] Blocked author deletion (no DOI/ISBN proof)")
            continue

        # Strong authoritative override
        if prov_src in {
            "crossref",
            "crossref-exact",
            "openalex",
            "openlibrary",
            "semanticscholar",
            "ieeexplore",
            "doi-agreement",
        }:
            if exv != bvn:
                logger.debug("[apply_corrections] Authoritative override: %s ← %s (from %s)", k, bv, prov_src)
                changes.append((k, ex.get(k), bv))
                ex[k] = bv
                audit[k] = prov_src
            continue

        # Normal correction fallback
        if (k in _ALWAYS_REWRITE) or (k not in matching_fields):
            if exv != bvn:
                logger.debug("[apply_corrections] Updating %s → %s (source=%s)", k, bv, prov_src or "consensus")
                changes.append((k, ex.get(k), bv))
                ex[k] = bv
                if prov_src:
                    audit[k] = prov_src

    # ---------- Pages enrichment (skip if protected) ----------
    if "pages" not in protected_fields:
        ex_pages = normalize_text(ex.get("pages", ""))
        be_pages = normalize_text(best.get("pages", ""))

        if ex_pages and be_pages:
            if _is_single_numeric_page(ex_pages) and _is_range_with_two_numbers(be_pages):
                ex_start = _extract_first_number(ex_pages)
                be_first = _extract_first_number(be_pages)
                if ex_start and be_first and ex_start == be_first:
                    if ex_pages != be_pages:
                        changes.append(("pages", ex.get("pages"), be_pages))
                        ex["pages"] = be_pages
                        audit.setdefault("pages", prov.get("pages", "consensus"))

        ex_pages_now = normalize_text(ex.get("pages", ""))
        if _is_single_numeric_page(ex_pages_now):
            target_start = _extract_first_number(ex_pages_now)
            cand_source = None
            cand_range = None
            for c in state.get("candidates", []) or []:
                cp = normalize_text(c.get("pages", ""))
                if _is_range_with_two_numbers(cp):
                    cstart = _extract_first_number(cp)
                    if cstart and target_start and cstart == target_start:
                        if not cand_range or len(cp) > len(cand_range):
                            cand_range = cp
                            cand_source = c.get("source") or "candidates"
            if cand_range and cand_range != ex_pages_now:
                changes.append(("pages", ex.get("pages"), cand_range))
                ex["pages"] = cand_range
                audit.setdefault("pages", cand_source or "candidates")

    # ---------- Suggestions (LLM/verify) ----------
    for k, v in (suggestions or {}).items():
        if k in protected_fields or k in protected_thesis:
            continue
        if k in locked and normalize_text(ex.get(k, "")) and \
                normalize_text(ex.get(k, "")) != normalize_text(str(v or "")):
            continue
        if k == "authors" and _author_deletion_unproven(ex0, ex, v, best):
            continue
        if (k in _ALWAYS_REWRITE) or (k not in matching_fields):
            if normalize_text(ex.get(k, "")) != normalize_text(v or ""):
                changes.append((k, ex.get(k), v))
                ex[k] = v
                audit.setdefault(k, "verify/llm")

    # ---------- Normalize ----------
    if isinstance(ex.get("authors"), str):
        al = authors_to_list(ex["authors"])
        if al != ex["authors"]:
            changes.append(("authors_list", ex["authors"], al))
            ex["authors"] = al
            audit.setdefault("authors", "normalize")

    if ex.get("month"):
        newm = normalize_month_field(ex["month"])
        if newm != ex["month"]:
            changes.append(("month_normalized", ex["month"], newm))
            ex["month"] = newm
            audit.setdefault("month", "normalize")

    # ---------- Commit ----------
    state["extracted"] = ex
    state["corrections"] = state.get("corrections", []) + changes
    state["attempts"] = state.get("attempts", 0) + 1
    state["_made_changes_last_cycle"] = bool(changes)
    state["audit"] = audit

    # Fingerprint & loop detection
    from ..tools.utils import fingerprint_state
    sugg = state.get("suggestions", {})
    best_now = state.get("best", {})
    new_fp = fingerprint_state(ex, best_now, sugg)
    hist = state.get("_fp_history", set())
    state["_loop_detected"] = new_fp in hist
    hist.add(new_fp)
    state["_fp_history"] = hist
    state["_fp"] = new_fp
    logger.debug("[apply_corrections] Completed — %d changes made, protected=%s", len(changes), protected_fields)
    return state
