import json
from ..state import PipelineState, ExtractedModel
from ..tools.utils import authors_to_list, normalize_month_field, normalize_text, fingerprint_state

# Never let LLM override authoritative values once set
_LOCK_ALWAYS = {"doi", "year", "month", "title", "authors", "pages"}  # pages added to prevent regressions
_LOCK_IF_PRESENT = {"journal_name", "journal_abbrev", "conference_name", "volume", "issue"}

def _coerce_year(y: str) -> str:
    y = normalize_text(y)
    if len(y) >= 4:
        for i in range(len(y) - 3):
            seg = y[i:i+4]
            if seg.isdigit() and (1800 <= int(seg) <= 2100):
                return seg
    return ""

async def llm_correct(state: PipelineState) -> PipelineState:
    ref = state["reference"]
    ex = state["extracted"]
    ver = state.get("verification") or {}
    llm = state["_llm"]

    # Authoritative values from online consensus/best
    best = state.get("best") or {}

    # Build lock set
    lock_fields = set()
    for k in _LOCK_ALWAYS:
        if normalize_text(best.get(k)):
            lock_fields.add(k)
    for k in _LOCK_IF_PRESENT:
        if normalize_text(best.get(k)):
            lock_fields.add(k)

    # Build a *frozen* entity pack for the LLM (for transparency, but not mutable)
    frozen_entities = {k: best.get(k) for k in ["title","authors","journal_name","conference_name","year","month","volume","issue","pages","doi"] if best.get(k)}
    prompt = (
        "You are an IEEE reference corrector.\n"
        "Given the raw reference, the current JSON, and a pack of VERIFIED fields from online sources, "
        "produce a STRICT JSON patch correcting only fields that are missing or obviously malformed.\n"
        "Under NO circumstances change any VERIFIED field. Keys among: title, authors (list), journal_name, "
        "journal_abbrev, conference_name, volume, issue, pages, year, month, doi, publisher, location, edition, isbn, url. "
        "If unsure, omit the key. JSON ONLY.\n\n"
        f"Raw: {ref}\n\nCurrent: {json.dumps(ex, ensure_ascii=False)}\n\n"
        f"Verified (frozen): {json.dumps(frozen_entities, ensure_ascii=False)}\n\n"
        f"Verification flags: {json.dumps(ver)}"
    )
    patch = await llm.json(prompt) or {}

    # Normalize authors + month + year
    if isinstance(patch.get("authors"), str):
        patch["authors"] = authors_to_list(patch["authors"])
    if patch.get("month"):
        patch["month"] = normalize_month_field(patch["month"])
    if patch.get("year"):
        patch["year"] = _coerce_year(str(patch["year"]))

    # Validate best-effort
    try:
        patch = ExtractedModel(**patch).dict(exclude_none=True)
    except Exception:
        patch = {}

    ex2 = dict(ex)
    changes = []

    # Apply LLM patch only to fields that are NOT locked and improve data
    for k, v in patch.items():
        if k in lock_fields:
            continue
        old = ex2.get(k)
        if normalize_text(old) != normalize_text(v) and normalize_text(v):
            ex2[k] = v
            changes.append((k, old, v))

    # Enforce authoritative locks from 'best' after applying LLM patch
    for k in lock_fields:
        bv = best.get(k)
        if k == "year":
            bv = _coerce_year(str(bv))
        if k == "month" and bv:
            bv = normalize_month_field(bv)
        if normalize_text(ex2.get(k)) != normalize_text(bv):
            changes.append((k, ex2.get(k), bv))
            ex2[k] = bv

    state["extracted"] = ex2
    state["corrections"] = (state.get("corrections") or []) + changes
    state["_made_changes_last_cycle"] = state.get("_made_changes_last_cycle", False) or bool(changes)

    # Update fingerprint
    sugg = state.get("suggestions") or {}
    best_now = state.get("best") or {}
    state["_fp"] = fingerprint_state(ex2, best_now, sugg)
    return state
