"""Fabricated-reference detection.

Runs after the lookup/verify loop settles, primarily on references that
NOTHING could verify. A real work — even an obscure one — nearly always
leaves at least one trace across the 14 consulted databases; a fabricated
one leaves a characteristic pattern of absences instead:

  identity_unmatched  no source record's title resembles the citation's
  venue_unknown       the cited journal/conference does not exist (checked
                      against candidate containers, the NLM catalog result,
                      and a live Crossref venue query)
  dead_doi            the citation carries a DOI no registry resolved
  bad_isbn            the ISBN fails its own checksum
  authors_disjoint    a real work matches the title but the cited authors
                      are entirely different (classic LLM hallucination)

Two or more strong signals ⇒ risk "high": the report leads with a
LIKELY FABRICATED verdict, and the formatter's polished output is replaced
by the user's original text — polishing a fake (or minting an IEEE
abbreviation for an imaginary journal) makes it MORE credible, which is
the exact opposite of this product's job.
"""
from typing import Any, Dict, List, Optional

from ..logging import logger
from ..state import PipelineState
from ..tools.utils import (
    normalize_text, token_similarity, isbn_valid, bare_doi,
)

CROSSREF_JOURNALS = "https://api.crossref.org/journals"
CROSSREF_WORKS = "https://api.crossref.org/works"

_STRONG = {"identity_unmatched", "venue_unknown", "dead_doi", "doi_collision",
           "authors_disjoint"}


async def _crossref_venue_exists(client: Any, venue: str) -> Optional[bool]:
    """True/False when Crossref answers, None on network failure (never
    claim nonexistence on a failed request)."""
    try:
        r = await client.get(CROSSREF_JOURNALS, params={"query": venue, "rows": 5})
        r.raise_for_status()
        items = ((r.json().get("message") or {}).get("items")) or []
        for it in items:
            if token_similarity(venue, normalize_text(it.get("title") or "")) >= 0.80:
                return True
    except Exception as e:
        logger.debug("[check_fabrication] journal query failed: %s", e)
        return None
    # Journals registry had nothing — conferences/proceedings live in works'
    # container titles instead, so check those before concluding.
    try:
        r = await client.get(CROSSREF_WORKS, params={
            "query.container-title": venue, "rows": 5, "select": "container-title"})
        r.raise_for_status()
        items = ((r.json().get("message") or {}).get("items")) or []
        for it in items:
            for ct in it.get("container-title") or []:
                if token_similarity(venue, normalize_text(ct)) >= 0.75:
                    return True
        return False
    except Exception as e:
        logger.debug("[check_fabrication] container query failed: %s", e)
        return None


def _venue_in_candidates(venue: str, candidates: List[Dict]) -> bool:
    for c in candidates or []:
        for k in ("journal_name", "conference_name", "book_title", "container_title"):
            if token_similarity(venue, normalize_text(c.get(k) or "")) >= 0.80:
                return True
    return False


async def check_fabrication(state: PipelineState) -> PipelineState:
    if state.get("_skip_pipeline"):
        return state

    ex = state.get("extracted", {}) or {}
    ex0 = state.get("_original_extracted") or ex
    best = state.get("best", {}) or {}
    cands = state.get("candidates", []) or []
    verified = bool(best) and best.get("source") != "extracted"

    signals: List[Dict[str, str]] = []
    checks: Dict[str, Any] = {}

    # A reference verified against real records is not fabricated; the only
    # signal that still matters there is a disjoint author set.
    mismatch = state.get("author_mismatch") or {}
    if mismatch:
        signals.append({
            "code": "authors_disjoint",
            "detail": ("a published work matches this title but its authors are "
                       "entirely different — AI-generated citations routinely "
                       "attach invented authors to real titles"),
        })

    if not verified:
        # 1. Identity: did ANY source return anything resembling this title?
        title = normalize_text(ex0.get("title") or ex.get("title") or "")
        if title:
            best_sim = max((token_similarity(title, normalize_text(c.get("title") or ""))
                            for c in cands), default=0.0)
            checks["closest_title_similarity"] = round(best_sim, 2)
            # Real works match ≥0.85 even through typos; fabricated titles on
            # popular topics still find ~0.6-similar neighbours, so the line
            # sits at 0.75.
            if best_sim < 0.75:
                signals.append({
                    "code": "identity_unmatched",
                    "detail": (f"no record with this title exists in any "
                               f"consulted database (closest match: "
                               f"{int(best_sim * 100)}% similar)"),
                })

        # 2. Venue existence — candidates, NLM verdict, then live Crossref.
        venue = normalize_text(ex0.get("journal_name") or ex.get("journal_name")
                               or ex0.get("conference_name") or ex.get("conference_name") or "")
        if venue and venue.lower() not in ("arxiv", "biorxiv", "medrxiv"):
            exists: Optional[bool] = True if (
                _venue_in_candidates(venue, cands) or ex.get("verified_journal_abbrev")
            ) else None
            if exists is None:
                exists = await _crossref_venue_exists(state.get("_http"), venue)
            checks["venue_exists"] = exists
            if exists is False:
                signals.append({
                    "code": "venue_unknown",
                    "detail": (f"the venue '{venue}' matches no journal or "
                               f"proceedings in the Crossref registry — it may "
                               f"not exist"),
                })

        # 3. DOI: present in the citation but resolved by no registry — or
        #    resolved to a DIFFERENT work (a fabricated citation wearing a
        #    real work's DOI).
        doi = bare_doi(str(ex0.get("doi") or ex.get("doi") or ""))
        if doi:
            same_doi = [c for c in cands
                        if bare_doi(str(c.get("doi") or "")) == doi]
            doi_lookups_timed_out = any(
                "|doi|" in k for k in (state.get("_timed_out_jobs") or set()))
            checks["doi_resolved"] = bool(same_doi)
            if not same_doi and not doi_lookups_timed_out:
                signals.append({
                    "code": "dead_doi",
                    "detail": f"the DOI {doi} was not resolved by any registry",
                })
            elif same_doi and title:
                doi_sim = max(token_similarity(title, normalize_text(c.get("title") or ""))
                              for c in same_doi)
                if doi_sim < 0.5:
                    signals.append({
                        "code": "doi_collision",
                        "detail": (f"the DOI {doi} belongs to a different work "
                                   f"(its title is only {int(doi_sim * 100)}% "
                                   f"similar to the cited one)"),
                    })

        # 4. ISBN checksum.
        isbn = normalize_text(str(ex0.get("isbn") or ex.get("isbn") or ""))
        if isbn and not isbn_valid(isbn):
            signals.append({
                "code": "bad_isbn",
                "detail": f"the ISBN {isbn} fails its own checksum — it cannot "
                          f"be a real ISBN",
            })

    strong = sum(1 for s in signals if s["code"] in _STRONG)
    if strong >= 2:
        risk = "high"
    elif strong == 1:
        risk = "medium"
    elif signals:
        risk = "low"
    else:
        risk = "none"

    state["fabrication"] = {"risk": risk, "signals": signals, "checks": checks}
    if signals:
        logger.debug("[check_fabrication] risk=%s signals=%s", risk,
                     [s["code"] for s in signals])

    # Never hand back a polished IEEE line for a likely-fake reference.
    if risk == "high":
        state["formatted"] = normalize_text(state.get("reference", ""))
        state["_formatter"] = ("Original text preserved — reference is likely "
                               "fabricated; formatting it would lend it false "
                               "credibility")
    return state
