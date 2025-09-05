from typing import Any, Dict, List, Tuple
from ..state import PipelineState
from ..tools.utils import normalize_text
from ..tools.sources.arxiv import ArxivClient
import asyncio
import re

def _normalize_candidate(source: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    from ..tools.utils import normalize_text
    out: Dict[str, Any] = {"source": source, "raw": rec}
    if source == "crossref":
        out["title"] = normalize_text((rec.get("title") or [""])[0]) if rec.get("title") else ""
        out["authors"] = [
            normalize_text(f"{a.get('given','')} {a.get('family','')}".strip())
            for a in rec.get("author", [])] if rec.get("author") else []
        out["journal_name"] = normalize_text((rec.get("container-title") or [""])[0]) if rec.get("container-title") else ""
        out["journal_abbrev"] = normalize_text((rec.get("short-container-title") or [""])[0]) if rec.get("short-container-title") else ""
        out["volume"] = normalize_text(rec.get("volume") or "")
        out["issue"] = normalize_text(rec.get("issue") or "")
        out["pages"] = normalize_text(rec.get("page") or "")
        out["doi"] = normalize_text(rec.get("DOI") or "")
        out["cr_type"] = normalize_text(rec.get("type") or "")
        y, m = "", ""
        for src in ("issued", "published-print", "published-online"):
            dp = (rec.get(src) or {}).get("date-parts")
            if dp:
                y = str(dp[0][0])
                if len(dp[0]) > 1:
                    m = str(dp[0][1])
                break
        out["year"], out["month"] = y, m
    elif source == "openalex":
        out["title"] = normalize_text(rec.get("display_name") or rec.get("title") or "")
        out["authors"] = [
            normalize_text(a.get("author", {}).get("display_name") or "")
            for a in rec.get("authorships", [])
        ] if rec.get("authorships") else []
        hv = rec.get("host_venue", {}) if isinstance(rec.get("host_venue"), dict) else {}
        out["journal_name"] = normalize_text(hv.get("display_name") or "")
        out["journal_abbrev"] = normalize_text(hv.get("abbrev") or "")
        out["doi"] = normalize_text(rec.get("doi") or "")
        out["volume"] = normalize_text(rec.get("biblio", {}).get("volume") or "")
        out["issue"] = normalize_text(rec.get("biblio", {}).get("issue") or "")
        fp = rec.get("biblio", {}).get("first_page") or ""
        lp = rec.get("biblio", {}).get("last_page") or ""
        out["pages"] = f"{fp}-{lp}" if fp and lp else normalize_text(fp or "")
        out["year"] = str(rec.get("publication_year") or (rec.get("from_publication_date") or "")[:4] or "")
        out["month"] = ""
        out["oa_is_proceedings"] = "proceedings" in (hv.get("display_name") or "").lower()
    elif source == "semanticscholar":
        out["title"] = normalize_text(rec.get("title") or "")
        out["authors"] = [normalize_text(a.get("name") or "") for a in rec.get("authors", [])] if rec.get("authors") else []
        out["journal_name"] = normalize_text(rec.get("venue") or (rec.get("publicationVenue") or {}).get("name") or "")
        out["journal_abbrev"] = ""
        eid = rec.get("externalIds") or {}
        out["doi"] = normalize_text(eid.get("DOI") or rec.get("doi") or "")
        out["year"] = normalize_text(rec.get("year") or "")
        out["month"] = ""
        out["s2_types"] = [normalize_text(t) for t in (rec.get("publicationTypes") or [])]
    elif source == "pubmed":
        out["title"] = normalize_text(rec.get("title") or rec.get("sorttitle") or "")
        out["authors"] = [normalize_text(a.get("name")) for a in rec.get("authors", []) if a.get("name")] if rec.get("authors") else []
        out["journal_name"] = normalize_text((rec.get("fulljournalname") or rec.get("source") or ""))
        out["journal_abbrev"] = normalize_text(rec.get("source") or "")
        out["doi"] = normalize_text((rec.get("elocationid") or "").replace("doi:", "").strip())
        out["volume"] = normalize_text(rec.get("volume") or "")
        out["issue"] = normalize_text(rec.get("issue") or "")
        out["pages"] = normalize_text(rec.get("pages") or "")
        out["year"] = normalize_text((rec.get("pubdate") or "").split(" ")[0])
        out["month"] = ""
    elif source == "arxiv":
        out["title"] = normalize_text(rec.get("title") or "")
        out["authors"] = [normalize_text(a) for a in rec.get("authors", [])]
        out["journal_name"] = "arXiv"
        out["journal_abbrev"] = "arXiv"
        out["doi"] = normalize_text(rec.get("doi") or "")
        out["year"] = normalize_text(rec.get("year") or "")
        out["month"] = ""
        out["volume"] = ""
        out["issue"] = ""
        out["pages"] = ""
    elif source == "ieee":
        # IEEE Xplore normalized mapping
        art = rec or {}
        out["title"] = normalize_text(art.get("title") or art.get("htmlTitle") or "")
        # authors: list of dicts with 'full_name'
        auths = []
        auth_block = art.get("authors") or {}
        for a in (auth_block.get("authors") or []):
            nm = a.get("full_name") or a.get("preferred_name") or ""
            nm = normalize_text(nm)
            if nm: auths.append(nm)
        out["authors"] = auths
        out["journal_name"] = normalize_text(art.get("publication_title") or art.get("pub_link") or "")
        out["journal_abbrev"] = ""
        out["doi"] = normalize_text(art.get("doi") or "")
        out["volume"] = normalize_text(art.get("volume") or "")
        out["issue"]  = normalize_text(art.get("issue") or "")
        sp = normalize_text(art.get("start_page") or "")
        ep = normalize_text(art.get("end_page") or "")
        out["pages"] = f"{sp}-{ep}" if sp and ep else sp
        out["year"] = normalize_text(str(art.get("publication_year") or ""))
        out["month"] = ""
    else:
        out.update({k: "" for k in ("title", "authors", "journal_name", "journal_abbrev", "doi", "volume", "issue", "pages", "year", "month")})
    return out

def _title_variants(title: str) -> List[str]:
    t = normalize_text(title)
    if not t:
        return []
    out = [t]
    m = re.split(r"\s*[:\-–—]\s*", t, maxsplit=1)
    if m and len(m[0]) >= 6:
        out.append(m[0])
    if len(t) > 180:
        out.append(t[:180])
    seen = set(); uniq=[]
    for v in out:
        if v not in seen:
            seen.add(v); uniq.append(v)
    return uniq

async def multisource_lookup(state: PipelineState) -> PipelineState:
    ex, sources = state["extracted"], state["_sources"]
    doi = normalize_text(ex.get("doi") or "").lower().replace("doi:", "")
    title = normalize_text(ex.get("title") or "")
    arxiv_id = normalize_text(ex.get("arxiv_id") or "")

    tasks = []
    for s in sources:
        if arxiv_id and isinstance(s, ArxivClient):
            tasks.append(s.by_id(arxiv_id))
        if doi:
            tasks.append(s.by_doi(doi))
        if title:
            for tv in _title_variants(title):
                tasks.append(s.by_title(tv))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    out_norm: List[Dict[str, Any]] = []
    idx = 0
    for s in sources:
        if arxiv_id and isinstance(s, ArxivClient):
            rec = results[idx]; idx += 1
            if rec:
                out_norm.append(_normalize_candidate(s.NAME, rec))
        if doi:
            rec = results[idx]; idx += 1
            if isinstance(rec, list):
                for r in rec:
                    out_norm.append(_normalize_candidate(s.NAME, r))
            elif isinstance(rec, dict) and rec:
                out_norm.append(_normalize_candidate(s.NAME, rec))
        if title:
            for _tv in _title_variants(title):
                rec = results[idx]; idx += 1
                if isinstance(rec, list):
                    for r in rec:
                        out_norm.append(_normalize_candidate(s.NAME, r))
                elif isinstance(rec, dict) and rec:
                    out_norm.append(_normalize_candidate(s.NAME, rec))

    dedup = {}
    for c in out_norm:
        key = (c["source"], (c.get("doi") or "").lower() or c.get("title") or "")
        dedup[key] = c
    state["candidates"] = list(dedup.values())
    return state
