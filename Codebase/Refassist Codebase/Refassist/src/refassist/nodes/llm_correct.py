import json
from ..state import PipelineState, ExtractedModel
from ..tools.utils import authors_to_list, normalize_month_field, normalize_text, fingerprint_state

async def llm_correct(state: PipelineState) -> PipelineState:
    ref = state["reference"]; ex = state["extracted"]; ver = state.get("verification") or {}
    llm = state["_llm"]
    prompt = (
        "You are an IEEE reference corrector. Given raw reference, current JSON, and verification booleans, "
        "return STRICT JSON correcting the fields. Keys among: title, authors (list), journal_name, journal_abbrev, "
        "conference_name, volume, issue, pages, year, month, doi, publisher, location, edition, isbn, url. "
        "Omit unknown keys. JSON ONLY.\n\n"
        f"Raw: {ref}\n\nCurrent: {json.dumps(ex, ensure_ascii=False)}\n\nVerification: {json.dumps(ver)}"
    )
    patch = await llm.json(prompt) or {}
    if isinstance(patch.get("authors"), str): patch["authors"] = authors_to_list(patch["authors"])
    if patch.get("month"): patch["month"] = normalize_month_field(patch["month"])
    try:
        patch = ExtractedModel(**patch).dict(exclude_none=True)
    except Exception:
        patch = {}
    ex2 = dict(ex); changes = []
    for k, v in patch.items():
        if normalize_text(ex2.get(k)) != normalize_text(v):
            changes.append((k, ex2.get(k), v)); ex2[k] = v
    state["extracted"] = ex2
    state["corrections"] = (state.get("corrections") or []) + changes
    state["_made_changes_last_cycle"] = state.get("_made_changes_last_cycle", False) or bool(changes)
    best = state.get("best") or {}
    sugg = state.get("suggestions") or {}
    state["_fp"] = fingerprint_state(ex2, best, sugg)
    return state
