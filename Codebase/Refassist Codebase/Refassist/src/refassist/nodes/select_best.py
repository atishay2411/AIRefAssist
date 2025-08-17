from ..state import PipelineState
from ..tools.scoring import score_candidate, is_trustworthy_match

def select_best(state: PipelineState) -> PipelineState:
    ex = state["extracted"]; candidates = state.get("candidates") or []
    if not candidates:
        state["best"] = {}
        return state
    best, best_score = None, -1.0
    for c in candidates:
        sc = score_candidate(ex, c)
        if sc > best_score:
            best, best_score = c, sc
    if best and not is_trustworthy_match(ex, best):
        best = {}
    state["best"] = best or {}
    return state
