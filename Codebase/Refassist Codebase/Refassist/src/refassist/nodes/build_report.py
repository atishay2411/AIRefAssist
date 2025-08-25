from ..state import PipelineState

def build_report(state: PipelineState) -> PipelineState:
    lines = []
    # Early exit message if input was invalid
    if state.get("_skip_pipeline"):
        lines.append(state.get("verification_message", "Reference validation failed."))
    else:
        changes = state.get("corrections") or []
        ver = state.get("verification") or {}
        if not changes:
            lines.append("No corrections were necessary; reference matched authoritative sources.")
        else:
            lines.append("Corrections (field: old → new):")
            for f, old, new in changes:
                lines.append(f"- {f}: '{old}' → '{new}'")
        failed = [k for k, v in ver.items() if not v]
        if failed:
            lines.append("Fields still needing attention: " + ", ".join(sorted(failed)))
        else:
            lines.append("All verification checks passed after corrections.")
    state["report"] = "\n".join(lines)
    return state
