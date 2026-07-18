"""Single-pass reference analysis.

One LLM call validates the input, classifies its type, and extracts all
bibliographic fields (previously three sequential LLM calls: validate →
detect_type → parse_extract). Regex fallbacks fill identifiers the LLM
misses and handle the no-LLM case.

While the LLM runs, the Crossref citation-matching search (which needs only
the raw string, not the extraction) is prefetched concurrently — its
multi-second latency hides entirely behind the LLM call.
"""
import asyncio
import re

from ..logging import logger
from ..state import PipelineState, ExtractedModel
from ..tools.utils import authors_to_list, normalize_month_field
from ..tools.type_reconcile import reconcile_type

ARXIV_RE = re.compile(r'(arxiv:)?\s*(\d{4}\.\d{4,5})(v\d+)?', re.I)
DOI_RE = re.compile(r'(10\.\d{4,9}/[^\s,;]+)', re.I)
_YEAR_RE = re.compile(r"\b(18|19|20)\d{2}\b")

REF_TYPES = (
    "journal article, conference paper, book, book chapter, thesis, "
    "technical report, dataset, standard, software, preprint, other"
)

_PROMPT_TEMPLATE = """You are a bibliographic reference analyzer. Analyze the input and return STRICT JSON with exactly these keys:

{{
  "is_reference": true|false,   // is the input a bibliographic reference (complete or partial)?
  "type": "...",                // one of: {types}
  "fields": {{ ... }}           // extracted metadata; omit unknown keys
}}

Allowed keys inside "fields": title, authors (list of strings), journal_name,
journal_abbrev, conference_name, book_title, editors (list of strings), volume,
issue, pages, year, month, doi, arxiv_id, publisher, location, edition, isbn, url.

Rules:
- Copy values verbatim from the input; do NOT invent or correct anything.
- "authors" are the people who wrote the work; "editors" only for edited volumes ("(ed.)"/"(eds.)").
- If the input is clearly not a reference (a question, prose, code, etc.), set is_reference=false and fields={{}}.

Input:
{reference}
"""


def _heuristic_is_reference(ref: str) -> bool:
    """Conservative fallback when no LLM is available."""
    ref = (ref or "").strip()
    if len(ref) < 20:
        return False
    # Prose blobs (pasted chat answers, abstracts) carry years/quotes too —
    # but a single bibliographic reference is never this long.
    if len(ref) > 800:
        return False
    signals = 0
    if _YEAR_RE.search(ref):
        signals += 1
    if DOI_RE.search(ref) or "arxiv" in ref.lower():
        signals += 2
    if ref.count(",") >= 2:
        signals += 1
    if re.search(r"\bvol\.|\bno\.|\bpp\.|\bed\.|proceedings|journal|conference|press", ref, re.I):
        signals += 1
    if re.search(r'"[^"]{5,}"|“[^”]{5,}”', ref):
        signals += 1
    return signals >= 2


def _regex_fill(parsed: dict, ref: str) -> dict:
    """Fill identifier fields the LLM missed straight from the raw string."""
    if not parsed.get("doi") and (dm := DOI_RE.search(ref)):
        parsed["doi"] = dm.group(1).rstrip(".")
    if not parsed.get("arxiv_id") and (am := ARXIV_RE.search(ref)):
        parsed["arxiv_id"] = am.group(2)
    # Word boundaries + digit-first captures: without them "vol"/"no" match
    # inside words ("nodular" once yielded issue="dular"). Page numbers may
    # use thousands separators ("pp. 63,330–63,345") — Springer journals
    # routinely paginate past 60000.
    _NUM = r"\d{1,3}(?:,\d{3})+|\d+"
    if not parsed.get("pages") and (pm := re.search(
            rf"\bpp?\.\s*({_NUM})(?:\s*[–—-]\s*({_NUM}))?", ref, flags=re.I)):
        start = pm.group(1).replace(",", "")
        end = (pm.group(2) or "").replace(",", "")
        parsed["pages"] = f"{start}-{end}" if end else start
    if not parsed.get("volume") and (vm := re.search(r"\bvol\.?\s*(\d[0-9A-Za-z]*)", ref, flags=re.I)):
        parsed["volume"] = vm.group(1)
    if not parsed.get("issue") and (im := re.search(r"\bno\.?\s*(\d[0-9A-Za-z]*)", ref, flags=re.I)):
        parsed["issue"] = im.group(1)
    # Article numbers replace page ranges in many modern journals
    if not parsed.get("pages") and (am2 := re.search(r"\bart\.?\s*no\.?\s*(\w+)", ref, flags=re.I)):
        parsed["pages"] = am2.group(1)
    if not parsed.get("year") and (y := _YEAR_RE.search(ref)):
        parsed["year"] = y.group(0)
    if not parsed.get("title"):
        m = re.search(r"“([^”]{3,})”|\"([^\"]{3,})\"", ref)
        if m:
            parsed["title"] = (m.group(1) or m.group(2)).strip()
            if not parsed.get("authors"):
                parsed["authors"] = authors_to_list(ref[:m.start()])
    return parsed


