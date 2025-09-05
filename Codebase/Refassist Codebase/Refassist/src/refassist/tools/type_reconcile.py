from typing import Dict, List, Optional
from collections import Counter

TYPE_CANON = {
    "journal-article": "journal article",
    "paper-conference": "conference paper",
    "proceedings-article": "conference paper",
    "book-chapter": "book chapter",
    "book": "book",
    "dataset": "dataset",
    "standard": "standard",
    "report": "technical report",
    "thesis": "thesis",
}

def reconcile_type(candidates: List[Dict[str, str]], llm_vote: Optional[str]) -> str:
    votes: List[str] = []

    if llm_vote:
        votes.append(llm_vote.lower())

    for c in candidates or []:
        source = c.get("source")
        if source == "crossref":
            t = (c.get("cr_type") or "").strip()
            if t:
                votes.append(TYPE_CANON.get(t, t))
        elif source == "openalex":
            if c.get("oa_is_proceedings"):
                votes.append("conference paper")
        elif source == "semanticscholar":
            types = c.get("s2_types") or []
            if any("conference" in t for t in types): votes.append("conference paper")
            if any("journal" in t for t in types): votes.append("journal article")
            if any("book" in t for t in types): votes.append("book")
        elif source == "arxiv":
            votes.append("preprint")

    if votes:
        return Counter(votes).most_common(1)[0][0]
    return "other"
