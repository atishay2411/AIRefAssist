import requests
from typing import Any, Dict
from ..state import PipelineState


def verify_journal_abbrev(state: PipelineState) -> PipelineState:
    """
    Verify journal abbreviation using NLM Catalog API.
    Updates state.extracted['verified_journal_abbrev'] and logs issues in state.corrections or state.verification_message.
    """
    journal = state.get("extracted", {}).get("journal_name", "")
    current_abbrev = state.get("extracted", {}).get("journal_abbrev", "")
    
    if not journal:
        state["verification_message"] = state.get("verification_message", "") + "No journal name provided for abbreviation verification. "
        state["corrections"] = state.get("corrections", []) + [("journal_abbrev", current_abbrev, "Missing journal name")]
        return state

    try:
        url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        params = {
            "db": "nlmcatalog",
            "term": f"{journal}[Journal]",
            "retmode": "json",
            "retmax": 1
        }
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()

        if data.get("esearchresult", {}).get("idlist"):
            nlm_id = data["esearchresult"]["idlist"][0]
            summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            summary_params = {
                "db": "nlmcatalog",
                "id": nlm_id,
                "retmode": "json"
            }
            summary_response = requests.get(summary_url, params=summary_params, timeout=5)
            summary_response.raise_for_status()
            summary_data = summary_response.json()

            journal_data = summary_data.get("result", {}).get(nlm_id, {})
            standard_abbrev = journal_data.get("isoabbreviation", "")
            
            if standard_abbrev:
                state["extracted"]["verified_journal_abbrev"] = standard_abbrev
                if current_abbrev and current_abbrev.lower() != standard_abbrev.lower():
                    state["corrections"] = state.get("corrections", []) + [
                        ("journal_abbrev", current_abbrev, standard_abbrev)
                    ]
                    state["verification_message"] = state.get("verification_message", "") + \
                        f"Journal abbreviation corrected: '{current_abbrev}' to '{standard_abbrev}'. "
            else:
                state["verification_message"] = state.get("verification_message", "") + \
                    f"No standard abbreviation found for journal: {journal}. "
                state["corrections"] = state.get("corrections", []) + [
                    ("journal_abbrev", current_abbrev, "Not found")
                ]
        else:
            state["verification_message"] = state.get("verification_message", "") + \
                f"Journal not found in NLM Catalog: {journal}. "
            state["corrections"] = state.get("corrections", []) + [
                ("journal_abbrev", current_abbrev, "Journal not found")
            ]
            
    except requests.RequestException as e:
        state["verification_message"] = state.get("verification_message", "") + \
            f"Failed to verify journal abbreviation: {str(e)}. "
        state["corrections"] = state.get("corrections", []) + [
            ("journal_abbrev", current_abbrev, f"Verification error: {str(e)}")
        ]
    
    return state