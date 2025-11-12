from ..state import PipelineState
from ..tools.utils import authors_to_list, safe_str, format_doi_link, normalize_month_field


# ------------------------------
# CSL JSON Export
# ------------------------------
def _to_csl_json(ex, rtype):
    typemap = {
        "journal article": "article-journal",
        "conference paper": "paper-conference",
        "book": "book",
        "book chapter": "chapter",
        "thesis": "thesis",
        "technical report": "report",
        "dataset": "dataset",
        "standard": "standard",
        "software": "software",
        "preprint": "article",
    }
    t = typemap.get(rtype, "article")

    # --- Authors ---
    authors = []
    for a in authors_to_list(ex.get("authors")):
        parts = a.split()
        family = parts[-1] if parts else a
        given = " ".join(parts[:-1]) if len(parts) > 1 else ""
        authors.append({"family": safe_str(family), "given": safe_str(given)})

    # --- Editors ---
    editors = []
    for e in authors_to_list(ex.get("editors") or []):
        parts = e.split()
        family = parts[-1] if parts else e
        given = " ".join(parts[:-1]) if len(parts) > 1 else ""
        editors.append({"family": safe_str(family), "given": safe_str(given)})

    # --- Issued date ---
    year_raw = ex.get("year")
    month_raw = normalize_month_field(ex.get("month") or "")
    issued = None
    try:
        y = int(year_raw) if safe_str(year_raw).isdigit() else None
        if y is not None:
            issued = {"date-parts": [[y, int(month_raw)]]} if (month_raw and month_raw.isdigit()) else {"date-parts": [[y]]}
    except Exception:
        issued = None

    doi_link = format_doi_link(ex.get("doi") or "")

    # --- Container logic ---
    container = safe_str(
        ex.get("journal_name")
        or ex.get("conference_name")
        or ex.get("book_title")
        or ""
    )

    # --- Build CSL JSON ---
    csl = {
        "type": t,
        "title": safe_str(ex.get("title")),
        "author": authors if authors else None,
        "editor": editors if editors else None,
        "container-title": container or None,
        "container-title-short": safe_str(ex.get("journal_abbrev")) or None,
        "volume": safe_str(ex.get("volume")),
        "issue": safe_str(ex.get("issue")),
        "page": safe_str(ex.get("pages")),
        "DOI": safe_str(ex.get("doi")),
        "URL": doi_link or safe_str(ex.get("url")),
        "publisher": safe_str(ex.get("publisher")),
        "publisher-place": safe_str(ex.get("location")),
        "issued": issued,
        "ISBN": safe_str(ex.get("isbn")),
    }

    return {k: v for k, v in csl.items() if v not in ("", None, [], {})}


# ------------------------------
# BibTeX Export
# ------------------------------
def _to_bibtex(ex, rtype):
    import re, hashlib

    def esc(s: str) -> str:
        return (
            s.replace("\\", "\\textbackslash{}")
            .replace("{", "\\{")
            .replace("}", "\\}")
            .replace("&", "\\&")
            .replace("%", "\\%")
            .replace("$", "\\$")
            .replace("#", "\\#")
            .replace("_", "\\_")
        )

    authors_list = authors_to_list(ex.get("authors"))
    editors_list = authors_to_list(ex.get("editors") or [])
    first_author_last = ""

    if authors_list:
        parts = authors_list[0].split()
        first_author_last = parts[-1] if parts else authors_list[0]

    year_str = safe_str(ex.get("year"))
    fa_key = re.sub(r"[^A-Za-z0-9]+", "", safe_str(first_author_last)) or "ref"
    yr_key = re.sub(r"[^0-9]+", "", year_str)
    if not yr_key:
        basis = safe_str(ex.get("doi")) or safe_str(ex.get("title"))
        h = hashlib.sha1(basis.encode("utf-8", "ignore")).hexdigest()[:6] if basis else "000000"
        yr_key = h
    key = f"{fa_key}{yr_key}"

    entry_type = {
        "journal article": "article",
        "conference paper": "inproceedings",
        "book": "book",
        "book chapter": "incollection",
        "thesis": "phdthesis",
        "technical report": "techreport",
        "dataset": "misc",
        "standard": "misc",
        "software": "misc",
        "preprint": "misc",
    }.get(rtype, "misc")

    A = " and ".join(authors_list)
    E = " and ".join(editors_list)
    title = safe_str(ex.get("title"))
    journal = safe_str(ex.get("journal_name"))
    conf = safe_str(ex.get("conference_name"))
    book_title = safe_str(ex.get("book_title"))
    volume = safe_str(ex.get("volume"))
    number = safe_str(ex.get("issue"))
    pages = safe_str(ex.get("pages"))
    year = safe_str(ex.get("year"))
    doi = safe_str(ex.get("doi"))
    publisher = safe_str(ex.get("publisher"))
    location = safe_str(ex.get("location"))
    isbn = safe_str(ex.get("isbn"))
    url_or_doi = format_doi_link(doi) if doi else safe_str(ex.get("url"))

    fields = []

    # --- Type-specific field mappings ---
    if entry_type == "article":
        fields += [
            ("author", A),
            ("title", title),
            ("journal", journal),
            ("volume", volume),
            ("number", number),
            ("pages", pages),
            ("year", year),
        ]
        if url_or_doi:
            fields.append(("url", url_or_doi))
        if doi and not url_or_doi:
            fields.append(("doi", doi))

    elif entry_type == "inproceedings":
        fields += [
            ("author", A),
            ("title", title),
            ("booktitle", conf or journal),
            ("pages", pages),
            ("year", year),
        ]
        if url_or_doi:
            fields.append(("url", url_or_doi))

    elif entry_type == "book":
        fields += [
            ("author", A),
            ("title", title),
            ("publisher", publisher),
            ("address", location),
            ("year", year),
            ("isbn", isbn),
        ]
        if url_or_doi:
            fields.append(("url", url_or_doi))

    elif entry_type == "incollection":
        # ✅ Correct handling for book chapters
        fields += [
            ("author", A),
            ("title", title),
            ("booktitle", book_title or conf or journal),
            ("editor", E),
            ("publisher", publisher),
            ("address", location),
            ("pages", pages),
            ("year", year),
        ]
        if url_or_doi:
            fields.append(("url", url_or_doi))
        if doi and not url_or_doi:
            fields.append(("doi", doi))

    elif entry_type == "phdthesis":
        fields += [
            ("author", A),
            ("title", title),
            ("school", publisher or conf or journal),
            ("year", year),
        ]
        if url_or_doi:
            fields.append(("url", url_or_doi))

    elif entry_type == "techreport":
        fields += [
            ("author", A),
            ("title", title),
            ("institution", publisher or conf or journal),
            ("year", year),
        ]
        if url_or_doi:
            fields.append(("url", url_or_doi))

    else:
        fields += [
            ("author", A),
            ("title", title),
            ("howpublished", conf or journal or publisher),
            ("year", year),
        ]
        if url_or_doi:
            fields.append(("url", url_or_doi))

    # Escape and serialize
    fields = [(k, esc(v)) for k, v in fields if v]
    body = ",\n  ".join([f"{k} = {{{v}}}" for k, v in fields])
    return f"@{entry_type}{{{key},\n  {body}\n}}"


# ------------------------------
# Entry point
# ------------------------------
def build_exports(state: PipelineState) -> PipelineState:
    ex = state["extracted"]
    rtype = (state["type"] or "other").lower()
    state["csl_json"] = _to_csl_json(ex, rtype)
    state["bibtex"] = _to_bibtex(ex, rtype)
    return state
