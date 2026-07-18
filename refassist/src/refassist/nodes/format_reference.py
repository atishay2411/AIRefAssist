import re

from ..state import PipelineState
from ..tools.utils import (
  authors_to_list, format_authors_ieee_list,
  normalize_text, normalize_pages, normalize_month_field,
  MONTHS_NAME, format_doi_ieee
)

# "IEEE Std 754-2019", "ISO/IEC 27001:2013", "ANSI C63.4-2014", ...
_STD_NO_RE = re.compile(
    r"\b((?:IEEE|ISO(?:/IEC)?|IEC|ANSI(?:/IEEE)?|ITU(?:-[RT])?|NIST|ETSI|ASTM|BS|EN|DIN)"
    r"[\s/]*(?:Std\.?|Standard)?\s*[A-Z]?\d[\w.\-/]*(?::\d{4})?)", re.I)

def format_reference(state: PipelineState) -> PipelineState:
    ex = state["extracted"]; rtype = (state["type"] or "other").lower()

    # Heuristic extraction (no LLM) is lossy — journal names and unquoted
    # book titles are never extracted. Reconstructing an UNVERIFIED reference
    # from those fragments destroys information ("A. C. Yunnus, Heat
    # Transfer..." once became just "2002."). Preserve the input instead.
    heuristic_mode = getattr(state.get("_llm"), "provider", "dummy") == "dummy"
    unverified = not state.get("best") or (state.get("best") or {}).get("source") == "extracted"
    if heuristic_mode and unverified:
        state["formatted"] = normalize_text(state.get("reference", ""))
        state["_formatter"] = "Original text preserved (unverified; heuristic extraction is lossy)"
        return state

    # If verification exited before the correction loop ran, extracted can
    # still be missing fields the matched record has (a "verified" reference
    # once formatted without its journal name). Fill gaps from best for
    # display — identity fields (title/authors) only ever change via
    # corrections.
    best = state.get("best") or {}
    if best and best.get("source") != "extracted":
        ex = dict(ex)
        for k in ("journal_name", "journal_abbrev", "conference_name", "book_title",
                  "volume", "issue", "pages", "year", "month", "doi",
                  "publisher", "location", "edition", "isbn", "url"):
            if not ex.get(k) and best.get(k):
                ex[k] = best[k]
    A = authors_to_list(ex.get("authors") or [])
    A_fmt = format_authors_ieee_list(A)
    title_raw = ex.get("title") or ""
    title = title_raw
    # IEEE style abbreviates journal names — prefer the NLM-verified
    # abbreviation, then whatever abbreviation sources supplied, then the
    # full name.
    journal = (ex.get("verified_journal_abbrev") or ex.get("journal_abbrev")
               or ex.get("journal_name") or "")
    vol = normalize_text(ex.get("volume") or "")
    issue = normalize_text(ex.get("issue") or "")
    pages_raw = normalize_text(ex.get("pages") or "")
    pages_norm, is_eloc = normalize_pages(pages_raw)
    if "-" in pages_norm: pages_norm = pages_norm.replace("-", "–")
    year = normalize_text(ex.get("year") or "")
    month = normalize_month_field(ex.get("month") or "")
    month_disp = MONTHS_NAME.get(month, month) if month else ""
    doi_link = format_doi_ieee(ex.get("doi") or "")
    conf = normalize_text(ex.get("conference_name") or "")
    loc = normalize_text(ex.get("location") or "")
    pub = normalize_text(ex.get("publisher") or "")
    edition = normalize_text(ex.get("edition") or "")
    isbn = normalize_text(ex.get("isbn") or "")
    url = normalize_text(ex.get("url") or "")

    parts = []
    if A_fmt: parts.append(A_fmt)
    # Books and standards italicize the title instead of quoting it
    include_quoted_title = rtype not in ("book", "standard")
    if include_quoted_title and title: parts.append(f"\"{title}\"")

    if rtype in ("journal article","journal"):
        if journal: parts.append(f"*{journal}*")
        if vol: parts.append(f"vol. {vol}")
        if issue: parts.append(f"no. {issue}")
        if pages_norm: parts.append(f"Art. no. {pages_norm}" if is_eloc else f"pp. {pages_norm}")
        date = " ".join([m for m in [month_disp, year] if m]).strip()
        if date: parts.append(date)
        if doi_link: parts.append(doi_link)

    elif rtype == "conference paper":
        # IEEE order: in *Venue*, City, vol. V, Year, pp. X–Y, doi: …
        venue = conf or journal or "Proceedings"
        if venue: parts.append(f"in *{venue}*")
        if loc: parts.append(loc)
        if vol: parts.append(f"vol. {vol}")
        date = " ".join([m for m in [month_disp, year] if m]).strip()
        if date: parts.append(date)
        if pages_norm: parts.append(f"pp. {pages_norm}")
        if doi_link: parts.append(doi_link)

    elif rtype == "preprint":
        # IEEE: A. Author, "Title," arXiv:xxxx.xxxxx, Year.
        aid = normalize_text(ex.get("arxiv_id") or "")
        if aid:
            parts.append(f"arXiv:{aid}")
        elif journal and "arxiv" in journal.lower():
            parts.append(journal)
        else:
            parts.append("preprint")
        date = " ".join([m for m in [month_disp, year] if m]).strip()
        if date: parts.append(date)
        if doi_link: parts.append(doi_link)

    elif rtype == "book":
        # Bookseller-flavored records append "(4th Edition)" to titles and
        # supply edition values already suffixed "4th ed." — dedupe both.
        if title:
            title = re.sub(r"\s*\(\d+(?:st|nd|rd|th)?\s+ed(?:ition)?\.?\)\s*$",
                           "", title, flags=re.I)
            parts.append(f"*{title}*")
        if edition:
            ed_val = re.sub(r"\s*\bed(?:ition)?\.?\s*$", "", edition, flags=re.I).strip()
            if ed_val: parts.append(f"{ed_val} ed.")
        imprint = f"{loc}: {pub}" if (loc and pub) else (loc or pub)
        if imprint: parts.append(imprint)
        if year: parts.append(year)
        if isbn: parts.append(f"ISBN: {isbn}")
        # Drop chapter-like pages on books
        if not re.search(r'\d+\s*[-–—]\s*\d+', ex.get("pages") or "") and doi_link:
            parts.append(doi_link)


    elif rtype in ("book chapter","chapter"):
        book_title = (ex.get("book_title") or conf or journal or "").strip()
        editors = authors_to_list(ex.get("editors") or [])
        from ..tools.utils import format_author_ieee
        editors_fmt = ", ".join(format_author_ieee(e) for e in editors if e)
        ed_label = "Ed." if len(editors) == 1 else "Eds." if editors else ""
        if book_title: parts.append(f"in *{book_title}*")
        if editors:
            parts.append(f"{editors_fmt}, {ed_label}".strip(", "))
        imprint_bits = [(ex.get("location") or "").strip(), (ex.get("publisher") or "").strip()]
        imprint = ": ".join([b for b in imprint_bits if b]) if any(imprint_bits) else ""
        if imprint: parts.append(imprint)
        if year: parts.append(year)
        if pages_norm: parts.append(f"pp. {pages_norm}")
        if doi_link: parts.append(doi_link)


    elif rtype == "thesis":
        # IEEE: A. Author, "Title," M.S. thesis / Ph.D. dissertation, Univ., City, Year.
        raw_ref = (state.get("reference") or "").lower()
        degree = "M.S. thesis" if re.search(r"\bm\.?s\.?\b|master", raw_ref) else "Ph.D. dissertation"
        parts.append(degree)
        # The school hides in different extracted fields ("Dept. …, Univ. of
        # Toronto" may land in publisher, book_title, or location)
        school = pub or normalize_text(ex.get("book_title") or "") or journal or conf
        if school: parts.append(school)
        if loc and loc != school: parts.append(loc)
        if year: parts.append(year)
        if url and not doi_link: parts.append(f"[Online]. Available: {url}")
        if doi_link: parts.append(doi_link)

    elif rtype == "technical report":
        # IEEE: A. Author, "Title," Institution, City, Rep., Year.
        inst = pub or conf or journal
        if inst: parts.append(inst)
        if loc: parts.append(loc)
        parts.append("Tech. Rep.")
        if year: parts.append(year)
        if url and not doi_link: parts.append(f"[Online]. Available: {url}")
        if doi_link: parts.append(doi_link)

    elif rtype == "standard":
        # IEEE: *Title of Standard*, Standard number, Year.
        raw_ref = state.get("reference") or ""
        m_std = _STD_NO_RE.search(raw_ref)
        std_no = normalize_text(m_std.group(1)) if m_std else ""
        # Extraction regularly loses standards titles (they are unquoted);
        # the text before the standard number is the title in practice.
        if not title and m_std:
            pre = raw_ref[:m_std.start()].strip(" ,.;:")
            pre = re.sub(r"[,;]?\s*in$", "", pre, flags=re.I).strip(" ,.;:")
            if len(pre) >= 8:
                title = pre
        if title: parts.append(f"*{title}*")
        if std_no and std_no.lower() not in (title or "").lower():
            parts.append(std_no)
        if pub and not std_no: parts.append(pub)
        if year: parts.append(year)
        if url and not doi_link: parts.append(f"[Online]. Available: {url}")
        if doi_link: parts.append(doi_link)

    elif rtype in ("software", "dataset"):
        kind = "Software" if rtype == "software" else "Dataset"
        maker = pub or journal or conf
        if maker: parts.append(maker)
        parts.append(kind)
        if year: parts.append(year)
        if url and not doi_link: parts.append(f"[Online]. Available: {url}")
        if doi_link: parts.append(doi_link)

    else:
        venue = journal or conf or pub
        if venue: parts.append(venue)
        date = " ".join([m for m in [month_disp, year] if m]).strip()
        if date: parts.append(date)
        if vol: parts.append(f"vol. {vol}")
        if issue: parts.append(f"no. {issue}")
        if pages_norm: parts.append(f"pp. {pages_norm}")
        if url and not doi_link: parts.append(f"[Online]. Available: {url}")
        if doi_link: parts.append(doi_link)

    informative = [p for p in parts if p]
    # Degenerate-output guard: a "formatted" reference that kept fewer than
    # two fields has destroyed information (an IEEE standard once collapsed
    # to the literal string "2019."). The input is always more useful.
    if len(informative) < 2:
        state["formatted"] = normalize_text(state.get("reference", ""))
        state["_formatter"] = ("Original text preserved (extraction kept too "
                               "few fields to format safely)")
        return state

    out = (", ".join(informative) + ".").replace(" ,", ",")
    # IEEE punctuation: the comma after a quoted title sits INSIDE the
    # closing quote — "Deep learning," Nature — not outside it.
    out = out.replace('", ', '," ')
    state["formatted"] = out
    state["_formatter"] = "Rule-based IEEE formatter (deterministic)"
    return state



