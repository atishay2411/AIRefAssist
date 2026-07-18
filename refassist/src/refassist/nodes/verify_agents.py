from ..config import PipelineConfig
from ..state import PipelineState
from ..tools.utils import (
    heuristic_abbrev, token_similarity, authors_to_list, normalize_text,
    normalize_month_field, fingerprint_state, is_plausible_year
)

def normalize_author_name(author: str) -> str:
    parts = author.strip().split()
    if not parts:
        return ""
    if parts[-1].lower() in ['al.', 'et', 'et.']:
        return ""
    initials = [p for p in parts[:-1] if p[0].isalpha() and (len(p) == 1 or p.endswith('.'))]
    surname = parts[-1] if parts[-1][0].isalpha() else ""
    return " ".join(initials + [surname]).lower().strip()

def _prefer_abbrev(be_ab: str, fallback: str) -> str:
    if be_ab: return be_ab
    return fallback

def agent_journal(extracted, best):
    ex_j = normalize_text(extracted.get("journal_name", ""))
    ex_ab = normalize_text(extracted.get("journal_abbrev", ""))
    be_j = normalize_text(best.get("journal_name", ""))
    be_ab = normalize_text(best.get("journal_abbrev", ""))
    corr = {}
    ok = False

    if be_j or be_ab:
        sim_full = token_similarity(ex_j, be_j) if ex_j and be_j else 0.0
        sim_ab = token_similarity(ex_ab, be_ab) if ex_ab and be_ab else 0.0
        ok = (sim_full >= 0.90) or (sim_ab >= 0.90)
        if be_j and be_j != ex_j: corr["journal_name"] = be_j
        chosen = _prefer_abbrev(be_ab, heuristic_abbrev(be_j or ex_j))
        if (be_ab and be_ab != ex_ab) or (not ex_ab and chosen):
            corr["journal_abbrev"] = chosen
    else:
        ok = False

    return {"ok": ok, "correction": corr or None}

def agent_authors(extracted, best):
    ex = authors_to_list(extracted.get("authors", []))
    be = authors_to_list(best.get("authors", []))
    if not be:
        return {"ok": False, "correction": None}

    def norm_list(L):
        return [normalize_author_name(a) for a in L if normalize_author_name(a)]

    exn, ben = norm_list(ex), norm_list(be)
    if not exn or not ben:
        return {"ok": False, "correction": {"authors": be} if be else None}

    if exn == ben:
        return {"ok": True, "correction": None}

    if len(exn) == len(ben) and set(exn) == set(ben):
        return {"ok": True, "correction": {"authors": be}}

    # Never propose DELETING cited co-authors on similarity evidence alone —
    # a matched record with strictly fewer authors is usually a different
    # work (an excerpt, a chapter, a solo essay), not a correction. Only an
    # exact DOI/ISBN match between citation and record proves the deletion.
    def _surname_keys(names):
        return {n.split()[-1] for n in names if n.split()}
    if _surname_keys(ben) < _surname_keys(exn):
        ex_doi = normalize_text(extracted.get("doi", "")).lower().replace("doi:", "")
        be_doi = normalize_text(best.get("doi", "")).lower().replace("doi:", "")
        ids_prove_it = bool(ex_doi and be_doi and ex_doi == be_doi)
        if not ids_prove_it:
            return {"ok": False, "correction": None}

    return {"ok": False, "correction": {"authors": be}}

def agent_title(extracted, best):
    ex_t = normalize_text(extracted.get("title", ""))
    be_t = normalize_text(best.get("title", ""))
    if be_t:
        sim = token_similarity(ex_t, be_t)
        return {"ok": sim >= 0.90, "correction": None if sim >= 0.90 else {"title": be_t}}
    return {"ok": False, "correction": None}

def agent_year_month(extracted, best):
    ex_y = str(extracted.get("year", "")).strip()
    ex_m = normalize_month_field(extracted.get("month", ""))
    be_y = str(best.get("year", "")).strip()
    be_m = normalize_month_field(best.get("month", ""))

    ok = False
    corr = {}

    if be_y or be_m:
        if be_y and be_y != ex_y: corr["year"] = be_y
        if be_m and be_m != ex_m: corr["month"] = be_m
        ok = not bool(corr)
    else:
        ok = is_plausible_year(ex_y) and bool(ex_y)

    return {"ok": ok, "correction": corr or None}

