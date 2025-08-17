from concurrent.futures import ThreadPoolExecutor, as_completed
from ..config import PipelineConfig
from ..state import PipelineState
from ..tools.utils import (
    heuristic_abbrev, token_similarity, authors_to_list, normalize_text, normalize_month_field, fingerprint_state
)

def _prefer_abbrev(a: str, b: str) -> str:
    cand = [x for x in [a, b] if x]
    if not cand: return ""
    def score(x): s=x.strip(); return (sum(1 for c in s if c.isupper()), -len(s))
    return sorted(cand, key=score, reverse=True)[0]

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
    from ..tools.utils import sentence_case
    ex_t = normalize_text(extracted.get("title") or "")
    be_t = normalize_text(best.get("title") or "")
    desired = sentence_case(ex_t) if ex_t else ""
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

def agent_year_month(extracted, best):
    ex_y = str(extracted.get("year") or "")
    ex_m = normalize_month_field(extracted.get("month") or "")
    be_y = str(best.get("year") or "")
    be_m = normalize_month_field(best.get("month") or "")
    ok = True; corr = {}
    if be_y and be_y != ex_y: corr["year"] = be_y; ok = False
    if be_m and be_m != ex_m: corr["month"] = be_m; ok = False
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

def verify_agents(state: PipelineState) -> PipelineState:
    ex = state["extracted"]; be = state.get("best") or {}
    agents = [agent_journal, agent_authors, agent_title, agent_year_month, agent_vipd, agent_presence]
    results = {}
    with ThreadPoolExecutor(max_workers=state["_cfg"].agent_threads) as pool:
        fut_map = {pool.submit(a, ex, be): a.__name__ for a in agents}
        for fut in as_completed(fut_map):
            name = fut_map[fut]
            try: results[name] = fut.result()
            except Exception: results[name] = {"ok": False}

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
