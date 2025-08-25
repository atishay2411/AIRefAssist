import re, json
from ..state import PipelineState, ExtractedModel
from ..tools.utils import (
    normalize_text, authors_to_list, normalize_month_field,
)
ARXIV_RE = re.compile(r'(arxiv:)?\s*(\d{4}\.\d{4,5})(v\d+)?', re.I)
DOI_RE = re.compile(r'(10\.\d{4,9}/[^\s,;]+)', re.I)

async def parse_extract(state: PipelineState) -> PipelineState:
    ref, rtype = state["reference"], state["type"]
    llm = state["_llm"]
    prompt = (
        "Parse the IEEE-style reference. Return STRICT JSON. Keys among:\n"
        "title, authors (list or string), journal_name, journal_abbrev, conference_name,\n"
        "volume, issue, pages, year, month, doi, publisher, location, edition, isbn, url.\n"
        "Omit unknown or invalid keys.\n"
        "IMPORTANT: If any extracted field contains extra characters, unexpected full stops, "
        "or other formatting issues that make it unlikely to be correct, DO NOT extract it. JSON ONLY.\n\n"
        f"Type hint: {rtype}\nReference: {ref}"
    )

    parsed = await llm.json(prompt) or {}
    if isinstance(parsed.get("authors"), str): parsed["authors"] = authors_to_list(parsed["authors"])

    if not parsed:
        m = re.search(r"“([^”]{3,})”|\"([^\"]{3,})\"", ref)
        if m:
            parsed["title"] = (m.group(1) or m.group(2)).strip()
            prefix = ref[:m.start()]
            parsed["authors"] = authors_to_list(prefix)
        if (dm := DOI_RE.search(ref)): parsed["doi"] = dm.group(1)
        if (am := ARXIV_RE.search(ref)): parsed["arxiv_id"] = am.group(2)
        if (pm := re.search(r"pp\.?\s*([\d\u2013\u2014\-]+)", ref, flags=re.I)):
            parsed["pages"] = pm.group(1).replace("\u2013","-").replace("\u2014","-")
        if (vm := re.search(r"vol\.?\s*([0-9A-Za-z]+)", ref, flags=re.I)): parsed["volume"] = vm.group(1)
        if (im := re.search(r"no\.?\s*([0-9A-Za-z]+)", ref, flags=re.I)): parsed["issue"] = im.group(1)
        if (y := re.search(r"\b(19|20)\d{2}\b", ref)): parsed["year"] = y.group(0)
    if parsed.get("month"): parsed["month"] = normalize_month_field(parsed["month"])
    try:
        parsed = ExtractedModel(**parsed).dict(exclude_none=True)
    except Exception:
        ...
    state["extracted"] = parsed
    return state
