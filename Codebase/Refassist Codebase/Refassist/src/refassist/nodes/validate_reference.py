import re
import json
from typing import Optional
from ..state import PipelineState

async def validate_input_reference(state: PipelineState) -> PipelineState:
    ref = state.get("reference")
    llm = state.get("_llm")

    if not ref or not isinstance(ref, str) or not llm:
        state["_skip_pipeline"] = True
        state["verification_message"] = "Reference missing or LLM not initialized."
        state["verification"] = {"is_reference": False}
        return state

    # LLM-first (and only) check
    is_reference = False
    source = "model"
    prompt = (
        "You are a bibliographic reference detector.\n"
        "Decide if the input is a complete reference (journal, conference, book, etc.).\n"
        "Respond ONLY with JSON: {\"is_reference\": true} or {\"is_reference\": false}.\n"
        f"Input:\n{ref}\nOutput:"
    )

    try:
        raw_json = await llm.json(prompt)
        if isinstance(raw_json, dict) and "is_reference" in raw_json:
            is_reference = bool(raw_json["is_reference"])
    except Exception as e:
        print(">> LLM call failed:", repr(e))
        is_reference = False

    # No heuristic fallback
    state["_skip_pipeline"] = not is_reference
    state["verification_message"] = (
        "Reference detected, proceeding with pipeline." if is_reference
        else "Reference invalid or incomplete."
    )
    state["verification"] = {"is_reference": is_reference}

    return state
