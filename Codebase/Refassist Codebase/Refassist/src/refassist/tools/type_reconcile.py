from typing import Dict, List, Optional
from collections import Counter
import re

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
    "incollection": "book chapter",  
}

def reconcile_type(candidates: List[Dict[str, str]], llm_vote: Optional[str], reference: Optional[str] = None) -> str:
    """
    Decide the best reference type (journal article, book, book chapter, etc.)
    using votes from LLM and external metadata sources.
    Added heuristics to correctly detect book chapters.
    """
    votes: List[str] = []

    # --- Include LLM vote if available ---
    if llm_vote:
        votes.append(llm_vote.lower())

    # --- Collect votes from each metadata source ---
    for c in candidates or []:
        source = c.get("source")
        if source == "crossref":
            t = (c.get("cr_type") or "").strip()
            if t:
                votes.append(TYPE_CANON.get(t, t))
        elif source == "openalex":
            if c.get("oa_is_proceedings"):
                votes.append("conference paper")
            if (t := c.get("oa_type")):
                votes.append(TYPE_CANON.get(t, t))
        elif source == "semanticscholar":
            types = c.get("s2_types") or []
            if any("conference" in t.lower() for t in types):
                votes.append("conference paper")
            if any("journal" in t.lower() for t in types):
                votes.append("journal article")
            if any("book" in t.lower() for t in types):
                votes.append("book")
        elif source == "arxiv":
            votes.append("preprint")

    # --- Heuristic: Detect book chapters from raw reference text ---
    if reference:
        ref_lower = reference.lower()
        # "In:" and "(ed.)" or "(eds.)" are strong signals of a book chapter
        if re.search(r'\bIn:\b', ref_lower, re.I) or re.search(r'\((ed|eds)\.\)', ref_lower):
            votes.append("book chapter")

    # --- Voting logic ---
    if votes:
        counts = Counter(votes)

        # Prioritize 'book chapter' over 'book' when both appear
        if "book chapter" in counts and "book" in counts:
            counts["book chapter"] += counts["book"]  # merge confidence
            del counts["book"]

        # Prefer 'conference paper' over generic 'paper'
        if "conference paper" in counts and "paper" in counts:
            counts["conference paper"] += counts["paper"]
            del counts["paper"]

        return counts.most_common(1)[0][0]

    # --- Fallback ---
    return "other"
