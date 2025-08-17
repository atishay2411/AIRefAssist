from langgraph.graph import StateGraph, START, END
from ..state import PipelineState
from ..config import PipelineConfig
from ..nodes import (
    init_runtime, detect_type, parse_extract, multisource_lookup, select_best,
    verify_agents, apply_corrections, llm_correct, enrich_from_best,
    format_reference, build_exports, build_report, cleanup, route_after_verify
)

def build_graph(cfg: PipelineConfig = PipelineConfig()) -> StateGraph:
    g = StateGraph(PipelineState)

    g.add_node("InitRuntime", init_runtime)
    g.add_node("DetectType", detect_type)
    g.add_node("ParseExtract", parse_extract)
    g.add_node("MultiSourceLookup", multisource_lookup)
    g.add_node("SelectBest", select_best)
    g.add_node("VerifyAgents", verify_agents)
    g.add_node("ApplyCorrections", apply_corrections)
    g.add_node("LLMCorrect", llm_correct)
    g.add_node("EnrichFromBest", enrich_from_best)
    g.add_node("FormatReference", format_reference)
    g.add_node("BuildExports", build_exports)
    g.add_node("BuildReport", build_report)
    g.add_node("Cleanup", cleanup)

    g.add_edge(START, "InitRuntime")
    g.add_edge("InitRuntime", "DetectType")
    g.add_edge("DetectType", "ParseExtract")
    g.add_edge("ParseExtract", "MultiSourceLookup")
    g.add_edge("MultiSourceLookup", "SelectBest")
    g.add_edge("SelectBest", "VerifyAgents")

    g.add_conditional_edges("VerifyAgents", route_after_verify, {
        "FormatReference": "FormatReference",
        "ApplyCorrections": "ApplyCorrections",
    })

    g.add_edge("ApplyCorrections", "LLMCorrect")
    g.add_edge("LLMCorrect", "EnrichFromBest")
    g.add_edge("EnrichFromBest", "MultiSourceLookup")

    g.add_edge("FormatReference", "BuildExports")
    g.add_edge("BuildExports", "BuildReport")
    g.add_edge("BuildReport", "Cleanup")
    g.add_edge("Cleanup", END)
    return g

async def run_one(reference: str, cfg: PipelineConfig = PipelineConfig(), recursion_limit: int | None = None):
    graph = build_graph(cfg).compile()
    state: PipelineState = {"reference": reference, "_cfg": cfg}
    return await graph.ainvoke(state, config={"recursion_limit": recursion_limit or cfg.recursion_limit})
