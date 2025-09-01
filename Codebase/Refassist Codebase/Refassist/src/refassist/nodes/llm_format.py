import re
from ..state import PipelineState
from ..tools.utils import normalize_text

IEEE_HINT = (
    "You are a precise IEEE reference formatter. "
    "Format a single reference in IEEE style using ONLY the provided fields. "
    "Do NOT invent or modify facts. If a field is missing, omit it. "
    "Output must be a single formatted reference line (no JSON, no commentary). "
    "Use italics with *asterisks* around container titles if present. "
    "Follow IEEE patterns for each type:\n"
    "- journal article: Authors, \"Title,\" *Journal*, vol. X, no. Y, pp. A–B or Art. no. N, Mon YYYY, https://doi.org/DOI.\n"
    "- conference paper: Authors, \"Title,\" in *Conference Name*, Location, pp. A–B, Mon YYYY, https://doi.org/DOI.\n"
    "- book: Authors, *Title*, Edition ed., Location: Publisher, YYYY, ISBN if provided, DOI/URL if provided.\n"
    "- book chapter: Authors, \"Title,\" in *Book Title*, pp. A–B, Publisher, Mon YYYY, DOI/URL.\n"
    "- preprint: Authors, \"Title,\" preprint (arXiv if indicated), Mon YYYY, DOI/URL.\n"
    "Ensure authors are in IEEE initials style if already provided that way; otherwise use as given. "
    "Never change the year, DOI, title, or authors — use them exactly as provided."
)

def _is_reasonable(ref: str) -> bool:
    if not ref or len(ref) < 20:
        return False
    # must contain at least a title quote or an italic block for containers, or a DOI/http
    if ("\"" in ref) or ("*") in ref or ("doi.org/" in ref) or ("http" in ref):
        return True
    return False

def _safe_line(s: str) -> str:
    s = s.strip()
    # collapse whitespace
    s = re.sub(r"\s+", " ", s)
    # ensure trailing period
    if s and not s.endswith("."):
        s += "."
    return s

async def llm_format(state: PipelineState) -> PipelineState:
    llm = state.get("_llm")
    ex = state.get("extracted", {}) or {}
    rtype = normalize_text(state.get("type") or "other")

    if not llm:
        return state

    # Build a locked, authoritative view for the LLM to format from.
    # We DO NOT let the LLM change these values — it only formats.
    payload_lines = [f"type: {rtype}"]
    for k in (
        "title","authors","journal_name","journal_abbrev","conference_name","volume","issue",
        "pages","year","month","doi","publisher","location","edition","isbn","url"
    ):
        v = ex.get(k)
        if v is None or v == "":
            continue
        if isinstance(v, list):
            payload_lines.append(f"{k}: {', '.join([str(x) for x in v])}")
        else:
            payload_lines.append(f"{k}: {v}")

    user_payload = "\n".join(payload_lines)

    prompt = (
        f"{IEEE_HINT}\n\n"
        f"Fields to use (authoritative; do not change values):\n{user_payload}\n\n"
        "Return exactly one IEEE-formatted reference line, nothing else."
    )

    out_text = (await llm.text(prompt)).strip() if hasattr(llm, "text") else ""
    if _is_reasonable(out_text):
        state["formatted"] = _safe_line(out_text)
    # If the LLM output is not reasonable, leave 'formatted' unset to trigger fallback.
    return state
