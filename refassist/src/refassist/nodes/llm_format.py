from __future__ import annotations
import re
from typing import Dict, Any

from ..logging import logger
from ..rag.service import build_query_from_state, get_style_guide_service
from ..tools.utils import authors_to_list, normalize_text

# ---------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert IEEE reference formatter.
Your task is to generate an EXACT IEEE-style reference for the given metadata.

Follow the IEEE Editorial Style Manual exactly and only use the retrieved style guide snippets.
Do not invent or hallucinate missing data.
If a field is missing, omit it gracefully.

Formatting rules:
- For authors:
  * Up to six authors: list all.
  * Seven or more: list first author followed by “et al.”
  * Use initials for given and middle names (F.M. Lastname).
  * Preserve compound surnames (van der Waals).
  * No spaces between initials.
- Page numbers:
  * Multiple pages → “pp.”
  * Single page → “p.”
- Include DOI or URL only if provided.
- Respect capitalization, punctuation, and italicization.
- Never rewrite titles; only correct casing.

Return only the final IEEE-style reference, one line.
"""


# ---------------------------------------------------------------------
# Helper — sanitize LLM output to remove hallucinated API errors
# ---------------------------------------------------------------------
def sanitize_llm_output(text: str) -> str:
    if not text:
        return ""

    forbidden = [
        "Missing credentials",
        "api_key",
        "AzureOpenAI",
        "OpenAI API key",
        "unauthorized",
        "invalid key",
        "Please pass one of",
    ]

    low = text.lower()
    if any(bad.lower() in low for bad in forbidden):
        return ""  # force fallback to rule-based formatter

    return text.strip()


def _norm_for_check(s: str) -> str:
    return (normalize_text(s)
            .replace("–", "-").replace("—", "-")
            .replace("“", '"').replace("”", '"')
            .lower())


def check_faithfulness(formatted: str, fields: Dict[str, Any]) -> str:
    """Verify the LLM-formatted string against the verified metadata.

    The formatter is the one LLM step whose output reaches the user without
    downstream verification — a transposed page number or dropped author here
    would ship silently. Every critical field present in the metadata must
    appear in the output. Returns "" when faithful, else the first violation.
    """
    f = _norm_for_check(formatted)

    def has_number(v: str) -> bool:
        v = _norm_for_check(str(v))
        return bool(re.search(rf"(?<!\d){re.escape(v)}(?!\d)", f))

    year = str(fields.get("year") or "").strip()
    if year and not has_number(year):
        return f"year {year!r} missing from formatted output"
    if fields.get("volume") and not has_number(str(fields["volume"])):
        return f"volume {fields['volume']!r} missing"
    if fields.get("issue") and not has_number(str(fields["issue"])):
        return f"issue {fields['issue']!r} missing"

    pages = _norm_for_check(str(fields.get("pages") or ""))
    nums = re.findall(r"\d+", pages)
    if nums and not all(has_number(n) for n in nums):
        return f"pages {fields['pages']!r} missing or altered"

    doi = _norm_for_check(str(fields.get("doi") or "")).replace("doi:", "").strip()
    if doi and doi not in f:
        return f"doi {fields['doi']!r} missing or altered"

    authors = authors_to_list(fields.get("authors") or [])
    if authors:
        first = authors[0].split()
        surname = _norm_for_check(first[-1]) if first else ""
        if len(surname) > 2 and surname not in f:
            return f"first author surname {surname!r} missing"
        if len(authors) >= 7 and "et al" not in f:
            return "7+ authors but no 'et al.' in output"

    return ""


def _retrieve_snippets(state: Dict[str, Any]) -> tuple[list, str]:
    """Best-effort style-guide retrieval; an unavailable RAG index must not block formatting."""
    query = build_query_from_state(state)
    try:
        rag_service = get_style_guide_service()
        return rag_service.retrieve_snippets(query), query
    except Exception as e:
        logger.warning("[llm_format] Style-guide retrieval unavailable: %s", e)
        return [], query


# ---------------------------------------------------------------------
# Main formatter
# ---------------------------------------------------------------------
async def llm_format(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    IEEE formatting with RAG + LLM.

    Uses the shared LLMAdapter from the pipeline state, so every provider it
    supports (OpenAI, Azure, Anthropic, Ollama) works here identically.

    Never raises: on any failure `formatted` stays empty and the graph
    falls back to the rule-based formatter.
    """
    snippets, query = _retrieve_snippets(state)

    context_block = "IEEE STYLE GUIDE EXCERPTS:\n" + "\n\n".join(
        f"[{s['rank']}] {s['text']}" for s in snippets
    )

    rtype = (state.get("type") or "unknown").lower()
    extracted = state.get("extracted") or {}
    corrected = state.get("corrected_entities") or {}
    merged = {**extracted, **corrected}

    # Fill display gaps from the verified best record (see format_reference)
    best = state.get("best") or {}
    if best and best.get("source") != "extracted":
        for k in ("journal_name", "journal_abbrev", "conference_name", "book_title",
                  "volume", "issue", "pages", "year", "month", "doi",
                  "publisher", "location", "edition", "isbn", "url"):
            if not merged.get(k) and best.get(k):
                merged[k] = best[k]

    fields_text = "\n".join(f"{k}: {v}" for k, v in merged.items() if v)

    logger.debug("[llm_format] type=%s fields=%s", rtype, merged)

    user_prompt = f"""{context_block}

REFERENCE_TYPE: {rtype.upper()}

METADATA FIELDS:
{fields_text or '(none)'}

Format this reference exactly per IEEE rules.
Output only the final IEEE formatted line.
"""

    formatted = ""
    llm = state.get("_llm")
    if llm is not None and getattr(llm, "provider", "dummy") != "dummy":
        try:
            raw = await llm.text(f"{SYSTEM_PROMPT}\n\n{user_prompt}")
            formatted = sanitize_llm_output(raw)
        except Exception as e:
            logger.warning("[llm_format] LLM formatting failed (%s); falling back to rule-based formatter", e)
            formatted = ""
    else:
        logger.debug("[llm_format] No LLM configured; rule-based formatter will run")

    # Faithfulness gate: LLM output that drops or alters a verified field is
    # discarded so the deterministic rule-based formatter takes over.
    if formatted:
        violation = check_faithfulness(formatted, merged)
        if violation:
            logger.warning("[llm_format] Unfaithful output rejected (%s); using rule-based formatter",
                           violation)
            formatted = ""

    logger.debug("[llm_format] result: %s", formatted)

    state["style_snippets"] = [s["text"] for s in snippets]
    state["formatted"] = formatted
    state["style_query"] = query
    if formatted:
        state["_formatter"] = ("LLM formatter (style-guide grounded)" if snippets
                               else "LLM formatter")

    return state
