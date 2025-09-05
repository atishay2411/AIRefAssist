from dataclasses import dataclass
import os

@dataclass
class PipelineConfig:
    timeout_s: float = float(os.getenv("IEEE_REF_TIMEOUT", "12"))
    concurrency: int = int(os.getenv("IEEE_REF_CONCURRENCY", "8"))
    cache_ttl_s: int = int(os.getenv("IEEE_REF_CACHE_TTL", "3600"))
    max_correction_rounds: int = int(os.getenv("IEEE_REF_MAX_CORR", "3"))
    max_hops: int = int(os.getenv("IEEE_REF_MAX_HOPS", "12"))
    stagnation_patience: int = int(os.getenv("IEEE_REF_STAGNATION", "2"))
    llm_provider: str = os.getenv("IEEE_REF_LLM", "auto")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2")
    ollama_base: str = os.getenv("OLLAMA_BASE_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    agent_threads: int = int(os.getenv("IEEE_REF_AGENT_THREADS", "6"))
    recursion_limit: int = int(os.getenv("IEEE_REF_RECURSION_LIMIT", "60"))
