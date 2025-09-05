from collections import Counter, defaultdict
from typing import Dict, List, Tuple, Optional
import re
from ..state import PipelineState
from ..tools.scoring import score_candidate, is_trustworthy_match
from ..tools.utils import normalize_text, token_similarity, authors_to_list, is_plausible_year, coerce_year

def _norm_author(author: str) -> str:
    parts = author.strip().split()
    if not parts: return ""
    if parts[-1].lower() in {"al.", "et", "et."}: return ""
    initials = [p[0].upper()+"." for p in parts[:-1] if p and p[0].isalpha()]
    surname = parts[-1] if parts[-1] and parts[-1][0].isalpha() else ""
    return (" ".join(initials + [surname])).strip().lower()

def _title_sim(a: str, b: str) -> float:
    return token_similarity(normalize_text(a), normalize_text(b))

def _cluster_by_title(cands: List[Dict]) -> List[List[Dict]]:
    clusters: List[List[Dict]] = []
    THRESH = 0.92  # tighter to avoid merging similar papers
    for c in cands:
        placed = False
        for cl in clusters:
            if _title_sim(c.get("title",""), cl[0].get("title","")) >= THRESH:
                cl.append(c); placed=True; break
        if not placed:
            clusters.append([c])
    return clusters

def _w(src: str) -> float:
    # Prioritize IEEE Xplore and Crossref in consensus voting
    return {"ieeexplore":1.2,"crossref":1.0,"openalex":0.8,"semanticscholar":0.6,"pubmed":0.55,"arxiv":0.4}.get((src or "").lower(), 0.2)

def _pages_richness(s: str) -> int:
    """
    0 = empty/none; 1 = single numeric; 2 = numeric range (start!=end)
    """
    if not s:
        return 0
    s2 = normalize_text(s).replace("—","-").replace("–","-")
    nums = re.findall(r"\d+", s2)
    if "-" in s2 and len(nums) >= 2:
        try:
            if int(nums[0]) != int(nums[1]):
                return 2
        except Exception:
            ...
    if len(nums) == 1:
        return 1
    return 0

def _vote_field(cl: List[Dict], key: str) -> Tuple[str, float, Optional[str]]:
    bucket: Dict[str, float] = defaultdict(float)
    source_for: Dict[str, str] = {}
    for c in cl:
        v = normalize_text(c.get(key, ""))
        if not v: continue
        w = _w(c.get("source"))
        bucket[v] += w
        if v not in source_for or w > _w(source_for.get(v,"")):
            source_for[v] = c.get("source")
    if not bucket: return "", 0.0, None

    if key == "pages":
        best_val, best_w = None, -1.0
        best_rich, best_len = -1, -1
        for v, w in bucket.items():
            rich = _pages_richness(v)
            if (w > best_w) or (w == best_w and rich > best_rich) or (w == best_w and rich == best_rich and len(v) > best_len):
                best_val, best_w = v, w
                best_rich, best_len = rich, len(v)
        return best_val or "", best_w, source_for.get(best_val or "")
    best_val, best_w = max(bucket.items(), key=lambda kv: kv[1])
    return best_val, best_w, source_for.get(best_val)

def _vote_authors(cl: List[Dict]) -> Tuple[List[str], float, Optional[str]]:
    bucket: Dict[Tuple[str,...], float] = defaultdict(float)
    raw_map: Dict[Tuple[str,...], List[str]] = {}
    src_map: Dict[Tuple[str,...], str] = {}
    for c in cl:
        raw = authors_to_list(c.get("authors", []))
        norm = tuple(a for a in [_norm_author(a) for a in raw] if a)
        if not norm: continue
        w = _w(c.get("source"))
        bucket[norm] += w
        if norm not in raw_map or len(raw_map[norm]) < len(raw):
            raw_map[norm] = raw
            src_map[norm] = c.get("source")
    if not bucket: return [], 0.0, None
    norm_best, w = max(bucket.items(), key=lambda kv: kv[1])
    return raw_map.get(norm_best, list(norm_best)), w, src_map.get(norm_best)

def _has_any_doi_agreement(cluster: List[Dict]) -> str:
    dois = [normalize_text(c.get("doi","")).lower().replace("doi:","") for c in cluster if c.get("doi")]
    dois = [d for d in dois if d]
    if not dois: return ""
    c = Counter(dois)
    doi, cnt = c.most_common(1)[0]
    # If any trusted source shares the DOI, accept
    if cnt >= 2 or any((ci.get("source") in {"crossref","ieeexplore","openalex"} and normalize_text(ci.get("doi")).lower().replace("doi:","")==doi) for ci in cluster):
        return doi
    return ""

def _best_year(cl: List[Dict]) -> Tuple[str, str]:
    by_src: Dict[str, List[str]] = defaultdict(list)
    for c in cl:
        y = coerce_year(c.get("year","") or "")
        if not is_plausible_year(y): continue
        by_src[c.get("source","other").lower()].append(y)
    for src in ("ieeexplore","crossref","openalex","semanticscholar","pubmed","arxiv"):
        ys = by_src.get(src, [])
        if ys:
            y = Counter(ys).most_common(1)[0][0]
            return y, src
    all_ys = [y for ys in by_src.values() for y in ys]
    if all_ys:
        y = Counter(all_ys).most_common(1)[0][0]
        return y, "consensus"
    return "", ""

