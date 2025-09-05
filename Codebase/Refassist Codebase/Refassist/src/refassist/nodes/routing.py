from ..state import PipelineState

def should_exit(state: PipelineState) -> bool:
    cfg = state.get("_cfg")
    if state.get("_loop_detected"): return True
    if (state.get("hops") or 0) >= cfg.max_hops: return True
    if (state.get("attempts") or 0) >= cfg.max_correction_rounds: return True
    if (state.get("_stagnation") or 0) >= cfg.stagnation_patience: return True
    if not state.get("_made_changes_last_cycle") and (state.get("_stagnation",0) >= 1): return True
    ver = state.get("verification") or {}
    return bool(ver) and all(ver.values())

def route_after_verify(state: PipelineState) -> str:
    return "FormatReference" if should_exit(state) else "ApplyCorrections"
