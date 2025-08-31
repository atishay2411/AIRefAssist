from concurrent.futures import ThreadPoolExecutor, as_completed
from ..config import PipelineConfig
from ..state import PipelineState
from ..tools.utils import (
    heuristic_abbrev, token_similarity, authors_to_list, normalize_text, normalize_month_field, fingerprint_state
)
from collections import Counter
import logging

logger = logging.getLogger("refassist")

async def fetch_date_from_doi(http, llm, doi: str, limiter) -> tuple[str | None, str | None]:
    if not doi:
        return None, None
    url = f"https://doi.org/{normalize_text(doi)}"
    try:
        async with limiter:
            r = await http.get(url, follow_redirects=True)
        if r.status_code != 200:
            logger.warning(f"DOI fetch failed: {r.status_code} for {url}")
            return None, None
        text = r.text[:2000]  # Limit to avoid LLM token overflow
        prompt = (
            "Extract the publication year and month/date from this HTML snippet. Look for 'Published', 'Available online', or metadata dates. "
            "Prioritize the earliest publication date. Return JSON {'year': 'YYYY', 'month': 'MM' or 'Month name'} or {} if not found or invalid "
            "(e.g., year >2029 or <1900). Snippet:\n" + text
        )
        j = await llm.json(prompt)
        year = j.get("year")
        month = j.get("month")
        valid_year = None
        valid_month = None
        if year:
            try:
                y = int(year)
                if 1900 <= y <= 2029:
                    valid_year = year
            except ValueError:
                pass
        if month:
            valid_month = normalize_month_field(month)
        if valid_year:
            logger.info(f"DOI fetch extracted date: year={valid_year}, month={valid_month} from {url}")
            return valid_year, valid_month
        logger.warning(f"No valid date extracted from DOI page: {url}")
        return None, None
    except Exception as e:
        logger.error(f"Error fetching DOI page: {e}")
        return None, None

def _prefer_abbrev(be_ab: str, fallback: str) -> str:
    if be_ab: return be_ab
    return fallback

def agent_journal(extracted, best):
    ex_j = normalize_text(extracted.get("journal_name") or "")
    ex_ab = normalize_text(extracted.get("journal_abbrev") or "")
    be_j = normalize_text(best.get("journal_name") or "")
    be_ab = normalize_text(best.get("journal_abbrev") or "")
    sim_full = token_similarity(ex_j, be_j) if ex_j and be_j else 0.0
    sim_ab   = token_similarity(ex_ab, be_ab) if ex_ab and be_ab else 0.0
    ok = (sim_full >= 0.6) or (sim_ab >= 0.6) or (bool(ex_j) and not be_j)
    corr = {}
    if be_j and be_j != ex_j: corr["journal_name"] = be_j
    if (be_ab and be_ab != ex_ab) or (not ex_ab and (be_ab or be_j)):
        chosen = _prefer_abbrev(be_ab, heuristic_abbrev(be_j or ex_j))
        corr["journal_abbrev"] = chosen
    return {"ok": ok, "correction": corr or None}

def agent_authors(extracted, best):
    ex = authors_to_list(extracted.get("authors"))
    be = authors_to_list(best.get("authors"))
    if be:
        matches = 0
        for ea in ex:
            last = ea.split()[-1].lower() if ea.split() else ""
            if any((ba.split()[-1].lower() if ba.split() else "") == last for ba in be):
                matches += 1
        required = max(1, int(0.5 * len(ex))) if ex else 1
        if matches >= required:
            return {"ok": True}
        return {"ok": False, "correction": {"authors": be}}
    return {"ok": bool(ex)}

def agent_title(extracted, best):
    ex_t = normalize_text(extracted.get("title") or "")
    be_t = normalize_text(best.get("title") or "")
    desired = ex_t if ex_t else ""
    if be_t:
        sim = token_similarity(ex_t, be_t)
        if sim >= 0.7:
            if ex_t != desired:
                return {"ok": True, "correction": {"title": desired}}
            return {"ok": True}
        return {"ok": False, "correction": {"title": be_t}}
    else:
        if ex_t and ex_t != desired:
            return {"ok": False, "correction": {"title": desired}}
        return {"ok": bool(ex_t)}

