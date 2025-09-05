from typing import Any, Dict, List
from .utils import normalize_text, token_similarity, authors_to_list

def score_candidate(extracted: Dict[str, Any], cand: Dict[str, Any]) -> float:
    score = 0.0

    ex_doi = normalize_text(extracted.get("doi") or "").lower().replace("doi:","")
    ca_doi = normalize_text(cand.get("doi") or "").lower().replace("doi:","")
    if ex_doi and ca_doi and ex_doi == ca_doi:
        score += 1.0

    # Titles carry most of the signal
    t_sim = token_similarity(extracted.get("title") or "", cand.get("title") or "")
    score += 0.9 * t_sim

    ex_auth = [a.split()[-1].lower() for a in authors_to_list(extracted.get("authors")) if a.split()]
    ca_auth = [a.split()[-1].lower() for a in authors_to_list(cand.get("authors")) if a.split()]
    if ex_auth and ca_auth:
        inter = len(set(ex_auth) & set(ca_auth))
        score += 0.25 * (inter / max(1, len(set(ex_auth) | set(ca_auth))))
    else:
        score -= 0.05  # slight penalty if we can’t check overlap

    ey = str(extracted.get("year") or "").strip()
    cy = str(cand.get("year") or "").strip()
    if ey and cy:
        if ey == cy:
            score += 0.12
        else:
            try:
                gap = abs(int(ey[:4]) - int(cy[:4]))
                if gap == 1: score -= 0.03
                elif gap == 2: score -= 0.06
                elif gap >= 3: score -= 0.12
            except Exception:
                score -= 0.02

    src_weight = {"crossref": 0.12, "openalex": 0.08, "semanticscholar": 0.06, "pubmed": 0.05, "arxiv": 0.03}
    score += src_weight.get(cand.get("source",""), 0.0)
    return score

def is_trustworthy_match(ex, cand) -> bool:
    """
    Strong guard:
      - DOI match => trust.
      - Else require very high title similarity (>= 0.90)
        AND (author overlap OR |year_gap| <= 2).
    """
    ex_doi = normalize_text(ex.get("doi")).lower().replace("doi:","")
    ca_doi = normalize_text(cand.get("doi")).lower().replace("doi:","")
    if ex_doi and ca_doi and ex_doi == ca_doi:
        return True

    t_sim = token_similarity(ex.get("title",""), cand.get("title",""))
    if t_sim < 0.90:
        return False

    ex_last = {a.split()[-1].lower() for a in authors_to_list(ex.get("authors")) if a.split()}
    ca_last = {a.split()[-1].lower() for a in authors_to_list(cand.get("authors")) if a.split()}
    author_ok = bool(ex_last and ca_last and (ex_last & ca_last))

    ey = normalize_text(ex.get("year") or "")[:4]
    cy = normalize_text(cand.get("year") or "")[:4]
    year_ok = False
    if ey.isdigit() and cy.isdigit():
        try:
            year_ok = abs(int(ey) - int(cy)) <= 2
        except Exception:
            year_ok = False

    return author_ok or year_ok
