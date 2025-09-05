"""RefAssist — Agentic IEEE Reference Pipeline (LangGraph)"""

from .graphs.pipeline import build_graph, run_one
from .config import PipelineConfig
from dotenv import load_dotenv
import os

__all__ = ["build_graph", "run_one", "PipelineConfig"]

# Load top-level .env
load_dotenv()  

