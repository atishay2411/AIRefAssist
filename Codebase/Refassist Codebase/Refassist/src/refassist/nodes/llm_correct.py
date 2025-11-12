from ..state import PipelineState
from ..tools.utils import normalize_text

BOOK_PROTECT = {"journal_name", "journal_abbrev", "volume", "issue", "pages"}

def _is_book_like(ex):
    t = normalize_text(ex.get("type",""))
    if "book" in t:
        return True
    if ex.get("publisher") and not ex.get("journal_name"):
        return True
    return False

def llm_correct(state: PipelineState) -> PipelineState:
    ex = state.get("extracted", {})
    prov = state.get("provenance", {}) or {}
    if _is_book_like(ex):
        # Do NOT enforce journal-ish fields for books
        for k in BOOK_PROTECT:
            if k in ex:
                # no-op: keep whatever extracted has; do not overwrite
                pass
        return state

    # Otherwise your prior enforcement logic can run as-is,
    # preferably checking that provenance is authoritative (crossref/openalex/etc).
    return state