def _consensus_record(ex: dict, candidates: List[Dict]) -> Tuple[Dict, List[str], Dict[str,str]]:
    if not candidates: return {}, [], {}
    clusters = _cluster_by_title(candidates)

    def cl_score(cl: List[Dict]) -> float:
        doi = _has_any_doi_agreement(cl)
        if doi: return 100.0
        return sum(_w(c.get("source")) for c in cl)

    clusters.sort(key=cl_score, reverse=True)
    top = clusters[0]

    best: Dict = {"source":"consensus"}
    provenance: Dict[str, str] = {}

    doi_agree = _has_any_doi_agreement(top)
    if doi_agree:
        best["doi"] = doi_agree; provenance["doi"] = "doi-agreement"
    else:
        v, _, src = _vote_field(top, "doi")
        best["doi"] = v; provenance["doi"] = src or ""

    v, _, src = _vote_field(top, "title")
    best["title"] = v; provenance["title"] = src or ""

    a, _, src = _vote_authors(top)
    best["authors"] = a; provenance["authors"] = src or ""

    y, ysrc = _best_year(top)
    best["year"] = y; provenance["year"] = ysrc or provenance.get("doi","") or ""

    for k in ("journal_name","journal_abbrev","conference_name","volume","issue","pages","month","publisher","location","edition","isbn","url"):
        v, _, src = _vote_field(top, k)
        best[k] = v; provenance[k] = src or ""

    # prefer richer page ranges if top cluster has only single-page
    top_pages = best.get("pages", "")
    if _pages_richness(top_pages) == 1:
        start = re.search(r"\d+", top_pages)
        start = start.group(0) if start else ""
        if start:
            richest = top_pages
            richest_src = provenance.get("pages", "")
            richest_rank = 1
            for c in top:
                candp = normalize_text(c.get("pages",""))
                if _pages_richness(candp) == 2:
                    nums = re.findall(r"\d+", candp)
                    if nums and nums[0] == start:
                        if 2 > richest_rank or (2 == richest_rank and len(candp) > len(richest)):
                            richest = candp
                            richest_src = c.get("source", richest_src)
                            richest_rank = 2
            if richest_rank == 2 and richest != top_pages:
                best["pages"] = richest
                provenance["pages"] = richest_src or provenance.get("pages","")

    matching_fields: List[str] = []
    for k in ("title","authors","year","journal_name","volume","issue","pages","doi"):
        exv = ex.get(k); bev = best.get(k)
        if k == "authors":
            exn = [_norm_author(a) for a in authors_to_list(exv or []) if _norm_author(a)]
            ben = [_norm_author(a) for a in authors_to_list(bev or []) if _norm_author(a)]
            if exn and ben and exn == ben: matching_fields.append(k)
        elif k == "title":
            if exv and bev and _title_sim(exv, bev) >= 0.93: matching_fields.append(k)
        else:
            if normalize_text(str(exv or "")) == normalize_text(str(bev or "")):
                matching_fields.append(k)

    return best, matching_fields, provenance

def select_best(state: PipelineState) -> PipelineState:
    ex = state["extracted"]
    cands = state.get("candidates", [])
    if not cands:
        state["best"] = {}
        state["matching_fields"] = []
        state["provenance"] = {}
        return state

    consensus, matching_fields, prov = _consensus_record(ex, cands)

    # Enforce strict trust before adopting consensus:
    #   - If consensus has DOI and it matches extracted DOI -> trust
    #   - Else, find the *most trustworthy* single candidate and require is_trustworthy_match
    trusted = False
    if consensus.get("doi") and ex.get("doi"):
        trusted = normalize_text(consensus["doi"]).lower().replace("doi:","") == normalize_text(ex["doi"]).lower().replace("doi:","")

    if not trusted:
        best_scored = max(cands, key=lambda c: score_candidate(ex, c))
        if is_trustworthy_match(ex, best_scored):
            consensus2, matching_fields2, prov2 = _consensus_record(ex, [best_scored])
            consensus = consensus2 or consensus
            matching_fields = matching_fields or matching_fields2
            prov = prov or prov2
            trusted = True
        else:
            # One more attempt: pick the candidate with highest title sim to extracted
            def _title_sim_to_ex(c): return _title_sim(ex.get("title",""), c.get("title",""))
            best_by_title = max(cands, key=_title_sim_to_ex)
            if _title_sim_to_ex(best_by_title) >= 0.95 and is_trustworthy_match(ex, best_by_title):
                consensus2, matching_fields2, prov2 = _consensus_record(ex, [best_by_title])
                consensus = consensus2 or consensus
                matching_fields = matching_fields or matching_fields2
                prov = prov or prov2
                trusted = True

    # If still not trusted, **do not** override — keep minimal best so later nodes don't rewrite facts
    if not trusted:
        state["best"] = {}
        state["matching_fields"] = []
        state["provenance"] = {}
        return state

    state["best"] = consensus or {}
    state["matching_fields"] = matching_fields or []
    state["provenance"] = prov or {}
    return state


