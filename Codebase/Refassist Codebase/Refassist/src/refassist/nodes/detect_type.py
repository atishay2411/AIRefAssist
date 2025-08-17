import re
from ..state import PipelineState
from ..tools.type_reconcile import reconcile_type

async def detect_type(state: PipelineState) -> PipelineState:
    ref = state["reference"]
    rtype = "other"
    if re.search(r"\bvol\.|no\.|pp\.", ref, flags=re.I): rtype = "journal article"
    if re.search(r"\bin\b.+(proc|conference|symposium|workshop)", ref, flags=re.I): rtype = "conference paper"
    if re.search(r"\bISBN\b", ref, flags=re.I): rtype = "book"
    llm = state["_llm"]
    vote = await llm.json(
        "Classify this reference into one of: journal article, conference paper, book, book chapter, thesis, technical report, dataset, standard, software, other. "
        "Return JSON {\"type\": \"...\"}. Ref:\n" + ref
    )
    state["_llm_type_vote"] = (vote or {}).get("type")
    state["type"] = reconcile_type(rtype, [], state["_llm_type_vote"])
    return state
