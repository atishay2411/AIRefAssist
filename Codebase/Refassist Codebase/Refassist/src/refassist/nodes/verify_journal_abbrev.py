from typing import Any, Dict
from ..state import PipelineState

# Async NLM Catalog verification using the shared httpx.AsyncClient
NLM_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NLM_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

async def verify_journal_abbrev(state: PipelineState) -> PipelineState:
    """
    Verify journal abbreviation using NLM Catalog API (async, non-blocking).
    Updates:
      - state.extracted['verified_journal_abbrev']
      - logs issues in state.corrections and state.verification_message
    """
    journal = (state.get("extracted", {}) or {}).get("journal_name", "") or ""
    current_abbrev = (state.get("extracted", {}) or {}).get("journal_abbrev", "") or ""

    if not journal.strip():
        state["verification_message"] = (state.get("verification_message", "") +
                                         "No journal name provided for abbreviation verification. ")
        state["corrections"] = state.get("corrections", []) + [
            ("journal_abbrev", current_abbrev, "Missing journal name")
        ]
        return state

    client = state.get("_http")
    if client is None:
        state["verification_message"] = (state.get("verification_message", "") +
                                         "HTTP client unavailable; skipped journal abbreviation verification. ")
        return state

    try:
        # Step 1: esearch → NLM ID
        es_params = {
            "db": "nlmcatalog",
            "term": f"{journal}[Journal]",
            "retmode": "json",
            "retmax": 1
        }
        r1 = await client.get(NLM_ESEARCH, params=es_params)
        r1.raise_for_status()
        data = r1.json()
        idlist = (data.get("esearchresult") or {}).get("idlist") or []
        if not idlist:
            state["verification_message"] = (state.get("verification_message", "") +
                                             f"Journal not found in NLM Catalog: {journal}. ")
            state["corrections"] = state.get("corrections", []) + [
                ("journal_abbrev", current_abbrev, "Journal not found")
            ]
            return state

        nlm_id = idlist[0]

        # Step 2: esummary → isoabbreviation
        sum_params = {
            "db": "nlmcatalog",
            "id": nlm_id,
            "retmode": "json"
        }
        r2 = await client.get(NLM_ESUMMARY, params=sum_params)
        r2.raise_for_status()
        sdata = r2.json()
        journal_data = (sdata.get("result") or {}).get(nlm_id, {}) or {}
        standard_abbrev = journal_data.get("isoabbreviation", "") or ""

        if standard_abbrev:
            state["extracted"]["verified_journal_abbrev"] = standard_abbrev
            if current_abbrev and current_abbrev.lower() != standard_abbrev.lower():
                state["corrections"] = state.get("corrections", []) + [
                    ("journal_abbrev", current_abbrev, standard_abbrev)
                ]
                state["verification_message"] = (state.get("verification_message", "") +
                                                 f"Journal abbreviation corrected: '{current_abbrev}' to '{standard_abbrev}'. ")
        else:
            state["verification_message"] = (state.get("verification_message", "") +
                                             f"No standard abbreviation found for journal: {journal}. ")
            state["corrections"] = state.get("corrections", []) + [
                ("journal_abbrev", current_abbrev, "Not found")
            ]

    except Exception as e:
        state["verification_message"] = (state.get("verification_message", "") +
                                         f"Failed to verify journal abbreviation: {str(e)}. ")
        state["corrections"] = state.get("corrections", []) + [
            ("journal_abbrev", current_abbrev, f"Verification error: {str(e)}")
        ]

    return state
