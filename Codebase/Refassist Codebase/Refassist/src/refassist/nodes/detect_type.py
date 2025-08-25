import re
from ..state import PipelineState
from ..tools.type_reconcile import reconcile_type

async def detect_type(state: PipelineState) -> PipelineState:
    ref = state["reference"]
    llm = state["_llm"]

    # Ask LLM for type classification
    vote = await llm.json(
        "Classify this reference into one of: journal article, conference paper, book, book chapter, "
        "thesis, technical report, dataset, standard, software, other. "
        "Return JSON {\"type\": \"...\"}. Ref:\n" + ref
    )

    # Print LLM output for debugging
    print("=== LLM Type Vote ===")
    print(vote)
    print("=====================")

    # Save the LLM type vote in state
    state["_llm_type_vote"] = (vote or {}).get("type")

    # Use online candidates if available; otherwise empty list
    candidates = state.get("candidates", [])

    # Reconcile using only LLM + online sources
    state["type"] = reconcile_type(candidates=candidates, llm_vote=state["_llm_type_vote"])
    
    return state
