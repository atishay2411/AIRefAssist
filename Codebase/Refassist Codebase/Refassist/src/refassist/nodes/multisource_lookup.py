from typing import Any, Dict, List, Tuple
from ..state import PipelineState
from ..tools.utils import normalize_text, coerce_year, is_plausible_year
import asyncio
import re

try:
    from ..tools.utils import _trace_year
except Exception:
    def _trace_year(*args, **kwargs):
        return


def _pages_richness(s: str) -> int:
    if not s:
        return 0
    s2 = normalize_text(s).replace("—", "-").replace("–", "-")
    nums = re.findall(r"\d+", s2)
    if "-" in s2 and len(nums) >= 2:
        try:
            if int(nums[0]) != int(nums[1]):
                return 2
        except Exception:
            return 2
    if len(nums) >= 1:
        return 1
    return 0


def _normalize_candidate(source: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a record from any metadata source (Crossref, OpenAlex, etc.)
    into a unified dictionary structure for downstream comparison.
    """
    out: Dict[str, Any] = {"source": source, "raw": rec}

    def _set_year(y: Any) -> str:
        yv = coerce_year(y or "")
        return yv if is_plausible_year(yv) else ""

    if source == "crossref":
        title = normalize_text((rec.get("title") or [""])[0]) if rec.get("title") else ""
        authors = [
            normalize_text(f"{a.get('given','')} {a.get('family','')}".strip())
            for a in (rec.get("author") or [])
        ]
        out.update({
            "title": title,
            "authors": authors,
            "journal_name": normalize_text((rec.get("container-title") or [""])[0]) if rec.get("container-title") else "",
            "journal_abbrev": normalize_text((rec.get("short-container-title") or [""])[0]) if rec.get("short-container-title") else "",
            "volume": normalize_text(rec.get("volume") or ""),
            "issue": normalize_text(rec.get("issue") or ""),
            "pages": normalize_text(rec.get("page") or ""),
            "doi": normalize_text(rec.get("DOI") or ""),
            "type": normalize_text(rec.get("type") or ""),
        })

        # --- Pages sanity filter ---
        t = out.get("type", "")
        if t in {"book", "edited-book", "reference-book", "monograph"}:
            # Drop pages for full books — Crossref often leaks nonsense here
            out["pages"] = ""
        elif t == "book-chapter":
            # Keep Crossref chapter pages
            pass
        else:
            # If pages look bogus (e.g., single large number or malformed)
            nums = re.findall(r"\d+", out.get("pages", ""))
            if len(nums) == 1:
                try:
                    if int(nums[0]) > 500:
                        out["pages"] = ""
                except Exception:
                    pass
            elif not re.search(r"\d+\s*[-–—]\s*\d+", out.get("pages", "")):
                # Not a range, probably noise
                out["pages"] = ""

        # --- Year parsing ---
        y, m = "", ""
        raw_dates = {}
        for src_key in ("published-print", "published-online", "issued", "created", "deposited"):
            block = (rec.get(src_key) or {})
            raw_dates[src_key] = block
            dp = block.get("date-parts")
            if dp and isinstance(dp, list) and dp and dp[0]:
                y = str(dp[0][0])
                if len(dp[0]) > 1:
                    m = str(dp[0][1])
                if is_plausible_year(y):
                    break
        out["year"], out["month"] = _set_year(y), m
        _trace_year(
            "normalize_candidate/crossref",
            title=out.get("title"),
            doi=out.get("doi"),
            chosen_year=out.get("year"),
            raw_date_fields=raw_dates,
        )

    elif source == "openalex":
        out["title"] = normalize_text(rec.get("display_name") or rec.get("title") or "")
        out["authors"] = [
            normalize_text(a.get("author", {}).get("display_name") or "")
            for a in (rec.get("authorships") or [])
        ]
        hv = rec.get("host_venue", {}) if isinstance(rec.get("host_venue"), dict) else {}
        out["journal_name"] = normalize_text(hv.get("display_name") or "")
        out["journal_abbrev"] = normalize_text(hv.get("abbrev") or "")
        out["doi"] = normalize_text(rec.get("doi") or "")
        out["volume"] = normalize_text(rec.get("biblio", {}).get("volume") or "")
        out["issue"] = normalize_text(rec.get("biblio", {}).get("issue") or "")
        fp = rec.get("biblio", {}).get("first_page") or ""
        lp = rec.get("biblio", {}).get("last_page") or ""
        out["pages"] = f"{fp}-{lp}" if fp and lp else normalize_text(fp or "")
        y = rec.get("publication_year") or (rec.get("from_publication_date") or "")[:4]
        out["year"] = _set_year(y)
        out["month"] = ""

    elif source == "semanticscholar":
        out["title"] = normalize_text(rec.get("title") or "")
        out["authors"] = [normalize_text(a.get("name") or "") for a in (rec.get("authors") or [])]
        out["journal_name"] = normalize_text(rec.get("venue") or (rec.get("publicationVenue") or {}).get("name") or "")
        out["journal_abbrev"] = ""
        eid = rec.get("externalIds") or {}
        out["doi"] = normalize_text(eid.get("DOI") or rec.get("doi") or "")
        out["year"] = _set_year(rec.get("year"))
        out["month"] = ""

    elif source == "pubmed":
        out["title"] = normalize_text(rec.get("title") or rec.get("sorttitle") or "")
        out["authors"] = [normalize_text(a.get("name")) for a in (rec.get("authors") or []) if a.get("name")]
        out["journal_name"] = normalize_text(rec.get("fulljournalname") or rec.get("source") or "")
        out["journal_abbrev"] = normalize_text(rec.get("source") or "")
        out["doi"] = normalize_text((rec.get("elocationid") or "").replace("doi:", "").strip())
        out["volume"] = normalize_text(rec.get("volume") or "")
        out["issue"] = normalize_text(rec.get("issue") or "")
        out["pages"] = normalize_text(rec.get("pages") or "")
        raw_pubdate = normalize_text(rec.get("pubdate") or "")
        out["year"] = _set_year(raw_pubdate.split(" ")[0] if raw_pubdate else "")
        out["month"] = ""

    elif source == "arxiv":
        out["title"] = normalize_text(rec.get("title") or "")
        out["authors"] = [normalize_text(a) for a in (rec.get("authors") or [])]
        out["journal_name"] = "arXiv"
        out["journal_abbrev"] = "arXiv"
        out["doi"] = normalize_text(rec.get("doi") or "")
        out["year"] = _set_year(rec.get("year"))
        out["month"] = ""
        out["volume"] = ""
        out["issue"] = ""
        out["pages"] = ""

    elif source == "ieee":
        art = rec or {}
        out["title"] = normalize_text(art.get("title") or art.get("htmlTitle") or "")
        auths = []
        auth_block = art.get("authors") or {}
        for a in (auth_block.get("authors") or []):
            nm = a.get("full_name") or a.get("preferred_name") or ""
            nm = normalize_text(nm)
            if nm:
                auths.append(nm)
        out["authors"] = auths
        out["journal_name"] = normalize_text(art.get("publication_title") or art.get("pub_link") or "")
        out["journal_abbrev"] = ""
        out["doi"] = normalize_text(art.get("doi") or "")
        out["volume"] = normalize_text(art.get("volume") or "")
        out["issue"] = normalize_text(art.get("issue") or "")
        sp = normalize_text(art.get("start_page") or "")
        ep = normalize_text(art.get("end_page") or "")
        out["pages"] = f"{sp}-{ep}" if sp and ep else sp
        out["year"] = _set_year(art.get("publication_year"))
        out["month"] = ""

    else:
        out.update({k: "" for k in ("title", "authors", "journal_name", "journal_abbrev", "doi", "volume", "issue", "pages", "year", "month")})

    _trace_year("candidate_final", source=source, title=out.get("title"), doi=out.get("doi"), year=out.get("year"), pages=out.get("pages"))
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
    seen = set()
    uniq = []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


async def multisource_lookup(state: PipelineState) -> PipelineState:
    ex, sources = state["extracted"], state["_sources"]
    doi = normalize_text(ex.get("doi") or "").lower().replace("doi:", "")
    title = normalize_text(ex.get("title") or "")
    arxiv_id = normalize_text(ex.get("arxiv_id") or "")

    tasks = []
    via_tags: List[str] = []

    for s in sources:
        if arxiv_id and getattr(s, "NAME", "") == "arxiv":
            tasks.append(s.by_id(arxiv_id))
            via_tags.append("id")
        if doi:
            tasks.append(s.by_doi(doi))
            via_tags.append("doi")
        if title:
            for tv in _title_variants(title):
                tasks.append(s.by_title(tv))
                via_tags.append("title")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    out_norm: List[Dict[str, Any]] = []
    idx = 0
    for s in sources:
        sname = getattr(s, "NAME", getattr(s, "__class__", type("X", (object,), {})).__name__).lower()
        if arxiv_id and sname == "arxiv":
            rec = results[idx]
            via = via_tags[idx]
            idx += 1
            if rec and not isinstance(rec, Exception):
                cand = _normalize_candidate(sname, rec)
                cand["_via"] = via
                out_norm.append(cand)
        if doi:
            rec = results[idx]
            via = via_tags[idx]
            idx += 1
            if isinstance(rec, list):
                for r in rec:
                    cand = _normalize_candidate(sname, r)
                    cand["_via"] = via
                    out_norm.append(cand)
            elif isinstance(rec, dict) and rec:
                cand = _normalize_candidate(sname, rec)
                cand["_via"] = via
                out_norm.append(cand)
        if title:
            for _tv in _title_variants(title):
                rec = results[idx]
                via = via_tags[idx]
                idx += 1
                if isinstance(rec, list):
                    for r in rec:
                        cand = _normalize_candidate(sname, r)
                        cand["_via"] = via
                        out_norm.append(cand)
                elif isinstance(rec, dict) and rec:
                    cand = _normalize_candidate(sname, rec)
                    cand["_via"] = via
                    out_norm.append(cand)

    def _via_priority(v: str) -> int:
        return {"id": 3, "doi": 3, "title": 1}.get((v or "").lower(), 0)

    dedup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in out_norm:
        key = (c["source"], (c.get("doi") or "").lower() or c.get("title") or "")
        if key not in dedup:
            dedup[key] = c
            continue
        cur = dedup[key]
        cur_pri = _via_priority(cur.get("_via"))
        new_pri = _via_priority(c.get("_via"))
        replace = False
        if new_pri > cur_pri:
            replace = True
        elif new_pri == cur_pri:
            cy, ny = coerce_year(cur.get("year", "")), coerce_year(c.get("year", ""))
            if is_plausible_year(cy) and is_plausible_year(ny):
                if int(ny) < int(cy):
                    replace = True
            elif not is_plausible_year(cy) and is_plausible_year(ny):
                replace = True
            elif is_plausible_year(cy) and not is_plausible_year(ny):
                replace = False
            else:
                if _pages_richness(c.get("pages", "")) > _pages_richness(cur.get("pages", "")):
                    replace = True
        if replace:
            dedup[key] = c

    print("\n=== CANDIDATES SUMMARY (post-dedup) ===")
    for c in dedup.values():
        print(f"{c['source']:15} via={c.get('_via')} year={c.get('year')} doi={c.get('doi')}")
    print("========================================\n")

    state["candidates"] = list(dedup.values())
    _trace_year(
        "multisource_lookup/summary",
        candidates=[(c["source"], c.get("year"), c.get("_via")) for c in dedup.values()],
    )
    return state
