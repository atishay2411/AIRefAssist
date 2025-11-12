from __future__ import annotations
import os
from typing import Dict, Any
from dotenv import load_dotenv, find_dotenv
from langchain_openai import AzureChatOpenAI

from ..rag.service import (
    StyleGuideConfig,
    StyleGuideRAGService,
    build_query_from_state
)

# ---------------------------------------------------------------------
# Load environment
# ---------------------------------------------------------------------
load_dotenv(find_dotenv(), override=True)

# ---------------------------------------------------------------------
# System prompt for IEEE reference formatting
# ---------------------------------------------------------------------
SYSTEM_PROMPT = """You are an expert IEEE reference formatter.
Your task is to generate an EXACT IEEE-style reference for the given metadata.

Follow the IEEE Editorial Style Manual exactly and only use the retrieved style guide snippets.
Do not invent or hallucinate missing data.
If a field is missing, omit it gracefully.

Formatting rules:
- For authors:
  * Up to six authors: list all.
  * Seven or more: list first author followed by “et al.”
  * Use initials for given and middle names (F.M. Lastname).
  * Include middle initials if available; do not omit them.
  * Preserve non-Western name order and compound surnames (e.g., “van der Waals”).
  * Separate multiple authors with commas, and before the last author use “and”.
  * No spaces between initials (e.g., “F.M.” not “F. M.”).
- Page numbers:
  * Multiple pages → “pp.”
  * Single page → “p.”
- Include DOI or URL only if provided.
- Respect capitalization, punctuation, and italicization.

For titles:
- Apply correct IEEE title casing (capitalize major words, keep acronyms uppercase).
- Preserve acronyms (AI, IEEE, DNA, GPT-5, etc.) and chemical or mathematical symbols.
- Never translate, shorten, or rephrase titles — only fix casing and punctuation.
- If a title already appears correctly formatted, keep it as-is.

Use '*' for italics and '**' for bold text.
Never include reasoning, explanations, or intermediate steps.
Return only the final formatted IEEE reference as a single line.
"""

# ---------------------------------------------------------------------
# Main IEEE formatter using RAG
# ---------------------------------------------------------------------
async def llm_format(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formats a reference using IEEE style.
    Uses Azure OpenAI LLM + RAG retrieval from StyleGuideRAGService.
    """

    # --- Initialize RAG service ---
    cfg = StyleGuideConfig.from_env()
    rag_service = StyleGuideRAGService(cfg)

    # --- Build query from pipeline state ---
    query = build_query_from_state(state)
    snippets = rag_service.retrieve_snippets(query)

    # --- Construct context block ---
    context_block = "IEEE STYLE GUIDE EXCERPTS:\n" + "\n\n".join(
        f"[{s['rank']}] {s['text']}" for s in snippets
    )

    # --- Prepare input data for formatting ---
    rtype = (state.get("type") or "unknown").lower()
    extracted = state.get("extracted") or {}
    corrected = state.get("corrected_entities") or {}

    # Merge corrected fields (from LLM correction) into extracted ones
    merged_fields = {**extracted, **corrected}
    fields_text = "\n".join(f"{k}: {v}" for k, v in merged_fields.items() if v)

    # --- Print BEFORE sending to LLM - For logging in terminal ---
    print("\n" + "=" * 80)
    print("[FORMAT BEFORE] Preparing to format reference")
    print("- Reference type:", rtype.upper())
    print("- Metadata to format (excerpt):")
    for k, v in merged_fields.items():
        print(f"    {k}: {v}")
    print("=" * 80 + "\n")

    # --- Build user prompt ---
    user_prompt = f"""{context_block}

REFERENCE_TYPE: {rtype.upper()}

METADATA FIELDS:
{fields_text or '(none)'}

Format this reference exactly per IEEE style.
Output only the final formatted line, nothing else.
"""

    # --- Initialize Azure OpenAI LLM ---
    llm = AzureChatOpenAI(
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        azure_deployment=os.getenv("AZURE_LLM_DEPLOYMENT", "gpt-4o-base"),
        openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        temperature=0.0,
    )

    # --- Invoke LLM with system + user messages ---
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    response = await llm.ainvoke(messages)
    formatted = response.content.strip()

    # --- Print AFTER receiving LLM output ---
    print("\n" + "=" * 80)
    print("[FORMAT AFTER] LLM returned formatted reference:")
    print(formatted)
    print("=" * 80 + "\n")

    # --- Store back to pipeline state ---
    state["style_snippets"] = [s["text"] for s in snippets]
    state["formatted"] = formatted
    state["style_query"] = query

    return state
