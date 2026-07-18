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

# Load .env early
load_dotenv()


def azure_chat_deployment() -> str:
    """
    Canonical Azure chat deployment name, with legacy env fallbacks.
    Canonical: AZURE_CHAT_DEPLOYMENT
    Legacy:    AZURE_OPENAI_DEPLOYMENT, AZURE_LLM_DEPLOYMENT
    """
    return (
        os.getenv("AZURE_CHAT_DEPLOYMENT")
        or os.getenv("AZURE_OPENAI_DEPLOYMENT")
        or os.getenv("AZURE_LLM_DEPLOYMENT")
        or ""
    )


def azure_embedding_deployment() -> str:
    """
    Canonical Azure embeddings deployment name, with legacy env fallback.
    Canonical: AZURE_EMBEDDING_DEPLOYMENT
    Legacy:    AZURE_EMBEDDINGS_DEPLOYMENT
    """
    return (
        os.getenv("AZURE_EMBEDDING_DEPLOYMENT")
        or os.getenv("AZURE_EMBEDDINGS_DEPLOYMENT")
        or ""
    )


class LLMAdapter:
    """
    LLM adapter supporting:
    - OpenAI
    - Azure OpenAI
    - Anthropic
    - Ollama

    All providers use async clients so calls never block the event loop.

    Provides:
        await .json(prompt)
        await .text(prompt)
    """

    def __init__(self, cfg: PipelineConfig):
        self.cfg = cfg
        self.provider = self._auto_provider(cfg.llm_provider)
        self._client = None
        self._init_client()

    # ---------------------------------------------------------
    # Provider selection
    # ---------------------------------------------------------
    def _auto_provider(self, p: str) -> str:
        """
        Auto mode priority:
        1. OpenAI
        2. Azure
        3. Anthropic
        4. Ollama
        """
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

    # ---------------------------------------------------------
    # Client initialization
    # ---------------------------------------------------------
    def _init_client(self):
        prov = self.provider

        try:
            if prov == "openai":
                from openai import AsyncOpenAI
                base = os.getenv("OPENAI_API_BASE")
                self._client = AsyncOpenAI(base_url=base) if base else AsyncOpenAI()

            elif prov == "azure":
                from openai import AsyncAzureOpenAI

                ep = os.getenv("AZURE_OPENAI_ENDPOINT")
                key = os.getenv("AZURE_OPENAI_API_KEY")
                ver = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")

                if not ep or not key:
                    raise RuntimeError("Azure endpoint or key missing")
                if not azure_chat_deployment():
                    raise RuntimeError(
                        "Azure provider selected but no chat deployment configured "
                        "(set AZURE_CHAT_DEPLOYMENT)."
                    )

                self._client = AsyncAzureOpenAI(
                    api_key=key,
                    api_version=ver,
                    azure_endpoint=ep
                )

                logger.info("[LLMAdapter] AzureOpenAI initialized.")

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
                    timeout=max(self.cfg.timeout_s, 60.0),
                    headers={"User-Agent": DEFAULT_UA},
                )
                logger.info("[LLMAdapter] Ollama client ready at %s", base)

            else:
                logger.warning("[LLMAdapter] Dummy provider active — no LLM configured.")

        except Exception as e:
            logger.error("[LLMAdapter] Init failed: %s", e)
            self.provider = "dummy"
            self._client = None

    async def aclose(self):
        """Release provider resources (only meaningful for Ollama's httpx client)."""
        client = self._client
        self._client = None
        try:
            if client is not None and hasattr(client, "aclose"):
                await client.aclose()
        except Exception:
            pass

    # ---------------------------------------------------------
    # JSON Mode
    # ---------------------------------------------------------
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
            logger.warning("[LLMAdapter] json() failed: %s", e)
            return {}

    async def _openai_json(self, prompt: str) -> str:
        model = self.cfg.openai_model
        resp = await self._client.chat.completions.create(
            model=model,
            temperature=self.cfg.temperature,
            max_tokens=1500,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Return STRICT JSON only."},
                {"role": "user", "content": prompt},
            ]
        )
        return resp.choices[0].message.content

    async def _azure_json(self, prompt: str) -> str:
        deployment = azure_chat_deployment()
        if not deployment:
            raise RuntimeError("No Azure chat deployment configured (set AZURE_CHAT_DEPLOYMENT).")
        resp = await self._client.chat.completions.create(
            model=deployment,
            temperature=self.cfg.temperature,
            max_completion_tokens=1500,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Return STRICT JSON only."},
                {"role": "user", "content": prompt},
            ]
        )
        return resp.choices[0].message.content

    async def _anthropic_json(self, prompt: str) -> str:
        resp = await self._client.messages.create(
            model=self.cfg.anthropic_model,
            max_tokens=2000,
            temperature=self.cfg.temperature,
            system="Return STRICT JSON only. No prose, no code fences.",
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text

    async def _ollama_json(self, prompt: str) -> str:
        """
        Robust JSON generation for Ollama.
        Uses format=json to force JSON output.
        Strips code fences.
        Returns '{}' on malformed output.
        """
        if not self._client:
            raise RuntimeError("Ollama client not initialized")

        model = os.getenv("OLLAMA_MODEL", self.cfg.ollama_model)

        payload = {
            "model": model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": self.cfg.temperature},
        }

        resp = await self._client.post("/api/generate", json=payload)
        resp.raise_for_status()

        data = resp.json()
        raw_text = data.get("response", "")

        logger.debug("[OLLAMA JSON RAW] %s", raw_text)

        if not raw_text:
            logger.warning("[OLLAMA JSON] Empty response. Returning {}.")
            return "{}"

        # Remove code fences like ```json ... ```
        if raw_text.startswith("```"):
            cleaned = raw_text.strip("` \n")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
            raw_text = cleaned

        return raw_text

    # ---------------------------------------------------------
    # TEXT Mode
    # ---------------------------------------------------------
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
            logger.warning("[LLMAdapter] text() failed: %s", e)
            return ""

    async def _openai_text(self, prompt: str) -> str:
        model = self.cfg.openai_model
        resp = await self._client.chat.completions.create(
            model=model,
            temperature=self.cfg.temperature,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.choices[0].message.content or ""

    async def _azure_text(self, prompt: str) -> str:
        deployment = azure_chat_deployment()
        if not deployment:
            raise RuntimeError("No Azure chat deployment configured (set AZURE_CHAT_DEPLOYMENT).")
        resp = await self._client.chat.completions.create(
            model=deployment,
            temperature=self.cfg.temperature,
            max_completion_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.choices[0].message.content or ""

    async def _anthropic_text(self, prompt: str) -> str:
        resp = await self._client.messages.create(
            model=self.cfg.anthropic_model,
            max_tokens=2000,
            temperature=self.cfg.temperature,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text

    async def _ollama_text(self, prompt: str) -> str:
        if not self._client:
            raise RuntimeError("Ollama client not initialized")

        model = os.getenv("OLLAMA_MODEL", self.cfg.ollama_model)

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": self.cfg.temperature},
        }

        resp = await self._client.post("/api/generate", json=payload)
        resp.raise_for_status()

        data = resp.json()
        text = data.get("response", "")

        logger.debug("[OLLAMA TEXT RAW] %s", text)

        return text or ""
