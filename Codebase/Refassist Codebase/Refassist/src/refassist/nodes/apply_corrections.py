from typing import Any, Dict, List, Tuple
from ..state import PipelineState
from ..tools.utils import normalize_text, authors_to_list, normalize_month_field, fingerprint_state

def apply_corrections(state: PipelineState) -> PipelineState:
    ex = dict(state["extracted"])
    best = state.get("best", {})
    suggestions = state.get("suggestions", {})
    matching_fields = state.get("matching_fields", [])
    changes: List[Tuple[str, Any, Any]] = []

    # Fields to consider for correction
    fields = ["title", "authors", "journal_name", "journal_abbrev", "volume", "issue", "pages", "doi", "year", "month", "conference_name", "publisher", "location", "edition", "isbn", "url"]

    # Apply corrections from best candidate for non-matching fields
    for k in fields:
        if k == "authors" or k not in matching_fields:
            bv = best.get(k)
            if bv and normalize_text(ex.get(k, "")) != normalize_text(bv):
                changes.append((k, ex.get(k), bv))
                ex[k] = bv

    # Apply suggestions from verify_agents (prioritize for authors)
    for k, v in suggestions.items():
        if k == "authors" or k not in matching_fields:
            if normalize_text(ex.get(k, "")) != normalize_text(v):
                changes.append((k, ex.get(k), v))
                ex[k] = v

    # Normalize authors if needed
    if isinstance(ex.get("authors"), str):
        al = authors_to_list(ex["authors"])
        if al != ex["authors"]:
            changes.append(("authors_list", ex["authors"], al))
            ex["authors"] = al

    # Normalize month
    if ex.get("month"):
        newm = normalize_month_field(ex["month"])
        if newm != ex["month"]:
            changes.append(("month_normalized", ex["month"], newm))
            ex["month"] = newm

    state["extracted"] = ex
    state["corrections"] = state.get("corrections", []) + changes
    state["attempts"] = state.get("attempts", 0) + 1
    state["_made_changes_last_cycle"] = bool(changes)

    # Update fingerprint for loop detection
    sugg = state.get("suggestions", {})
    best_now = state.get("best", {})
    new_fp = fingerprint_state(ex, best_now, sugg)
    hist = state.get("_fp_history", set())
    state["_loop_detected"] = new_fp in hist
    hist.add(new_fp)
    state["_fp_history"] = hist
    state["_fp"] = new_fp
    return state