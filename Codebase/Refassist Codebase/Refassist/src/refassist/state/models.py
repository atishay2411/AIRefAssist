from typing import Any, Dict, List, Optional, Tuple, Set
from typing_extensions import TypedDict

try:
    from pydantic import BaseModel
except Exception:
    class BaseModel:
        def __init__(self, **kw): ...
        def dict(self, **kw): return {}

class ExtractedModel(BaseModel):
    title: Optional[str] = None
    authors: Optional[List[str]] = None
    journal_name: Optional[str] = None
    journal_abbrev: Optional[str] = None
    conference_name: Optional[str] = None
    verified_journal_abbrev: Optional[str] = None
    volume: Optional[str] = None
    issue: Optional[str] = None
    pages: Optional[str] = None
    year: Optional[str] = None
    month: Optional[str] = None
    doi: Optional[str] = None
    publisher: Optional[str] = None
    location: Optional[str] = None
    edition: Optional[str] = None
    isbn: Optional[str] = None
    url: Optional[str] = None
    arxiv_id: Optional[str] = None

class PipelineState(TypedDict, total=False):
    reference: str
    type: str
    extracted: Dict[str, Any]
    candidates: List[Dict[str, Any]]
    best: Dict[str, Any]
    verification: Dict[str, bool]
    suggestions: Dict[str, Any]
    corrections: List[Tuple[str, Any, Any]]
    formatted: str
    report: str
    attempts: int
    hops: int
    _made_changes_last_cycle: bool
    _cfg: Any
    _llm: Any
    _http: Any
    _cache: Any
    _limiter: Any
    _sources: Any
    _llm_type_vote: Optional[str]
    csl_json: Dict[str, Any]
    bibtex: str
    _ver_score: int
    _stagnation: int
    _fp: str
    _fp_history: Set[str]
    _loop_detected: bool
    _skip_pipeline: Optional[bool]
    verification_message: Optional[str]
    matching_fields: List[str]  # NEW: List of fields that matched the best candidate