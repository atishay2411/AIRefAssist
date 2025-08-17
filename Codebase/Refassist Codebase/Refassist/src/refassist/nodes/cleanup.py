from ..state import PipelineState

async def cleanup(state: PipelineState) -> PipelineState:
    try:
        if state.get("_http") is not None:
            await state["_http"].aclose()
    except Exception:
        ...
    try:
        llm = state.get("_llm")
        if llm and llm.provider == "ollama" and getattr(llm, "_client", None) is not None:
            await llm._client.aclose()
    except Exception:
        ...
    return state
