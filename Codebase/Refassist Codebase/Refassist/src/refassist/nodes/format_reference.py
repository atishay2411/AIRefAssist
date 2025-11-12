from ..state import PipelineState
from ..tools.utils import (
  authors_to_list, format_authors_ieee_list,
  normalize_text, normalize_pages, normalize_month_field,
  MONTHS_NAME, format_doi_link
)

def format_reference(state: PipelineState) -> PipelineState:
    ex = state["extracted"]; rtype = (state["type"] or "other").lower()
    A = authors_to_list(ex.get("authors") or [])
    A_fmt = format_authors_ieee_list(A)
    title_raw = ex.get("title") or ""
    title = title_raw
    journal = ex.get("journal_abbrev") or ex.get("journal_name") or ""
    vol = normalize_text(ex.get("volume") or "")
    issue = normalize_text(ex.get("issue") or "")
    pages_raw = normalize_text(ex.get("pages") or "")
    pages_norm, is_eloc = normalize_pages(pages_raw)
    if "-" in pages_norm: pages_norm = pages_norm.replace("-", "–")
    year = normalize_text(ex.get("year") or "")
    month = normalize_month_field(ex.get("month") or "")
    month_disp = MONTHS_NAME.get(month, month) if month else ""
    doi_link = format_doi_link(ex.get("doi") or "")
    conf = normalize_text(ex.get("conference_name") or "")
    loc = normalize_text(ex.get("location") or "")
    pub = normalize_text(ex.get("publisher") or "")
    edition = normalize_text(ex.get("edition") or "")
    isbn = normalize_text(ex.get("isbn") or "")

    parts = []
    if A_fmt: parts.append(A_fmt)
    include_quoted_title = rtype not in ("book",)
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
        venue = conf or journal or "Proceedings"
        if venue: parts.append(f"in *{venue}*")
        if loc: parts.append(loc)
        if pages_norm: parts.append(f"pp. {pages_norm}")
        date = " ".join([m for m in [month_disp, year] if m]).strip()
        if date: parts.append(date)
        if doi_link: parts.append(doi_link)

    elif rtype == "preprint":
        parts.append("preprint")
        if journal and "arxiv" in journal.lower(): parts.append(journal)
        date = " ".join([m for m in [month_disp, year] if m]).strip()
        if date: parts.append(date)
        if doi_link: parts.append(doi_link)

    elif rtype == "book":
        if title: parts.append(f"*{title}*")
        if edition: parts.append(f"{edition} ed.")
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
        editors_fmt = format_authors_ieee_list(editors).replace(" and ", ", ")  # list only, no "and" for editors
        ed_label = "Ed." if len(editors) == 1 else "Eds." if editors else ""
        if book_title: parts.append(f"in *{book_title}*")
        if editors:
            parts.append(f"{editors_fmt}, {ed_label}".strip(", "))
        imprint_bits = [loc, pub] = [(ex.get("location") or "").strip(), (ex.get("publisher") or "").strip()]
        imprint = ": ".join([b for b in imprint_bits if b]) if any(imprint_bits) else ""
        if imprint: parts.append(imprint)
        if year: parts.append(year)
        if pages_norm: parts.append(f"pp. {pages_norm}")
        if doi_link: parts.append(doi_link)


    else:
        venue = journal or conf or pub
        if venue: parts.append(venue)
        date = " ".join([m for m in [month_disp, year] if m]).strip()
        if date: parts.append(date)
        if vol: parts.append(f"vol. {vol}")
        if issue: parts.append(f"no. {issue}")
        if pages_norm: parts.append(f"pp. {pages_norm}")
        if doi_link: parts.append(doi_link)

    state["formatted"] = (", ".join([p for p in parts if p]) + ".").replace(" ,", ",")
    return state



