"""NLM Catalog journal-abbreviation lookup.

Runs as one of the parallel jobs inside MultiSourceLookup (it used to be a
serial node ahead of the lookup, adding 1–4s to the critical path of every
journal reference).

Query strategy: an exact "[Title Abbreviation]" match first (catches short
names like "Nature", where a bare [Journal] search matches hundreds of
records and retmax=1 picks the wrong one), then a quoted [Journal] phrase
search for full titles.
"""
from typing import Any, Optional

from ..logging import logger

NLM_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NLM_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


async def _esearch_first_id(client: Any, term: str) -> Optional[str]:
    r = await client.get(NLM_ESEARCH, params={
        "db": "nlmcatalog", "term": term, "retmode": "json", "retmax": 1,
    })
    r.raise_for_status()
    ids = (r.json().get("esearchresult") or {}).get("idlist") or []
    return ids[0] if ids else None


async def nlm_lookup_abbrev(client: Any, cache: Any, journal: str) -> str:
    """Return the ISO abbreviation for a journal, or "" when unresolvable.
    Results (including misses) are cached for the process lifetime TTL."""
    journal = (journal or "").strip()
    if not journal or client is None:
        return ""

    cache_key = ("nlm", journal.lower())
    if cache is not None and (hit := cache.get(cache_key)) is not None:
        return hit

    abbrev = ""
    # NCBI eutils allows ~3 req/s per IP; a parallel PubMed lookup in the same
    # gather can push us over, so one polite retry on 429 usually succeeds.
    for attempt in (1, 2):
        try:
            nlm_id = await _esearch_first_id(client, f'"{journal}"[Title Abbreviation]')
            if not nlm_id:
                nlm_id = await _esearch_first_id(client, f'"{journal}"[Journal]')
            if nlm_id:
                r = await client.get(NLM_ESUMMARY, params={
                    "db": "nlmcatalog", "id": nlm_id, "retmode": "json",
                })
                r.raise_for_status()
                rec = (r.json().get("result") or {}).get(nlm_id, {}) or {}
                # Some records (e.g. Nature) carry only the Medline abbreviation
                abbrev = rec.get("isoabbreviation") or rec.get("medlineta") or ""
            break
        except Exception as e:
            if attempt == 1 and "429" in str(e):
                import asyncio
                await asyncio.sleep(0.5)
                continue
            logger.debug("[nlm] abbreviation lookup failed for %r: %s", journal, e)
            return ""  # transient failure: don't cache

    if cache is not None:
        cache[cache_key] = abbrev
    return abbrev


def apply_abbrev_result(state, abbrev: str) -> None:
    """Fold an NLM result into the pipeline state (extracted + audit trail)."""
    ex = state.get("extracted") or {}
    current = ex.get("journal_abbrev", "") or ""
    if abbrev:
        ex["verified_journal_abbrev"] = abbrev
        if current and current.lower() != abbrev.lower():
            state["corrections"] = state.get("corrections", []) + [
                ("journal_abbrev", current, abbrev)
            ]
            state["verification_message"] = (state.get("verification_message", "") +
                f"Journal abbreviation corrected: '{current}' to '{abbrev}'. ")
    else:
        state["corrections"] = state.get("corrections", []) + [
            ("journal_abbrev", current, "Not found")
        ]