async def agent_year_month(extracted, best, candidates, http, llm, limiter, doi):
    ex_y = str(extracted.get("year") or "").strip()
    ex_m = normalize_month_field(extracted.get("month") or "")
    be_y = str(best.get("year") or "").strip()
    be_m = normalize_month_field(best.get("month") or "")
    
    # Always cross-check DOI if available (priority source)
    doi_year = None
    doi_month = None
    if doi:
        logger.info(f"Cross-checking DOI for date: {doi}")
        doi_year, doi_month = await fetch_date_from_doi(http, llm, doi, limiter)
    
    # Prioritize DOI if successful
    final_year = doi_year if doi_year else None
    final_month = doi_month if doi_month else be_m  # Month fallback to best, as it's less critical
    
    # If DOI fails/not present, fallback to candidate consensus
    if not final_year:
        candidate_years = [str(c.get("year") or "").strip() for c in candidates if c.get("year")]
        year_counts = Counter(candidate_years)
        most_common_year = year_counts.most_common(1)[0][0] if year_counts else be_y
        try:
            y = int(most_common_year)
            if 1900 <= y <= 2029:
                final_year = most_common_year
            else:
                final_year = ex_y  # Ultimate fallback
        except ValueError:
            final_year = ex_y
    
    ok = True; corr = {}
    if final_year and final_year != ex_y:
        corr["year"] = final_year
        ok = False
    if final_month and final_month != ex_m:
        corr["month"] = final_month
        ok = False
    return {"ok": ok, "correction": corr or None}

def agent_vipd(extracted, best):
    exv, exi, exp, exd = [normalize_text(extracted.get(k) or "") for k in ("volume","issue","pages","doi")]
    bev, bei, bep, bed = [normalize_text(best.get(k) or "") for k in ("volume","issue","pages","doi")]
    ok = True; corr = {}
    if bev and bev != exv: corr["volume"] = bev; ok = False
    if bei and bei != exi: corr["issue"]  = bei; ok = False
    if bep and bep != exp: corr["pages"]  = bep; ok = False
    if bed and bed.lower().replace("doi:","") != exd.lower().replace("doi:",""):
        corr["doi"] = bed; ok = False
    return {"ok": ok, "correction": corr or None}

def agent_presence(extracted, best):
    return {"ok": bool(extracted.get("title")) and bool(extracted.get("authors"))}

async def verify_agents(state: PipelineState) -> PipelineState:
    ex = state["extracted"]; be = state.get("best") or {}
    candidates = state.get("candidates") or []
    http = state["_http"]
    llm = state["_llm"]
    limiter = state["_limiter"]
    doi = normalize_text(ex.get("doi") or "").replace("doi:", "").strip()  # Cleaned DOI

    agents = [
        (agent_journal, (ex, be)),
        (agent_authors, (ex, be)),
        (agent_title, (ex, be)),
        (agent_vipd, (ex, be)),
        (agent_presence, (ex, be)),
    ]
    results = {}
    with ThreadPoolExecutor(max_workers=state["_cfg"].agent_threads) as pool:
        fut_map = {pool.submit(a, *args): a.__name__ for a, args in agents}
        for fut in as_completed(fut_map):
            name = fut_map[fut]
            try: results[name] = fut.result()
            except Exception: results[name] = {"ok": False}

    # Async call for year_month with DOI priority
    ym_result = await agent_year_month(ex, be, candidates, http, llm, limiter, doi)
    results["agent_year_month"] = ym_result

    suggestions = {}
    for out in results.values():
        if out.get("correction"): suggestions.update(out["correction"])

    vipd_ok = results.get("agent_vipd", {}).get("ok", False)
    ym_ok = results.get("agent_year_month", {}).get("ok", False)

    verification = {
        "title":          results.get("agent_title", {}).get("ok", False),
        "authors":        results.get("agent_authors", {}).get("ok", False),
        "journal_name":   results.get("agent_journal", {}).get("ok", False),
        "journal_abbrev": results.get("agent_journal", {}).get("ok", False),
        "year":           ym_ok,
        "month":          ym_ok,
        "volume":         vipd_ok,
        "issue":          vipd_ok,
        "pages":          vipd_ok,
        "doi":            vipd_ok,
        "presence":       results.get("agent_presence", {}).get("ok", False),
    }

    ver_score = sum(1 for v in verification.values() if v)
    last_score = state.get("_ver_score", -1)
    stagnation = state.get("_stagnation", 0)
    stagnation = 0 if ver_score > last_score else (stagnation + 1)

    state["_ver_score"] = ver_score
    state["_stagnation"] = stagnation
    state["verification"] = verification
    state["suggestions"] = suggestions
    state["hops"] = (state.get("hops") or 0) + 1

    fp = fingerprint_state(ex, be, suggestions)
    hist = state.get("_fp_history") or set()
    state["_loop_detected"] = fp in hist
    hist.add(fp)
    state["_fp_history"] = hist
    state["_fp"] = fp
    return state