from ..state import PipelineState
from ..tools.utils import authors_to_list

def build_report(state: PipelineState) -> PipelineState:
    lines = []
    # Early exit message if input was invalid
    if state.get("_skip_pipeline"):
        lines.append(state.get("verification_message", "Reference validation failed."))
    else:
        changes = state.get("corrections", [])
        ver = state.get("verification", {})
        matching_fields = state.get("matching_fields", [])
        
        if matching_fields:
            lines.append(f"Fields matched with authoritative source: {', '.join(matching_fields)}")
        else:
            lines.append("No fields matched with authoritative sources.")
        
        if not changes:
            lines.append("No corrections were necessary.")
        else:
            lines.append("Corrections applied (field: old → new):")
            for f, old, new in changes:
                if f == "authors":
                    old_str = ", ".join(authors_to_list(old)) if isinstance(old, (str, list)) else str(old)
                    new_str = ", ".join(authors_to_list(new)) if isinstance(new, (str, list)) else str(new)
                    lines.append(f"- {f}: '{old_str}' → '{new_str}'")
                else:
                    lines.append(f"- {f}: '{old}' → '{new}'")
        
        failed = [k for k, v in ver.items() if not v]
        if failed:
            lines.append("Fields still needing attention: " + ", ".join(sorted(failed)))
        else:
            lines.append("All verification checks passed after corrections.")
    
    state["report"] = "\n".join(lines)
    return state