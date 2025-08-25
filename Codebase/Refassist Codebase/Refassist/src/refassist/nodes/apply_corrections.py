from typing import Any, Dict, List, Tuple
from ..state import PipelineState
from ..tools.utils import (
    normalize_text, authors_to_list, normalize_month_field, fingerprint_state
)

def apply_corrections(state: PipelineState) -> PipelineState:
    ex = dict(state["extracted"])
    best = state.get("best") or {}
    suggestions = state.get("suggestions") or {}
    changes: List[Tuple[str, Any, Any]] = []

    for k in ("title","authors","journal_name","journal_abbrev","volume","issue","pages","doi","year","month","conference_name","publisher","location","edition","isbn","url"):
        bv = best.get(k)
        if bv and normalize_text(ex.get(k)) != normalize_text(bv):
            changes.append((k, ex.get(k), bv)); ex[k] = bv

    if ex.get("title"):
        sc = ex["title"]
        if sc != ex["title"]:
            changes.append(("title_sentence_case", ex["title"], sc)); ex["title"] = sc

    if isinstance(ex.get("authors"), str):
        al = authors_to_list(ex["authors"])
        if al != ex["authors"]:
            changes.append(("authors_list", ex["authors"], al)); ex["authors"] = al

    for k, v in suggestions.items():
        if normalize_text(ex.get(k)) != normalize_text(v):
            changes.append((k, ex.get(k), v)); ex[k] = v

    if ex.get("month"):
        newm = normalize_month_field(ex["month"])
        if newm != ex["month"]:
            changes.append(("month_normalized", ex["month"], newm)); ex["month"] = newm

    state["extracted"] = ex
    state["corrections"] = (state.get("corrections") or []) + changes
    state["attempts"] = (state.get("attempts") or 0) + 1
    state["_made_changes_last_cycle"] = bool(changes)

    sugg = state.get("suggestions") or {}
    best_now = state.get("best") or {}
    new_fp = fingerprint_state(ex, best_now, sugg)
    hist = state.get("_fp_history") or set()
    state["_loop_detected"] = new_fp in hist
    hist.add(new_fp)
    state["_fp_history"] = hist
    state["_fp"] = new_fp
    return state
