from typing import Any, Dict, List, Tuple
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

    # ---------- Book-like guard ----------
    if _is_book_like(ex, best):
        protected_fields = {"journal_name", "journal_abbrev", "volume", "issue", "pages"}
        print(f"[apply_corrections] Detected BOOK-LIKE record → protecting {protected_fields}")
    else:
        protected_fields = set()
        print("[apply_corrections] Non-book type: unknown")

    # ---------- Authoritative year protection ----------
    best_year = coerce_year(best.get("year") or "")
    ex_year = coerce_year(ex.get("year") or "")

    all_prov_sources = {normalize_text(v) for v in prov.values() if isinstance(v, str)}
    authoritative_present = bool(
        all_prov_sources
        & {"ieeexplore", "crossref", "openalex", "semanticscholar", "doi-agreement", "earliest"}
    )

    if best_year and is_plausible_year(best_year) and authoritative_present:
        if ex_year and ex_year != best_year:
            print(
                f"[apply_corrections] Keeping authoritative year {best_year} "
                f"(authoritative sources detected: {all_prov_sources})"
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
        if k in protected_fields:
            continue
        if k == "year" and year_locked:
            continue

        bv = best.get(k)
        if not bv:
            continue

        prov_src = (prov.get(k) or "").lower()
        exv = normalize_text(ex.get(k, ""))
        bvn = normalize_text(bv)

        # Strong authoritative override
        if prov_src in {
            "crossref",
            "openalex",
            "semanticscholar",
            "ieeexplore",
            "doi-agreement",
        }:
            if exv != bvn:
                print(f"[apply_corrections] Authoritative override: {k} ← {bv} (from {prov_src})")
                changes.append((k, ex.get(k), bv))
                ex[k] = bv
                audit[k] = prov_src
            continue

        # Normal correction fallback
        if (k in _ALWAYS_REWRITE) or (k not in matching_fields):
            if exv != bvn:
                print(f"[apply_corrections] Updating {k} → {bv} (source={prov_src or 'consensus'})")
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
        if k in protected_fields:
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
    print(f"[apply_corrections] Completed — {len(changes)} changes made, protected={protected_fields}")
    return state
