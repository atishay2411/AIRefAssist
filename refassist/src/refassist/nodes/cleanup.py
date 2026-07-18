from ..state import PipelineState

async def cleanup(state: PipelineState) -> PipelineState:
    # Only close per-invocation resources we own. The HTTP client and the
    # LLM adapter are shared across pipeline runs and stay open for the
    # lifetime of the process.
    try:
        if state.get("_http") is not None and state.get("_owns_http", False):
            await state["_http"].aclose()
    except Exception:
        ...
    return state
