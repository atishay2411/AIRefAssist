from ..state import PipelineState
from ..tools.utils import normalize_month_field, normalize_text
import re

def _is_single_numeric_page(s: str) -> bool:
    s = normalize_text(s)
    return bool(s) and ("-" not in s) and bool(re.fullmatch(r"\d+", s))

def _is_two_number_range(s: str) -> bool:
    s = normalize_text(s).replace("—","-").replace("–","-")
    nums = re.findall(r"\d+", s)
    return ("-" in s) and (len(nums) >= 2)

def _first_num(s: str) -> str:
    m = re.search(r"\d+", normalize_text(s))
    return m.group(0) if m else ""

def enrich_from_best(state: PipelineState) -> PipelineState:
    ex = dict(state["extracted"]); be = state.get("best") or {}
    for k in ("journal_abbrev","journal_name","volume","issue","pages","year","month","doi","conference_name","publisher","location","edition","isbn","url","title","authors"):
        if not ex.get(k) and be.get(k):
            ex[k] = be.get(k)

    # Upgrade single start page to full range if best has it and starts the same
    if ex.get("pages") and be.get("pages"):
        exp, bep = normalize_text(ex["pages"]), normalize_text(be["pages"])
        if _is_single_numeric_page(exp) and _is_two_number_range(bep):
            if _first_num(exp) and _first_num(exp) == _first_num(bep):
                ex["pages"] = bep

    if ex.get("month"):
        ex["month"] = normalize_month_field(ex["month"])
    state["extracted"] = ex
    return state
