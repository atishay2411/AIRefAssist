from typing import Any, Dict, List, Tuple
from ..logging import logger
from ..state import PipelineState
from ..tools.utils import normalize_text, coerce_year, is_plausible_year, bare_doi
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


_RETRACTED_PREFIX_RE = re.compile(r"^\s*(retracted|withdrawn)\s*(article)?\s*[:\-—]\s*", re.I)


def _strip_retraction_prefix(title: str) -> Tuple[str, bool]:
    """Publishers prepend "RETRACTED:" to titles; strip it (so matching still
    works) and surface it as a flag."""
    m = _RETRACTED_PREFIX_RE.match(title or "")
    if m:
        return title[m.end():].strip(), True
    return title, False


def _crossref_is_retracted(rec: Dict[str, Any]) -> bool:
    for u in (rec.get("updated-by") or []):
        t = f"{u.get('type', '')} {u.get('label', '')}".lower()
        if "retract" in t or "withdraw" in t:
            return True
    return False


def _normalize_candidate(source: str, rec: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a record from any metadata source (Crossref, OpenAlex, etc.)
    into a unified dictionary structure for downstream comparison.
    """
    out: Dict[str, Any] = {"source": source, "raw": rec, "retracted": False}

    def _set_year(y: Any) -> str:
        yv = coerce_year(y or "")
        return yv if is_plausible_year(yv) else ""

    if source == "crossref":
        title = normalize_text((rec.get("title") or [""])[0]) if rec.get("title") else ""
        title, flagged = _strip_retraction_prefix(title)
        out["retracted"] = flagged or _crossref_is_retracted(rec)
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
        oa_title = normalize_text(rec.get("display_name") or rec.get("title") or "")
        oa_title, flagged = _strip_retraction_prefix(oa_title)
        out["retracted"] = bool(rec.get("is_retracted")) or flagged
        out["title"] = oa_title
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
        pm_title = normalize_text(rec.get("title") or rec.get("sorttitle") or "")
        pm_title, flagged = _strip_retraction_prefix(pm_title)
        # PubMed marks retractions explicitly in the publication-type list
        pubtypes = rec.get("pubtype") or []
        out["retracted"] = flagged or any("retracted publication" in str(p).lower() for p in pubtypes)
        rec = dict(rec)
        rec["title"] = pm_title
        out["title"] = pm_title
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

    elif source == "europepmc":
        def _epmc_name(n: str) -> str:
            # Europe PMC uses "Family I" ("LeCun Y") — rotate to "I Family"
            # so author matching sees given-name-first like every other source.
            parts = n.strip().rstrip(".").split()
            if len(parts) >= 2 and parts[-1].isupper() and len(parts[-1]) <= 3:
                return f"{parts[-1]} {' '.join(parts[:-1])}"
            return n.strip()
        out["title"] = normalize_text(rec.get("title") or "").rstrip(".")
        out["authors"] = [_epmc_name(a) for a in (rec.get("authorString") or "").split(", ") if a.strip()]
        out["journal_name"] = normalize_text(rec.get("journalTitle") or "")
        out["journal_abbrev"] = ""
        out["volume"] = normalize_text(rec.get("journalVolume") or "")
        out["issue"] = normalize_text(rec.get("issue") or "")
        out["pages"] = normalize_text(rec.get("pageInfo") or "")
        out["doi"] = normalize_text(rec.get("doi") or "")
        out["year"] = _set_year(rec.get("pubYear"))
        out["month"] = ""

    elif source == "unpaywall":
        out["title"] = normalize_text(rec.get("title") or "")
        out["authors"] = [normalize_text(f"{a.get('given', '')} {a.get('family', '')}".strip())
                          for a in (rec.get("z_authors") or []) if a.get("family")]
        out["journal_name"] = normalize_text(rec.get("journal_name") or "")
        out["journal_abbrev"] = ""
        out["doi"] = normalize_text(rec.get("doi") or "")
        out["year"] = _set_year(rec.get("year"))
        out["month"] = ""
        out["volume"] = ""
        out["issue"] = ""
        out["pages"] = ""
        oa = rec.get("best_oa_location") or {}
        out["url"] = normalize_text(oa.get("url") or "")
        out["type"] = normalize_text(rec.get("genre") or "")

    elif source == "biorxiv":
        out["title"] = normalize_text(rec.get("title") or "")
        out["authors"] = [normalize_text(a) for a in (rec.get("authors") or "").split(";") if a.strip()]
        out["journal_name"] = "bioRxiv" if rec.get("server") != "medrxiv" else "medRxiv"
        out["journal_abbrev"] = out["journal_name"]
        out["doi"] = normalize_text(rec.get("doi") or "")
        out["year"] = _set_year((rec.get("date") or "")[:4])
        out["month"] = ""
        out["volume"] = ""
        out["issue"] = ""
        out["pages"] = ""
        out["type"] = "preprint"

    elif source == "doaj":
        out["title"] = normalize_text(rec.get("title") or "")
        out["authors"] = [normalize_text(a.get("name") or "") for a in (rec.get("author") or [])]
        j = rec.get("journal") or {}
        out["journal_name"] = normalize_text(j.get("title") or "")
        out["journal_abbrev"] = ""
        out["volume"] = normalize_text(j.get("volume") or "")
        out["issue"] = normalize_text(j.get("number") or "")
        sp, ep = rec.get("start_page") or "", rec.get("end_page") or ""
        out["pages"] = f"{sp}-{ep}" if sp and ep else normalize_text(str(sp))
        out["doi"] = next((normalize_text(i.get("id") or "") for i in (rec.get("identifier") or [])
                           if (i.get("type") or "").lower() == "doi"), "")
        out["year"] = _set_year(rec.get("year"))
        out["month"] = ""
        out["type"] = "journal-article"

    elif source == "googlebooks":
        out["title"] = normalize_text(rec.get("title") or "")
        out["authors"] = [normalize_text(a) for a in (rec.get("authors") or [])]
        out["journal_name"] = ""
        out["journal_abbrev"] = ""
        out["publisher"] = normalize_text(rec.get("publisher") or "")
        ids = rec.get("industryIdentifiers") or []
        out["isbn"] = next((i.get("identifier", "") for i in ids
                            if i.get("type") == "ISBN_13"),
                           next((i.get("identifier", "") for i in ids
                                 if i.get("type") == "ISBN_10"), ""))
        out["year"] = _set_year((rec.get("publishedDate") or "")[:4])
        out["month"] = ""
        out["volume"] = ""
        out["issue"] = ""
        out["pages"] = ""
        out["type"] = "book"

    elif source == "dblp":
        from ..tools.sources.dblp import dblp_type
        # DBLP appends a trailing period to titles
        out["title"] = normalize_text(rec.get("title") or "").rstrip(".")
        a = (rec.get("authors") or {}).get("author") or []
        if isinstance(a, dict):
            a = [a]
        out["authors"] = [normalize_text(x.get("text") if isinstance(x, dict) else x) for x in a]
        out["journal_name"] = normalize_text(rec.get("venue") or "")
        out["journal_abbrev"] = ""
        out["volume"] = normalize_text(rec.get("volume") or "")
        out["issue"] = normalize_text(rec.get("number") or "")
        out["pages"] = normalize_text(rec.get("pages") or "")
        out["doi"] = normalize_text(rec.get("doi") or "")
        out["url"] = normalize_text(rec.get("ee") or "")
        out["year"] = _set_year(rec.get("year"))
        out["month"] = ""
        out["type"] = dblp_type(rec.get("type"))

    elif source == "datacite":
        out["title"] = normalize_text(((rec.get("titles") or [{}])[0] or {}).get("title") or "")
        out["authors"] = [normalize_text(c.get("name") or "") for c in (rec.get("creators") or [])]
        out["journal_name"] = ""
        out["journal_abbrev"] = ""
        out["publisher"] = normalize_text(rec.get("publisher") or "")
        out["doi"] = normalize_text(rec.get("doi") or "")
        out["url"] = normalize_text(rec.get("url") or "")
        out["year"] = _set_year(rec.get("publicationYear"))
        out["month"] = ""
        out["volume"] = ""
        out["issue"] = ""
        out["pages"] = ""
        rtg = ((rec.get("types") or {}).get("resourceTypeGeneral") or "").lower()
        out["type"] = {"dataset": "dataset", "software": "software"}.get(rtg, "")

    elif source == "openlibrary":
        out["title"] = normalize_text(rec.get("title") or "")
        out["authors"] = [normalize_text(a) for a in (rec.get("author_name") or [])]
        out["journal_name"] = ""
        out["journal_abbrev"] = ""
        out["doi"] = ""
        pub = rec.get("publisher")
        out["publisher"] = normalize_text(pub[0] if isinstance(pub, list) and pub else pub or "")
        isbns = rec.get("isbn") or []
        out["isbn"] = normalize_text(isbns[0]) if isbns else ""
        out["year"] = _set_year(rec.get("first_publish_year"))
        out["month"] = ""
        out["volume"] = ""
        out["issue"] = ""
        out["pages"] = ""
        out["type"] = "book"

    elif source in ("ieee", "ieeexplore"):
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

    # DOI forms differ per source (OpenAlex/DataCite return https://doi.org/…
    # URLs) — bare them all HERE so every downstream comparison and every
    # output uses one canonical form.
    out["doi"] = bare_doi(out.get("doi") or "")

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

    # First-author surname disambiguates title searches (critical for common
    # titles like "Deep Learning"). The surname is not reliably the last
    # token — family-first citations ("Xue J.") would yield the hint "J.",
    # poisoning every author-filtered query — so take the longest real name
    # token from the first couple of authors.
    from ..tools.utils import authors_to_list
    author_hint = None
    for a in authors_to_list(ex.get("authors") or [])[:2]:
        tokens = [t.strip(".,") for t in a.split()]
        named = [t for t in tokens if len(t) >= 3 and t.replace("-", "").isalpha()]
        if named:
            author_hint = max(named, key=len)
            break

    rtype = (state.get("type") or "").lower()
    book_like = rtype in ("book", "book chapter") or bool(
        ex.get("publisher") and not ex.get("journal_name")
    )

    cfg = state.get("_cfg")
    source_timeout = getattr(cfg, "source_timeout_s", 8.0) or 8.0

    # Jobs that timed out in an earlier correction round are skipped: they are
    # not cached (the coroutine was cancelled), so re-issuing them would eat
    # the full timeout again in every round.
    timed_out: set = state.get("_timed_out_jobs") or set()

    # (source_name, via, job_key, coroutine) — a single job list keeps the
    # fan-out and the result consumption trivially aligned.
    jobs: List[Tuple[str, str, str, Any]] = []

    def _add(sname: str, via: str, key: str, coro) -> None:
        if key in timed_out:
            coro.close()
            return
        jobs.append((sname, via, key, coro))

    for s in sources:
        sname = getattr(s, "NAME", type(s).__name__).lower()
        if arxiv_id and sname == "arxiv":
            _add(sname, "id", f"{sname}|id|{arxiv_id}", s.by_id(arxiv_id))
        if doi:
            _add(sname, "doi", f"{sname}|doi|{doi}", s.by_doi(doi))
        if title:
            if sname in ("openlibrary", "googlebooks") and not book_like:
                continue  # book catalogs are noise for articles/papers
            for tv in _title_variants(title):
                _add(sname, "title", f"{sname}|title|{tv.lower()}",
                     s.by_title(tv, author=author_hint))
        if sname == "crossref" and state.get("reference") and hasattr(s, "by_biblio"):
            # Citation matching over the raw string survives title typos —
            # it is the recall workhorse, so it gets a longer timeout below.
            # AnalyzeReference prefetched it concurrently with the LLM call;
            # consume that task instead of issuing a duplicate request.
            pre = state.get("_biblio_prefetch")
            if pre is not None and not pre.cancelled():
                state["_biblio_prefetch"] = None  # single use

                async def _consume(task=pre):
                    return await task
                _add(sname, "biblio", f"{sname}|biblio", _consume())
            else:
                _add(sname, "biblio", f"{sname}|biblio",
                     s.by_biblio(state["reference"]))

    # NLM journal-abbreviation check rides along in the same gather instead of
    # blocking the pipeline ahead of it.
    nlm_job = None
    if (rtype in ("journal", "journal article", "conference paper")
            and ex.get("journal_name") and not ex.get("verified_journal_abbrev")):
        from .verify_journal_abbrev import nlm_lookup_abbrev
        nlm_job = nlm_lookup_abbrev(state.get("_http"), state.get("_cache"), ex["journal_name"])

    async def _bounded(coro, timeout):
        # A hung source (arXiv and S2 regularly take 10s+) must not stall the
        # whole gather; the pipeline tolerates a missing source.
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except Exception as e:
            return e

    def _job_timeout(via: str) -> float:
        # Crossref citation matching is slow under load but is the single
        # highest-recall query — worth waiting twice as long for.
        return source_timeout * 2 if via == "biblio" else source_timeout

    all_coros = [_bounded(c, _job_timeout(via)) for _, via, _, c in jobs]
    if nlm_job is not None:
        all_coros.append(_bounded(nlm_job, source_timeout))
    all_results = await asyncio.gather(*all_coros)

    if nlm_job is not None:
        from .verify_journal_abbrev import apply_abbrev_result
        nlm_result = all_results.pop()
        if isinstance(nlm_result, str):
            apply_abbrev_result(state, nlm_result)
    results = all_results

    out_norm: List[Dict[str, Any]] = []
    for (sname, via, key, _), rec in zip(jobs, results):
        if isinstance(rec, Exception) or not rec:
            if isinstance(rec, asyncio.TimeoutError):
                logger.debug("[multisource_lookup] %s timed out after %.1fs (%s)",
                             sname, source_timeout, key)
                timed_out.add(key)
            continue
        for r in (rec if isinstance(rec, list) else [rec]):
            if isinstance(r, dict) and r:
                cand = _normalize_candidate(sname, r)
                cand["_via"] = via
                out_norm.append(cand)

    state["_timed_out_jobs"] = timed_out

    def _via_priority(v: str) -> int:
        return {"id": 3, "doi": 3, "biblio": 2, "title": 1}.get((v or "").lower(), 0)

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

    if logger.isEnabledFor(10):  # DEBUG
        for c in dedup.values():
            logger.debug("[multisource_lookup] %s via=%s year=%s doi=%s",
                         c["source"], c.get("_via"), c.get("year"), c.get("doi"))

    state["candidates"] = list(dedup.values())
    _trace_year(
        "multisource_lookup/summary",
        candidates=[(c["source"], c.get("year"), c.get("_via")) for c in dedup.values()],
    )
    return state
