from langgraph.graph import StateGraph, START, END
from ..state import PipelineState
from ..config import PipelineConfig
from ..nodes import (
    init_runtime, detect_type, parse_extract, multisource_lookup, select_best,
    verify_agents, apply_corrections, llm_correct, enrich_from_best,
    format_reference, build_exports, build_report, cleanup, route_after_verify
)
from ..nodes.validate_reference import validate_input_reference
from ..nodes.verify_journal_abbrev import verify_journal_abbrev
from ..nodes.llm_format import llm_format  # NEW

def _has_llm_formatted(state: PipelineState) -> bool:
    s = (state.get("formatted") or "").strip()
    return bool(s) and (len(s) > 10)

def build_graph(cfg: PipelineConfig = PipelineConfig()) -> StateGraph:
    g = StateGraph(PipelineState)

    # Nodes
    g.add_node("InitRuntime", init_runtime)
    g.add_node("VerifyReferenceType", validate_input_reference)
    g.add_node("DetectType", detect_type)
    g.add_node("ParseExtract", parse_extract)
    g.add_node("VerifyJournalAbbrev", verify_journal_abbrev)
    g.add_node("MultiSourceLookup", multisource_lookup)
    g.add_node("SelectBest", select_best)
    g.add_node("VerifyAgents", verify_agents)
    g.add_node("ApplyCorrections", apply_corrections)
    g.add_node("LLMCorrect", llm_correct)
    g.add_node("EnrichFromBest", enrich_from_best)
    g.add_node("LLMFormat", llm_format)           # NEW: LLM-first formatter
    g.add_node("FormatReference", format_reference) # Fallback rules
    g.add_node("BuildExports", build_exports)
    g.add_node("BuildReport", build_report)
    g.add_node("Cleanup", cleanup)

    # Edges
    g.add_edge(START, "InitRuntime")
    g.add_edge("InitRuntime", "VerifyReferenceType")

    g.add_conditional_edges(
        "VerifyReferenceType",
        lambda s: "DetectType" if not s.get("_skip_pipeline") else "BuildReport",
        {"DetectType": "DetectType", "BuildReport": "BuildReport"},
    )

    g.add_edge("DetectType", "ParseExtract")
    g.add_edge("ParseExtract", "VerifyJournalAbbrev")
    g.add_edge("VerifyJournalAbbrev", "MultiSourceLookup")
    g.add_edge("MultiSourceLookup", "SelectBest")
    g.add_edge("SelectBest", "VerifyAgents")

    # After verification, either exit to formatting or continue corrections
    g.add_conditional_edges("VerifyAgents", route_after_verify, {
        "FormatReference": "LLMFormat",   # try LLM formatter first
        "ApplyCorrections": "ApplyCorrections",
    })

    g.add_edge("ApplyCorrections", "LLMCorrect")
    g.add_edge("LLMCorrect", "EnrichFromBest")
    g.add_edge("EnrichFromBest", "MultiSourceLookup")

    # If LLM formatting failed, fallback to rule-based formatter
    g.add_conditional_edges(
        "LLMFormat",
        lambda s: "BuildExports" if _has_llm_formatted(s) else "FormatReference",
        {"BuildExports":"BuildExports", "FormatReference":"FormatReference"},
    )

    g.add_edge("FormatReference", "BuildExports")
    g.add_edge("BuildExports", "BuildReport")
    g.add_edge("BuildReport", "Cleanup")
    g.add_edge("Cleanup", END)
    return g



# async def run_one(reference: str, cfg: PipelineConfig = PipelineConfig(), recursion_limit: int | None = None):
#     graph = build_graph(cfg).compile()
#     state: PipelineState = {
#         "reference": reference,
#         "_cfg": cfg,
#         "_skip_pipeline": False,            # initialize new key
#         "verification_message": ""          # initialize new key
#     }
#     return await graph.ainvoke(state, config={"recursion_limit": recursion_limit or cfg.recursion_limit})


from pathlib import Path

async def run_one(reference: str, cfg: PipelineConfig = PipelineConfig(), recursion_limit: int | None = None):
    # Build and compile the graph
    compiled = build_graph(cfg).compile()
    
    # Get PNG bytes
    png_bytes = compiled.get_graph().draw_mermaid_png()
    
    # Save to file
    graph_path = Path("pipeline_graph.png")
    graph_path.write_bytes(png_bytes)
    print(f"Graph saved to: {graph_path.resolve()}")
    
    # Prepare the initial state
    state: PipelineState = {"reference": reference, "_cfg": cfg}
    
    # Run the graph asynchronously
    return await compiled.ainvoke(state, config={"recursion_limit": recursion_limit or cfg.recursion_limit})
