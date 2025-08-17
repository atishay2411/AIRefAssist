from ..state import PipelineState
from ..tools.utils import normalize_month_field

def enrich_from_best(state: PipelineState) -> PipelineState:
    ex = dict(state["extracted"]); be = state.get("best") or {}
    for k in ("journal_abbrev","journal_name","volume","issue","pages","year","month","doi","conference_name","publisher","location","edition","isbn","url","title","authors"):
        if not ex.get(k) and be.get(k):
            ex[k] = be.get(k)
    if ex.get("month"):
        ex["month"] = normalize_month_field(ex["month"])
    state["extracted"] = ex
    return state
