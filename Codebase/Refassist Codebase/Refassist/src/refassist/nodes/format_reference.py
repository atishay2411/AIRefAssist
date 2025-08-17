from ..state import PipelineState
from ..tools.utils import (
  authors_to_list, format_authors_ieee_list, sentence_case,
  normalize_text, normalize_pages, normalize_month_field,
  MONTHS_NAME, format_doi_link
)

def format_reference(state: PipelineState) -> PipelineState:
    ex = state["extracted"]; rtype = (state["type"] or "other").lower()
    A = authors_to_list(ex.get("authors") or [])
    A_fmt = format_authors_ieee_list(A)
    title_raw = ex.get("title") or ""
    title = sentence_case(title_raw)
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
        if doi_link: parts.append(doi_link)

    elif rtype in ("book chapter","chapter"):
        book_title = (ex.get("book_title") or conf or journal or "").strip()
        if book_title: parts.append(f"in *{book_title}*")
        if pages_norm: parts.append(f"pp. {pages_norm}")
        if pub: parts.append(pub)
        date = " ".join([m for m in [month_disp, year] if m]).strip()
        if date: parts.append(date)
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
