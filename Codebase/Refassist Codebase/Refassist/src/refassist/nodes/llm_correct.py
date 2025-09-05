import json
from ..state import PipelineState, ExtractedModel
from ..tools.utils import authors_to_list, normalize_month_field, normalize_text, fingerprint_state

# Fields we never allow the LLM to override once we have an authoritative value
# from online sources (best/consensus).
_LOCK_ALWAYS = {"doi", "year", "month", "title", "authors"}
# Additional fields we lock if present (helps prevent volume/issue/pages regressions)
_LOCK_IF_PRESENT = {"journal_name", "journal_abbrev", "conference_name", "volume", "issue", "pages"}

def _coerce_year(y: str) -> str:
    y = normalize_text(y)
    # Keep only a 4-digit year if present; otherwise return empty to avoid garbage like "Aug 1987"
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

    # Build lock set: always lock _LOCK_ALWAYS if best has a value; for others, lock when present in best
    lock_fields = set()
    for k in _LOCK_ALWAYS:
        if normalize_text(best.get(k)):
            lock_fields.add(k)
    for k in _LOCK_IF_PRESENT:
        if normalize_text(best.get(k)):
            lock_fields.add(k)

    # Ask LLM for a patch, but it will be filtered below.
    prompt = (
        "You are an IEEE reference corrector. Given raw reference, current JSON, and verification booleans, "
        "return STRICT JSON correcting only fields that are missing or obviously malformed. "
        "Keys among: title, authors (list), journal_name, journal_abbrev, conference_name, volume, issue, pages, "
        "year, month, doi, publisher, location, edition, isbn, url. "
        "If you are not sure, omit the key. JSON ONLY.\n\n"
        f"Raw: {ref}\n\nCurrent: {json.dumps(ex, ensure_ascii=False)}\n\nVerification: {json.dumps(ver)}"
    )
    patch = await llm.json(prompt) or {}

    # Normalize authors + month
    if isinstance(patch.get("authors"), str):
        patch["authors"] = authors_to_list(patch["authors"])
    if patch.get("month"):
        patch["month"] = normalize_month_field(patch["month"])
    if patch.get("year"):
        patch["year"] = _coerce_year(str(patch["year"]))

    # Validate patch against pydantic model (best-effort)
    try:
        patch = ExtractedModel(**patch).dict(exclude_none=True)
    except Exception:
        patch = {}

    ex2 = dict(ex)
    changes = []

    # 1) Apply LLM patch only to fields that are NOT locked and actually improve data
    for k, v in patch.items():
        if k in lock_fields:
            # Ignore LLM for locked fields (we trust online sources over LLM guesses)
            continue
        old = ex2.get(k)
        if normalize_text(old) != normalize_text(v) and normalize_text(v):
            ex2[k] = v
            changes.append((k, old, v))

    # 2) Enforce authoritative locks from 'best' after applying LLM patch
    for k in lock_fields:
        bv = best.get(k)
        if k == "year":
            bv = _coerce_year(str(bv))
        if k == "month" and bv:
            bv = normalize_month_field(bv)
        if normalize_text(ex2.get(k)) != normalize_text(bv):
            changes.append((k, ex2.get(k), bv))
            ex2[k] = bv

    # 3) Keep basic normalizations
    if isinstance(ex2.get("authors"), str):
        # Should not happen, but guard anyway
        ex2["authors"] = authors_to_list(ex2["authors"])

    state["extracted"] = ex2
    state["corrections"] = (state.get("corrections") or []) + changes
    state["_made_changes_last_cycle"] = state.get("_made_changes_last_cycle", False) or bool(changes)

    # Update fingerprint for loop detection
    sugg = state.get("suggestions") or {}
    best_now = state.get("best") or {}
    state["_fp"] = fingerprint_state(ex2, best_now, sugg)
    return state
