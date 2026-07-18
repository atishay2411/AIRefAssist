"""RefAssist — Agentic IEEE Reference Pipeline (LangGraph)"""

__version__ = "0.9.0"

from .graphs.pipeline import build_graph, run_one
from .config import PipelineConfig

__all__ = ["build_graph", "run_one", "PipelineConfig", "__version__"]
