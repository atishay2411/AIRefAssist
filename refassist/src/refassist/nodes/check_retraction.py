"""Retraction check for the resolved reference.

Signals, in order of cost:
  1. The Retraction Watch database (in-memory once loaded) — the
     authoritative registry, and the only signal that carries the retraction
     nature, date, and reasons.
  2. Candidate-level flags gathered during lookup (OpenAlex `is_retracted`,
     Crossref `updated-by`, "RETRACTED:" title prefixes) for records that
     match the selected best record.
  3. When the reference has a DOI, an authoritative (cached) per-DOI check
     against Crossref, OpenAlex, and PubMed — search-route responses often
     omit the retraction fields that the per-DOI routes carry.
"""
import asyncio
from typing import Any, Dict, Optional

from ..logging import logger
from ..state import PipelineState
from ..tools.retractionwatch import get_rw_db
from ..tools.utils import normalize_text


def _crossref_record_retracted(rec: Dict[str, Any]) -> bool:
    for u in (rec.get("updated-by") or []):
        t = f"{u.get('type', '')} {u.get('label', '')}".lower()
        if "retract" in t or "withdraw" in t:
            return True
    t = rec.get("title")
    title = (t[0] if isinstance(t, list) and t else t) or ""
    return str(title).strip().lower().startswith(("retracted", "withdrawn"))


def _norm_doi(v: Any) -> str:
    return normalize_text(v or "").lower().replace("doi:", "").strip()


def _source(state: PipelineState, name: str) -> Optional[Any]:
    return next((s for s in state.get("_sources") or [] if getattr(s, "NAME", "") == name), None)


async def check_retraction(state: PipelineState) -> PipelineState:
    if state.get("_skip_pipeline"):
        return state

    best = state.get("best") or {}
    ex = state.get("extracted") or {}
    retracted = bool(best.get("retracted"))

    # 1. Retraction Watch (instant once the dataset is loaded). A hit also
    #    supplies nature/date/reasons for the report; an "Expression of
    #    concern" or "Correction" is recorded without marking retraction.
    rw_doi = _norm_doi(best.get("doi") or ex.get("doi"))
    if rw_doi and (rw := get_rw_db().lookup(rw_doi)):
        state["retraction_info"] = {**rw, "source": "Retraction Watch"}
        if "retraction" in (rw.get("nature") or "").lower():
            retracted = True

    if not retracted and best:
        b_doi = _norm_doi(best.get("doi"))
        b_title = normalize_text(best.get("title", "")).lower()
        for c in state.get("candidates") or []:
            if not c.get("retracted"):
                continue
            c_doi = _norm_doi(c.get("doi"))
            c_title = normalize_text(c.get("title", "")).lower()
            if (b_doi and c_doi == b_doi) or (b_title and c_title == b_title):
                retracted = True
                break

    # 3. Authoritative per-DOI check (responses are cached by the clients).
    #    PubMed marks retractions in its publication-type list — the strongest
    #    signal for biomedical works whose Crossref records lag.
    doi = _norm_doi(best.get("doi") or ex.get("doi"))
    if not retracted and doi:
        lookups = []
        for name in ("crossref", "openalex", "pubmed"):
            if (src := _source(state, name)) is not None:
                lookups.append((name, src.by_doi(doi)))
        results = await asyncio.gather(*(c for _, c in lookups), return_exceptions=True)
        for (name, _), rec in zip(lookups, results):
            if isinstance(rec, Exception) or not isinstance(rec, dict) or not rec:
                continue
            if name == "openalex" and rec.get("is_retracted"):
                retracted = True
                break
            if name == "crossref" and _crossref_record_retracted(rec):
                retracted = True
                break
            if name == "pubmed" and any(
                    "retracted publication" in str(p).lower()
                    for p in rec.get("pubtype") or []):
                retracted = True
                break

    state["retracted"] = retracted
    if retracted:
        logger.warning("[check_retraction] Reference flagged as RETRACTED (doi=%s)", doi or "n/a")
        # The retraction must travel WITH the citation — a user copying the
        # formatted string must not lose it (IEEE practice is to note the
        # retraction in the reference itself).
        fmt = state.get("formatted") or ""
        if fmt and "retract" not in fmt.lower():
            state["formatted"] = fmt.rstrip() + " (Retracted.)"
    return state
