import os
from typing import Any, Dict
from dotenv import load_dotenv
from ..config import PipelineConfig
from ..logging import logger
from ..tools.utils import safe_json_load, DEFAULT_UA

try:
    import httpx
except ImportError:
    httpx = None


# Load environment variables early
load_dotenv()


class LLMAdapter:
    """
    LLM adapter supporting OpenAI, Azure OpenAI, Anthropic, and Ollama.
    Provides `.json(prompt)` and `.text(prompt)` async methods.
    """

    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.provider = self._auto_provider(cfg.llm_provider)
        self._client = None
        self._init_client()

    # ------------------------
    # Provider detection
    # ------------------------
    def _auto_provider(self, p: str) -> str:
        if p != "auto":
            return p
        if os.getenv("OPENAI_API_KEY"):
            return "openai"
        if os.getenv("AZURE_OPENAI_API_KEY"):
            return "azure"
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.getenv("OLLAMA_BASE_URL") or os.getenv("OLLAMA_HOST"):
            return "ollama"
        return "dummy"

    # ------------------------
    # Client initialization
    # ------------------------
    def _init_client(self):
        prov = self.provider
        try:
            if prov == "openai":
                from openai import OpenAI
                base = os.getenv("OPENAI_API_BASE")
                self._client = OpenAI(base_url=base) if base else OpenAI()

            elif prov == "azure":
                from openai import AzureOpenAI
                ep = os.getenv("AZURE_OPENAI_ENDPOINT")
                ver = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")
                key = os.getenv("AZURE_OPENAI_API_KEY")

                if not ep or not key:
                    raise RuntimeError("Missing Azure OpenAI configuration (endpoint or key).")

                # Correct usage for openai>=1.0
                self._client = AzureOpenAI(
                    azure_endpoint=ep,
                    api_key=key,
                    api_version=ver
                )

                logger.info("[LLMAdapter] Initialized azure client successfully.")

            elif prov == "anthropic":
                import anthropic
                self._client = anthropic.AsyncAnthropic()

            elif prov == "ollama" and httpx is not None:
                base = (
                    os.getenv("OLLAMA_BASE_URL")
                    or os.getenv("OLLAMA_HOST")
                    or self.cfg.ollama_base
                )
                self._client = httpx.AsyncClient(
                    base_url=base,
                    timeout=self.cfg.timeout_s,
                    headers={"User-Agent": DEFAULT_UA},
                )
        except Exception as e:
            logger.warning(f"[LLMAdapter] Init failed: {e}")
            self._client = None
            self.provider = "dummy"

    # ------------------------
    # JSON mode
    # ------------------------
    async def json(self, prompt: str) -> Dict[str, Any]:
        try:
            if self.provider == "openai":
                raw = await self._openai_json(prompt)
            elif self.provider == "azure":
                raw = await self._azure_json(prompt)
            elif self.provider == "anthropic":
                raw = await self._anthropic_json(prompt)
            elif self.provider == "ollama":
                raw = await self._ollama_json(prompt)
            else:
                return {}
            return safe_json_load(raw) or {}
        except Exception as e:
            logger.warning(f"[LLMAdapter] json() failed: {e}")
            return {}

    async def _openai_json(self, prompt: str) -> str:
        model = self.cfg.openai_model
        resp = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return STRICT JSON only. No prose."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            top_p=0.1,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content

    async def _azure_json(self, prompt: str) -> str:
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or self.cfg.openai_model
        resp = self._client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": "Return STRICT JSON only. No prose."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            top_p=0.1,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content

    # ------------------------
    # TEXT mode (for formatting)
    # ------------------------
    async def text(self, prompt: str) -> str:
        try:
            if self.provider == "openai":
                return await self._openai_text(prompt)
            elif self.provider == "azure":
                return await self._azure_text(prompt)
            elif self.provider == "anthropic":
                return await self._anthropic_text(prompt)
            elif self.provider == "ollama":
                return await self._ollama_text(prompt)
            else:
                return ""
        except Exception as e:
            logger.warning(f"[LLMAdapter] text() failed: {e}")
            return ""

    async def _openai_text(self, prompt: str) -> str:
        model = self.cfg.openai_model
        resp = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a precise formatter. Output plain text only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            top_p=0.1,
        )
        return resp.choices[0].message.content or ""

    async def _azure_text(self, prompt: str) -> str:
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT") or self.cfg.openai_model
        resp = self._client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": "You are a precise formatter. Output plain text only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            top_p=0.1,
        )
        return resp.choices[0].message.content or ""


