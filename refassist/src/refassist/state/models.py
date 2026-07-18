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
    book_title: Optional[str] = None      # container title for chapters
    editors: Optional[List[str]] = None   # list of editor names


class PipelineState(TypedDict, total=False):
    # NOTE: every key that must survive between graph nodes MUST be declared
    # here — LangGraph drops undeclared keys when merging node output.
    reference: str
    type: str
    extracted: Dict[str, Any]
    candidates: List[Dict[str, Any]]
    best: Dict[str, Any]
    provenance: Dict[str, str]      # field -> source that supplied best value
    audit: Dict[str, str]           # field -> source that supplied applied correction
    verification: Dict[str, bool]
    suggestions: Dict[str, Any]
    corrections: List[Tuple[str, Any, Any]]
    formatted: str
    _formatter: str  # which formatter actually produced `formatted`
    retracted: bool
    retraction_info: Dict[str, Any]  # nature/date/reasons from Retraction Watch
    author_mismatch: Dict[str, Any]  # title matched a published work, authors disjoint
    version_alternatives: List[str]
    report: str
    report_data: Dict[str, Any]
    report_path: str
    style_snippets: List[str]
    style_query: str
    corrected_entities: Dict[str, Any]
    attempts: int
    hops: int
    _owns_http: bool
    _timed_out_jobs: Set[str]
    _started_at: float
    _biblio_prefetch: Any  # asyncio.Task started during AnalyzeReference
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
    _original_extracted: Dict[str, Any]  # user's fields as first extracted (immutable)
    _locked_fields: Set[str]    # fields verified OK in an earlier round — monotonic
    identity_conflict: bool     # best contradicts user coords that match another record
    fabrication: Dict[str, Any]  # fabrication-detection verdict (risk, signals, checks)