import inspect
from time import perf_counter

from langgraph.graph import StateGraph, START, END
from ..logging import logger
from ..state import PipelineState
from ..config import PipelineConfig
from ..nodes import (
    init_runtime, analyze_reference, multisource_lookup, select_best,
    verify_agents, apply_corrections, enrich_from_best,
    format_reference, check_retraction, check_fabrication,
    build_exports, build_report, cleanup,
    route_after_verify,
)

# ------------------------------
# Internal helpers
# ------------------------------

def _timed(name: str, fn):
    """Log each node's wall-clock time at DEBUG — the first thing you need
    when a reference is slow is to know where the time went."""
    if inspect.iscoroutinefunction(fn):
        async def _aw(state):
            t0 = perf_counter()
            try:
                return await fn(state)
            finally:
                logger.debug("[timing] %-18s %6.2fs", name, perf_counter() - t0)
        return _aw

    def _w(state):
        t0 = perf_counter()
        try:
            return fn(state)
        finally:
            logger.debug("[timing] %-18s %6.2fs", name, perf_counter() - t0)
    return _w


# Cache a compiled graph so we don't rebuild it on every request.
_COMPILED = None

def build_graph(cfg: PipelineConfig | None = None) -> StateGraph:
    """
    Pipeline shape:

        InitRuntime → AnalyzeReference ─(not a reference)→ BuildReport
                            │
                            ▼
                    MultiSourceLookup  (all sources + NLM abbrev check, parallel)
                            │
          ┌── EnrichFromBest ← ApplyCorrections ──┤ (needs corrections)
          └──────────────→ MultiSourceLookup      │
                                                  ▼
              SelectBest → VerifyAgents ─(verified)→ FormatReference
                                                      │
              BuildExports → CheckRetraction → CheckFabrication → BuildReport → Cleanup

    Formatting is deterministic (rule-based only). The LLM formatter was
    removed after live testing: it drifted from IEEE style (italic et al.,
    serial comma on two authors, URL DOIs) and once minted an IEEE journal
    abbreviation for a venue that does not exist.
    """
    g = StateGraph(PipelineState)

    # Nodes — AnalyzeReference is a single LLM pass replacing the previous
    # validate → detect-type → parse-extract chain (3 LLM calls → 1).
    for name, fn in (
        ("InitRuntime", init_runtime),
        ("AnalyzeReference", analyze_reference),
        ("MultiSourceLookup", multisource_lookup),
        ("SelectBest", select_best),
        ("VerifyAgents", verify_agents),
        ("ApplyCorrections", apply_corrections),
        ("EnrichFromBest", enrich_from_best),
        ("FormatReference", format_reference),
        ("CheckRetraction", check_retraction),
        ("CheckFabrication", check_fabrication),
        ("BuildExports", build_exports),
        ("BuildReport", build_report),
        ("Cleanup", cleanup),
    ):
        g.add_node(name, _timed(name, fn))

    # Edges
    g.add_edge(START, "InitRuntime")
    g.add_edge("InitRuntime", "AnalyzeReference")

    g.add_conditional_edges(
        "AnalyzeReference",
        lambda s: "BuildReport" if s.get("_skip_pipeline") else "MultiSourceLookup",
        {"BuildReport": "BuildReport", "MultiSourceLookup": "MultiSourceLookup"},
    )

    g.add_edge("MultiSourceLookup", "SelectBest")
    g.add_edge("SelectBest", "VerifyAgents")

    # After verification, either exit to formatting or continue corrections
    g.add_conditional_edges("VerifyAgents", route_after_verify, {
        "FormatReference": "FormatReference",
        "ApplyCorrections": "ApplyCorrections",
    })

    g.add_edge("ApplyCorrections", "EnrichFromBest")
    g.add_edge("EnrichFromBest", "MultiSourceLookup")

    g.add_edge("FormatReference", "BuildExports")
    g.add_edge("BuildExports", "CheckRetraction")
    g.add_edge("CheckRetraction", "CheckFabrication")
    g.add_edge("CheckFabrication", "BuildReport")
    g.add_edge("BuildReport", "Cleanup")
    g.add_edge("Cleanup", END)
    return g


async def run_one(reference: str, cfg: PipelineConfig | None = None, recursion_limit: int | None = None):
    """
    Execute the pipeline for a single reference.
    Uses a module-level compiled graph (no re-compilation per call).
    """
    global _COMPILED
    cfg = cfg or PipelineConfig()
    if _COMPILED is None:
        _COMPILED = build_graph(cfg).compile()

    t0 = perf_counter()
    state: PipelineState = {"reference": reference, "_cfg": cfg}
    result = await _COMPILED.ainvoke(
        state,
        config={"recursion_limit": recursion_limit or cfg.recursion_limit}
    )
    logger.info("[pipeline] resolved in %.1fs (hops=%s, status=%s)",
                perf_counter() - t0, result.get("hops"),
                (result.get("report_data") or {}).get("status"))
    return result


def save_graph_png(out_path: str = "outputs/pipeline_graph.png") -> str:
    """Render the compiled pipeline as a Mermaid PNG (debug/docs helper, not called at runtime)."""
    import os
    graph = (_COMPILED or build_graph().compile()).get_graph()
    png_data = graph.draw_mermaid_png()
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(png_data)
    return out_path
