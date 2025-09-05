from typing import Any, Dict, List, Tuple
from ..state import PipelineState
from ..tools.utils import normalize_text, authors_to_list, normalize_month_field, fingerprint_state
import re

_ALWAYS_REWRITE = {"title","authors","year","month","doi"}  # critical truth fields

def _is_single_numeric_page(s: str) -> bool:
    s = normalize_text(s)
    return bool(s) and ("-" not in s) and bool(re.fullmatch(r"\d+", s))

def _extract_first_number(s: str) -> str:
    m = re.search(r"\d+", normalize_text(s))
    return m.group(0) if m else ""

def _is_range_with_two_numbers(s: str) -> bool:
    s = normalize_text(s).replace("—","-").replace("–","-")
    if "-" not in s:
        return False
    nums = re.findall(r"\d+", s)
    return len(nums) >= 2

def _first_num(s: str) -> str:
    m = re.search(r"\d+", normalize_text(s))
    return m.group(0) if m else ""

def apply_corrections(state: PipelineState) -> PipelineState:
    ex = dict(state["extracted"])
    best = state.get("best", {}) or {}
    prov = state.get("provenance", {}) or {}
    suggestions = state.get("suggestions", {}) or {}
    matching_fields = set(state.get("matching_fields", []))
    changes: List[Tuple[str, Any, Any]] = []

    # store field -> source audit for the final report
    audit = dict(state.get("audit", {}))

    fields = [
        "title","authors","journal_name","journal_abbrev","volume","issue","pages",
        "doi","year","month","conference_name","publisher","location","edition","isbn","url"
    ]

    # Apply consensus-best (force for core truth fields)
    for k in fields:
        bv = best.get(k)
        if not bv: 
            continue
        if (k in _ALWAYS_REWRITE) or (k not in matching_fields):
            if normalize_text(ex.get(k, "")) != normalize_text(bv):
                changes.append((k, ex.get(k), bv))
                ex[k] = bv
                if prov.get(k):
                    audit[k] = prov.get(k)

    # ---------- PAGES ENRICHMENT (best -> candidates) ----------
    # Step A: upgrade from best if compatible (existing logic)
    ex_pages = normalize_text(ex.get("pages", ""))
    be_pages = normalize_text(best.get("pages", ""))

    if ex_pages and be_pages:
        if _is_single_numeric_page(ex_pages) and _is_range_with_two_numbers(be_pages):
            ex_start = _extract_first_number(ex_pages)
            be_first = _extract_first_number(be_pages)
            if ex_start and be_first and ex_start == be_first:
                if normalize_text(ex.get("pages","")) != be_pages:
                    changes.append(("pages", ex.get("pages"), be_pages))
                    ex["pages"] = be_pages
                    audit.setdefault("pages", prov.get("pages","consensus"))

    # Step B: if still single page, search ALL candidates for a richer range
    # (useful when consensus best lacks a range but another trusted source has it)
    ex_pages_now = normalize_text(ex.get("pages", ""))
    if _is_single_numeric_page(ex_pages_now):
        target_start = _extract_first_number(ex_pages_now)
        cand_source = None
        cand_range = None

        # Candidates are already normalized in state["candidates"]
        for c in state.get("candidates", []) or []:
            cp = normalize_text(c.get("pages",""))
            if _is_range_with_two_numbers(cp):
                cstart = _extract_first_number(cp)
                if cstart and target_start and cstart == target_start:
                    # Prefer the longest string (most detail) among matches
                    if not cand_range or len(cp) > len(cand_range):
                        cand_range = cp
                        cand_source = c.get("source") or "candidates"

        if cand_range and cand_range != ex_pages_now:
            changes.append(("pages", ex.get("pages"), cand_range))
            ex["pages"] = cand_range
            audit.setdefault("pages", cand_source or "candidates")

    # Suggestions on top
    for k, v in suggestions.items():
        if (k in _ALWAYS_REWRITE) or (k not in matching_fields):
            if normalize_text(ex.get(k, "")) != normalize_text(v):
                changes.append((k, ex.get(k), v))
                ex[k] = v
                audit.setdefault(k, "verify/llm")

    # Normalize
    if isinstance(ex.get("authors"), str):
        al = authors_to_list(ex["authors"])
        if al != ex["authors"]:
            changes.append(("authors_list", ex["authors"], al))
            ex["authors"] = al
            audit.setdefault("authors","normalize")

    if ex.get("month"):
        newm = normalize_month_field(ex["month"])
        if newm != ex["month"]:
            changes.append(("month_normalized", ex["month"], newm))
            ex["month"] = newm
            audit.setdefault("month","normalize")

    state["extracted"] = ex
    state["corrections"] = state.get("corrections", []) + changes
    state["attempts"] = state.get("attempts", 0) + 1
    state["_made_changes_last_cycle"] = bool(changes)
    state["audit"] = audit

    sugg = state.get("suggestions", {})
    best_now = state.get("best", {})
    new_fp = fingerprint_state(ex, best_now, sugg)
    hist = state.get("_fp_history", set())
    state["_loop_detected"] = new_fp in hist
    hist.add(new_fp)
    state["_fp_history"] = hist
    state["_fp"] = new_fp
    return state
