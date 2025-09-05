import re
from ..state import PipelineState
from ..tools.utils import normalize_text

IEEE_HINT = (
    "You are a precise IEEE reference formatter. "
    "Format a single reference in IEEE style using ONLY the provided fields. "
    "Do NOT invent or modify facts. If a field is missing, omit it. "
    "Output must be a single formatted reference line (no JSON, no commentary). "
    "Use italics with *asterisks* around container titles if present. "
    "Authors rule (IEEE reference list): list all authors if there are up to six. "
    "If there are seven or more authors, list only the first author followed by *et al.* "
    "Always include full page ranges if available (e.g., pp. 5338–5346); if only one page is present, use 'p. N'. "
    "Do NOT guess missing pages."
)

def _is_reasonable(ref: str) -> bool:
    if not ref or len(ref) < 20:
        return False
    return ("\"" in ref) or ("*") in ref or ("doi.org/" in ref) or ("http" in ref)

def _safe_line(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    if s and not s.endswith("."):
        s += "."
    return s

def _normalize_pages_field(pages: str) -> str:
    pages = (pages or "").strip()
    if not pages:
        return ""
    nums = re.findall(r"\d+", pages)
    if len(nums) == 2:
        return f"{nums[0]}–{nums[1]}"
    if len(nums) == 1:
        return nums[0]
    return pages

def _post_sanitize(s: str) -> str:
    s = re.sub(r"\s+,", ",", s)
    s = re.sub(r"\s+\.", ".", s)
    s = re.sub(r"\bpp\.\s*(?=[,\.](?:\s|$))", "", s)
    s = re.sub(r"\bpp\.\s*(\d+)[\s,]+(\d+)", r"pp. \1–\2", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

async def llm_format(state: PipelineState) -> PipelineState:
    llm = state.get("_llm")
    ex = state.get("extracted", {}) or {}
    rtype = normalize_text(state.get("type") or "other")

    if not llm:
        return state

    if "pages" in ex and ex["pages"]:
        ex["pages"] = _normalize_pages_field(str(ex["pages"]))

    payload_lines = [f"type: {rtype}"]
    for k in (
        "title","authors","journal_name","journal_abbrev","conference_name",
        "volume","issue","pages","year","month","doi","publisher","location","edition","isbn","url"
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
        cleaned = _post_sanitize(_safe_line(out_text))
        state["formatted"] = _safe_line(cleaned)
    return state