def agent_vipd(extracted, best):
    ex_v, ex_i, ex_p, ex_d = [normalize_text(extracted.get(k, "")) for k in ("volume", "issue", "pages", "doi")]
    be_v, be_i, be_p, be_d = [normalize_text(best.get(k, "")) for k in ("volume", "issue", "pages", "doi")]
    corr = {}
    ok = False

    if be_v or be_i or be_p or be_d:
        if be_v and be_v != ex_v: corr["volume"] = be_v
        if be_i and be_i != ex_i: corr["issue"] = be_i
        if be_p and be_p != ex_p: corr["pages"] = be_p
        if be_d and be_d.lower().replace("doi:", "") != ex_d.lower().replace("doi:", ""):
            corr["doi"] = be_d
        ok = not bool(corr)
    else:
        ok = False

    return {"ok": ok, "correction": corr or None}

def agent_presence(extracted, best):
    return {"ok": bool(extracted.get("title")) and bool(extracted.get("authors"))}

def verify_agents(state: PipelineState) -> PipelineState:
    ex = state["extracted"]
    be = state.get("best", {})
    matching_fields = state.get("matching_fields", [])
    agents = [agent_journal, agent_authors, agent_title, agent_year_month, agent_vipd, agent_presence]
    results = {}

    for a in agents:
        try:
            results[a.__name__] = a(ex, be)
        except Exception:
            results[a.__name__] = {"ok": False}

    suggestions = {}
    for name, out in results.items():
        if out.get("correction"):
            for k, v in out["correction"].items():
                if k == "authors" or k not in matching_fields:
                    suggestions[k] = v

    vipd_ok = results.get("agent_vipd", {}).get("ok", False)
    ym_ok = results.get("agent_year_month", {}).get("ok", False)

    verification = {
        "title": results.get("agent_title", {}).get("ok", False) or "title" in matching_fields,
        "authors": results.get("agent_authors", {}).get("ok", False),
        "journal_name": results.get("agent_journal", {}).get("ok", False) or "journal_name" in matching_fields,
        "journal_abbrev": results.get("agent_journal", {}).get("ok", False) or "journal_abbrev" in matching_fields,
        "year": ym_ok or "year" in matching_fields,
        "month": ym_ok or "month" in matching_fields,
        "volume": vipd_ok or "volume" in matching_fields,
        "issue": vipd_ok or "issue" in matching_fields,
        "pages": vipd_ok or "pages" in matching_fields,
        "doi": vipd_ok or "doi" in matching_fields,
        "presence": results.get("agent_presence", {}).get("ok", False),
    }

    # Monotonic rounds: a field the agents confirmed once stays confirmed.
    # Later lookup rounds retrieve fresh candidates; without the lock, a new
    # (wrong) best record can overwrite a previously verified value — that is
    # exactly how a correct Shannon Part 1 citation regressed to Part 2.
    locked = set(state.get("_locked_fields") or set())
    for f, ok in verification.items():
        if ok and f in ("title", "authors", "journal_name", "journal_abbrev",
                        "year", "month", "volume", "issue", "pages", "doi"):
            locked.add(f)
    state["_locked_fields"] = locked

    ver_score = sum(1 for v in verification.values() if v)
    last_score = state.get("_ver_score", -1)
    stagnation = state.get("_stagnation", 0)
    stagnation = 0 if ver_score > last_score else (stagnation + 1)

    state["_ver_score"] = ver_score
    state["_stagnation"] = stagnation
    state["verification"] = verification
    state["suggestions"] = suggestions
    state["hops"] = state.get("hops", 0) + 1

    fp = fingerprint_state(ex, be, suggestions)
    hist = state.get("_fp_history", set())
    state["_loop_detected"] = fp in hist
    hist.add(fp)
    state["_fp_history"] = hist
    state["_fp"] = fp
    return state
