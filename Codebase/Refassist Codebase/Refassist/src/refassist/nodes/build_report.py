# from ..state import PipelineState
# from ..tools.utils import authors_to_list

# def build_report(state: PipelineState) -> PipelineState:
#     lines = []
#     if state.get("_skip_pipeline"):
#         lines.append(state.get("verification_message", "Reference validation failed."))
#     else:
#         changes = state.get("corrections", [])
#         ver = state.get("verification", {})
#         matching_fields = state.get("matching_fields", [])
#         audit = state.get("audit", {})  # field -> provenance source
#         provenance = state.get("provenance", {})  # best pick provenance

#         if matching_fields:
#             lines.append(f"Fields matched with authoritative source: {', '.join(matching_fields)}")
#         else:
#             lines.append("No fields matched with authoritative sources.")

#         if not changes:
#             lines.append("No corrections were necessary.")
#         else:
#             lines.append("Corrections applied (field: old → new):")
#             for f, old, new in changes:
#                 prov = audit.get(f) or provenance.get(f) or "unknown"
#                 if f == "authors":
#                     old_str = ", ".join(authors_to_list(old)) if isinstance(old, (str, list)) else str(old)
#                     new_str = ", ".join(authors_to_list(new)) if isinstance(new, (str, list)) else str(new)
#                     lines.append(f"- {f}: '{old_str}' → '{new_str}'  [source: {prov}]")
#                 else:
#                     lines.append(f"- {f}: '{old}' → '{new}'  [source: {prov}]")

#         failed = [k for k, v in ver.items() if not v]
#         if failed:
#             lines.append("Fields still needing attention: " + ", ".join(sorted(failed)))
#         else:
#             lines.append("All verification checks passed after corrections.")

#     state["report"] = "\n".join(lines)
#     return state


import os
from docx import Document
from docx.shared import Pt
from ..state import PipelineState
from ..tools.utils import authors_to_list

TEMPLATE_PATH = os.path.join(os.getcwd(), "Template.docx")
EXPORTS_DIR = os.path.join(os.getcwd(), "exports")

def _format_corrections(changes, prov, audit):
    if not changes:
        return "No corrections were needed."

    lines = []
    for f, old, new in changes:
        prov_source = audit.get(f) or prov.get(f) or "Unknown"
        if f == "authors":
            old_str = ", ".join(authors_to_list(old)) if old else "MISSING"
            new_str = ", ".join(authors_to_list(new)) if new else "MISSING"
            lines.append(f"- {f}: {old_str} → {new_str}  (source: {prov_source})")
        else:
            old_str = str(old) if old else "MISSING"
            new_str = str(new) if new else "MISSING"
            lines.append(f"- {f}: {old_str} → {new_str}  (source: {prov_source})")
    return "\n".join(lines)

def _format_provenance(best, ex, prov, audit):
    lines = []
    all_fields = [
        "title", "authors", "journal_name", "journal_abbrev", "conference_name",
        "volume", "issue", "pages", "year", "month", "doi",
        "publisher", "location", "edition", "isbn", "url"
    ]
    for f in all_fields:
        val = best.get(f) or ex.get(f)
        if not val:
            continue
        if f == "authors":
            val = ", ".join(authors_to_list(val))
        src = prov.get(f) or audit.get(f) or "Not available"
        lines.append(f"- {f}: {val}  (source: {src})")
    return "\n".join(lines)

def build_report(state: PipelineState) -> PipelineState:
    ex = state.get("extracted", {})
    best = state.get("best", {})
    prov = state.get("provenance", {}) or {}
    audit = state.get("audit", {}) or {}
    changes = state.get("corrections", [])
    ver = state.get("verification", {})
    matching_fields = set(state.get("matching_fields", []))
    formatted = state.get("formatted", "")
    fallback_used = not bool(formatted)
    failed = [k for k, v in ver.items() if not v]

    # Text chunks for placeholders
    overview = (
        f"- Type detected: {state.get('type','Unknown')}\n"
        f"- DOI: {best.get('doi') or 'Not available'}\n"
        f"- Primary source: {prov.get('doi') or prov.get('title') or 'Consensus'}"
    )

    verification = (
        f"Fields matched authoritative sources: {', '.join(sorted(matching_fields)) or 'None'}\n"
        f"Fields needing attention: {', '.join(sorted(failed)) or 'None'}"
    )

    corrections = _format_corrections(changes, prov, audit)
    provenance = _format_provenance(best, ex, prov, audit)

    formatting = (
        "LLM-based formatting applied successfully " if formatted
        else "LLM formatting failed or skipped \nFalling back to rule-based IEEE formatter "
    )

    final_reference = formatted or state.get("ieee_formatted", "") or "Error: No formatted reference available."
    suggestions = (
        "- Verify fields needing attention manually." if failed else "- No manual action needed "
    )

    # ---------------------------------------------------------
    # 1. Update state["report"] for CLI output
    # ---------------------------------------------------------
    state["report"] = f"""
IEEE Reference Report

1. Overview
{overview}

2. Field Verification
{verification}

3. Corrections Applied
{corrections}

4. Provenance (Source per Field)
{provenance}

5. Formatting Strategy
{formatting}

6. Final Formatted Reference
{final_reference}

7. Suggested Actions
{suggestions}
"""

    # ---------------------------------------------------------
    # 2. Generate Word report using template.docx
    # ---------------------------------------------------------
    if not os.path.exists(EXPORTS_DIR):
        os.makedirs(EXPORTS_DIR)

    if not os.path.exists(TEMPLATE_PATH):
        # Fallback: generate basic docx if template missing
        doc = Document()
        doc.add_heading("IEEE Reference Report", level=1)
        doc.add_paragraph(state["report"])
        doc.save(os.path.join(EXPORTS_DIR, "report.docx"))
        state["report_path"] = os.path.join(EXPORTS_DIR, "report.docx")
        return state

    # Use the provided template.docx
    doc = Document(TEMPLATE_PATH)

    # Replace placeholders in all paragraphs
    placeholders = {
        "{OVERVIEW}": overview,
        "{VERIFICATION}": verification,
        "{CORRECTIONS}": corrections,
        "{PROVENANCE}": provenance,
        "{FORMATTING}": formatting,
        "{FINAL_REFERENCE}": final_reference,
        "{SUGGESTIONS}": suggestions,
    }

    for p in doc.paragraphs:
        for placeholder, value in placeholders.items():
            if placeholder in p.text:
                inline = p.runs
                for i in range(len(inline)):
                    if placeholder in inline[i].text:
                        inline[i].text = inline[i].text.replace(placeholder, value)

    # Save the customized report
    report_path = os.path.join(EXPORTS_DIR, "report.docx")
    doc.save(report_path)
    state["report_path"] = report_path

    return state
