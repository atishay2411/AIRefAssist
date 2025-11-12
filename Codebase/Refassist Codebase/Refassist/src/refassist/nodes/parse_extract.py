import re
import json
from ..state import PipelineState, ExtractedModel
from ..tools.utils import (
    normalize_text, authors_to_list, normalize_month_field,
)

# --- Regex patterns ---
ARXIV_RE = re.compile(r'(arxiv:)?\s*(\d{4}\.\d{4,5})(v\d+)?', re.I)
DOI_RE = re.compile(r'(10\.\d{4,9}/[^\s,;]+)', re.I)


async def parse_extract(state: PipelineState) -> PipelineState:
    """
    Extract structured fields (authors, title, container, etc.) from a freeform reference string.
    Includes robust handling for book chapters (e.g., "In: P. Panayi (ed.) Racial Violence...").
    """
    ref, rtype = state["reference"], state["type"]
    llm = state["_llm"]

    # --- Primary LLM extraction ---
    prompt = (
        "Parse this reference (may not be IEEE formatted). Return STRICT JSON. "
        "Allowed keys: title, authors (list|string), journal_name, journal_abbrev, "
        "conference_name, book_title, editors (list|string), volume, issue, pages, "
        "year, month, doi, publisher, location, edition, isbn, url. "
        "Omit unknown keys. JSON ONLY.\n\n"
        f"Type hint: {rtype}\nReference: {ref}"
    )

    parsed = await llm.json(prompt) or {}

    # --- Normalize author list ---
    if isinstance(parsed.get("authors"), str):
        parsed["authors"] = authors_to_list(parsed["authors"])

    # --- Fallback regex parsing when LLM fails ---
    if not parsed:
        # Try to capture the title between quotes
        m = re.search(r"“([^”]{3,})”|\"([^\"]{3,})\"", ref)
        if m:
            parsed["title"] = (m.group(1) or m.group(2)).strip()
            prefix = ref[:m.start()]
            parsed["authors"] = authors_to_list(prefix)

        # DOI / arXiv / pages / vol / issue / year
        if (dm := DOI_RE.search(ref)):
            parsed["doi"] = dm.group(1)
        if (am := ARXIV_RE.search(ref)):
            parsed["arxiv_id"] = am.group(2)
        if (pm := re.search(r"pp\.?\s*([\d\u2013\u2014\-]+)", ref, flags=re.I)):
            parsed["pages"] = pm.group(1).replace("\u2013", "-").replace("\u2014", "-")
        if (vm := re.search(r"vol\.?\s*([0-9A-Za-z]+)", ref, flags=re.I)):
            parsed["volume"] = vm.group(1)
        if (im := re.search(r"no\.?\s*([0-9A-Za-z]+)", ref, flags=re.I)):
            parsed["issue"] = im.group(1)
        if (y := re.search(r"\b(19|20)\d{2}\b", ref)):
            parsed["year"] = y.group(0)

    # --- Normalize month field if present ---
    if parsed.get("month"):
        parsed["month"] = normalize_month_field(parsed["month"])

    # --- Extra: heuristic parsing for “In: … (ed.) … Publisher” book chapters ---
    # Example:
    # "In: P. Panayi (ed.) Racial Violence in Britain in the Nineteenth and Twentieth Centuries. Leicester: Leicester University Press."
    m_in = re.search(r'\bIn:\s*(.+?)\.\s*([^:]+):\s*([^.,]+)', ref, flags=re.I)
    if m_in:
        editors_raw = m_in.group(1).strip()      # e.g., "P. Panayi (ed.) Racial Violence in Britain..."
        loc = m_in.group(2).strip()
        pub = m_in.group(3).strip()

        # Extract editors and book title
        # Remove (ed.) or (eds.) then split into names
        editors = re.sub(r'\((ed|eds)\.\)', '', editors_raw, flags=re.I).strip()
        parsed["editors"] = [a.strip() for a in re.split(r',\s*|\s+and\s+', editors) if a.strip()]

        parsed["location"] = parsed.get("location") or loc
        parsed["publisher"] = parsed.get("publisher") or pub

        # Try to extract the book title following editors
        # e.g., "P. Panayi (ed.) Racial Violence in Britain in the Nineteenth and Twentieth Centuries."
        m_title = re.search(r'\)\s*([^\.]+)\.', editors_raw + '. ' + ref[m_in.start():])
        if m_title:
            parsed["book_title"] = m_title.group(1).strip()

    # --- Validate and coerce to ExtractedModel ---
    try:
        parsed = ExtractedModel(**parsed).dict(exclude_none=True)
    except Exception:
        # If something doesn’t validate, just skip coercion
        pass

    state["extracted"] = parsed
    return state
