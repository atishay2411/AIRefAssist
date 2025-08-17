from typing import Any, Dict, List
from .utils import normalize_text, token_similarity, authors_to_list

def score_candidate(extracted: Dict[str, Any], cand: Dict[str, Any]) -> float:
    score = 0.0
    ex_doi = normalize_text(extracted.get("doi") or "").lower().replace("doi:","")
    ca_doi = normalize_text(cand.get("doi") or "").lower().replace("doi:","")
    if ex_doi and ca_doi and ex_doi == ca_doi: score += 1.0
    score += 0.6 * token_similarity(extracted.get("title") or "", cand.get("title") or "")
    ex_auth = [a.split()[-1].lower() for a in authors_to_list(extracted.get("authors")) if a.split()]
    ca_auth = [a.split()[-1].lower() for a in authors_to_list(cand.get("authors")) if a.split()]
    if ex_auth and ca_auth:
        inter = len(set(ex_auth) & set(ca_auth))
        score += 0.2 * (inter / max(1, len(set(ex_auth) | set(ca_auth))))
    ey = str(extracted.get("year") or "").strip()
    cy = str(cand.get("year") or "").strip()
    if ey and cy and ey == cy: score += 0.1
    src_weight = {"crossref": 0.12, "openalex": 0.08, "semanticscholar": 0.06, "pubmed": 0.05, "arxiv": 0.03}
    score += src_weight.get(cand.get("source",""), 0.0)
    return score

def is_trustworthy_match(ex, cand) -> bool:
    ex_doi = normalize_text(ex.get("doi")).lower().replace("doi:","")
    ca_doi = normalize_text(cand.get("doi")).lower().replace("doi:","")
    if ex_doi and ca_doi and ex_doi == ca_doi: return True
    t_sim = token_similarity(ex.get("title",""), cand.get("title",""))
    ex_last = {a.split()[-1].lower() for a in authors_to_list(ex.get("authors")) if a.split()}
    ca_last = {a.split()[-1].lower() for a in authors_to_list(cand.get("authors")) if a.split()}
    return (t_sim >= 0.8) and bool(ex_last & ca_last)