def _chapter_heuristics(parsed: dict, ref: str) -> dict:
    """Handle "In: E. Ditor (ed.) Book Title. City: Publisher" book chapters."""
    m_in = re.search(r'\bIn:\s*(.+?)\.\s*([^:]+):\s*([^.,]+)', ref, flags=re.I)
    if not m_in:
        return parsed
    editors_raw = m_in.group(1).strip()
    parsed.setdefault("location", m_in.group(2).strip())
    parsed.setdefault("publisher", m_in.group(3).strip())
    if not parsed.get("editors"):
        editors = re.sub(r'\((ed|eds)\.\)', '', editors_raw, flags=re.I).strip()
        parsed["editors"] = [a.strip() for a in re.split(r',\s*|\s+and\s+', editors) if a.strip()]
    if not parsed.get("book_title"):
        m_title = re.search(r'\)\s*([^\.]+)\.', editors_raw + '. ' + ref[m_in.start():])
        if m_title:
            parsed["book_title"] = m_title.group(1).strip()
    return parsed


def _clean_doi(doi) -> str:
    """Bare canonical DOI: strips URL/doi: prefixes and trailing sentence
    punctuation/quotes, which are never part of a DOI (internal parentheses
    ARE legitimate: 10.1016/s0140-6736(97)11096-0)."""
    from ..tools.utils import bare_doi
    return bare_doi(doi).rstrip(".,;\"'”’")


async def analyze_reference(state: PipelineState) -> PipelineState:
    ref = state.get("reference")
    llm = state.get("_llm")

    if not ref or not isinstance(ref, str) or not ref.strip():
        state["_skip_pipeline"] = True
        state["verification_message"] = "Reference missing."
        state["verification"] = {"is_reference": False}
        state["extracted"] = {}
        return state

    # Normalize CSV/Excel-escaped quotes and stray numbering that survive
    # direct API calls (the UI splitter also does this) — a doubled-quote
    # title breaks quote-based extraction badly.
    ref = re.sub(r'"{2,}', '"', ref)
    ref = re.sub(r"^\s*[\"']?\s*\[\d+\]\s*", "", ref).strip()
    state["reference"] = ref

    # Prefetch: the biblio search needs only the raw string — start it now so
    # its latency overlaps the LLM call instead of following it.
    crossref = next((s for s in state.get("_sources") or []
                     if getattr(s, "NAME", "") == "crossref"), None)
    if crossref is not None and hasattr(crossref, "by_biblio"):
        state["_biblio_prefetch"] = asyncio.create_task(crossref.by_biblio(ref))

    result: dict = {}
    if llm is not None and getattr(llm, "provider", "dummy") != "dummy":
        result = await llm.json(_PROMPT_TEMPLATE.format(types=REF_TYPES, reference=ref)) or {}

    if "is_reference" in result:
        is_reference = bool(result["is_reference"])
    else:
        # LLM unavailable or gave no usable answer — heuristics, not rejection.
        is_reference = _heuristic_is_reference(ref)
        logger.debug("[analyze_reference] LLM unavailable; heuristic verdict=%s", is_reference)

    if not is_reference:
        pre = state.get("_biblio_prefetch")
        if pre is not None:
            pre.cancel()
            state["_biblio_prefetch"] = None
        state["_skip_pipeline"] = True
        state["verification_message"] = "Input does not look like a bibliographic reference."
        state["verification"] = {"is_reference": False}
        state["extracted"] = {}
        return state

    parsed = result.get("fields") or {}
    if not isinstance(parsed, dict):
        parsed = {}
    if isinstance(parsed.get("authors"), str):
        parsed["authors"] = authors_to_list(parsed["authors"])
    if isinstance(parsed.get("editors"), str):
        parsed["editors"] = authors_to_list(parsed["editors"])

    parsed = _regex_fill(parsed, ref)
    parsed = _chapter_heuristics(parsed, ref)

    if parsed.get("doi"):
        parsed["doi"] = _clean_doi(parsed["doi"])
    if parsed.get("title"):
        # Quote-extracted titles keep the trailing citation comma: "Title,"
        parsed["title"] = str(parsed["title"]).strip().rstrip(",")
    if parsed.get("pages"):
        # Strip thousands separators the LLM may have carried over verbatim
        parsed["pages"] = re.sub(r"(\d),(?=\d{3}\b)", r"\1", str(parsed["pages"]))
    if parsed.get("month"):
        parsed["month"] = normalize_month_field(parsed["month"])
    if parsed.get("year") is not None:
        parsed["year"] = str(parsed["year"])

    try:
        model = ExtractedModel(**{k: v for k, v in parsed.items()})
        dump = getattr(model, "model_dump", None) or model.dict  # pydantic v2 / v1
        parsed = dump(exclude_none=True)
    except Exception:
        pass  # keep raw dict if coercion fails

    state["_llm_type_vote"] = (result.get("type") or "").lower() or None
    state["type"] = reconcile_type(
        candidates=[], llm_vote=state["_llm_type_vote"], reference=ref
    )
    state["extracted"] = parsed
    # Immutable snapshot of what the USER actually supplied — correction
    # guards compare against this, not the round-by-round mutated copy.
    state["_original_extracted"] = dict(parsed)
    state["_skip_pipeline"] = False
    state["verification_message"] = "Reference detected, proceeding with pipeline."
    state["verification"] = {"is_reference": True}
    return state
