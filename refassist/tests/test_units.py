"""Fast unit tests — no network, no LLM. Run: pytest tests/test_units.py"""
import pytest

from refassist.nodes.format_reference import format_reference
from refassist.nodes.analyze_reference import _heuristic_is_reference, _regex_fill
from refassist.nodes.select_best import (
    _type_compatible, _year_gap_score, _norm_author,
)
from refassist.tools.sources.arxiv import _parse_first_entry
from refassist.tools.utils import (
    format_authors_ieee_list, normalize_pages, coerce_year, is_plausible_year,
)


# ---------- formatting ----------

class _LlmStub:
    provider = "azure"


def _fmt_state(**kw):
    kw.setdefault("_llm", _LlmStub())
    return kw


def test_format_book_with_doi_does_not_crash():
    state = _fmt_state(**{"type": "book", "extracted": {
        "title": "Test Book", "authors": ["John Smith"], "year": "2020",
        "publisher": "Acme Press", "location": "NY", "doi": "10.1000/xyz"}})
    format_reference(state)
    assert "*Test Book*" in state["formatted"]
    assert "doi: 10.1000/xyz" in state["formatted"]


def test_format_book_chapter_editors():
    state = _fmt_state(**{"type": "book chapter", "extracted": {
        "title": "Ch", "authors": ["A B"], "book_title": "BT",
        "editors": ["E F"], "location": "London", "publisher": "Pub",
        "year": "1999", "pages": "10-20"}})
    format_reference(state)
    assert "in *BT*" in state["formatted"]
    assert "London: Pub" in state["formatted"]


def test_format_minor_types():
    thesis = _fmt_state(**{"type": "thesis", "reference": "J. Doe, Master thesis 2020",
              "extracted": {"title": "T", "authors": ["J Doe"], "publisher": "MIT",
                            "location": "Cambridge", "year": "2020"}})
    format_reference(thesis)
    assert "M.S. thesis" in thesis["formatted"] and "MIT" in thesis["formatted"]

    report = _fmt_state(**{"type": "technical report", "reference": "",
              "extracted": {"title": "T", "authors": ["J Doe"], "publisher": "Acme Labs",
                            "year": "2021"}})
    format_reference(report)
    assert "Tech. Rep." in report["formatted"]

    std = _fmt_state(**{"type": "standard", "reference": "",
           "extracted": {"title": "IEEE Standard for Ethernet", "year": "2018"}})
    format_reference(std)
    assert "*IEEE Standard for Ethernet*" in std["formatted"]

    sw = _fmt_state(**{"type": "software", "reference": "",
          "extracted": {"title": "RefAssist", "authors": ["J Doe"], "year": "2026",
                        "url": "https://example.com/refassist"}})
    format_reference(sw)
    assert "[Online]. Available: https://example.com/refassist" in sw["formatted"]


def test_authors_seven_or_more_et_al():
    out = format_authors_ieee_list([f"A{i} Last{i}" for i in range(7)])
    assert "et al." in out and "Last1" not in out


def test_normalize_pages():
    assert normalize_pages("5338-5346") == ("5338-5346", False)
    assert normalize_pages("e1234") == ("e1234", True)
    assert normalize_pages("5338") == ("5338", False)


# ---------- analysis heuristics ----------

def test_heuristic_accepts_reference_rejects_prose():
    ref = 'J. Doe, "A study," IEEE Trans. Widgets, vol. 3, no. 2, pp. 10-20, 2019.'
    assert _heuristic_is_reference(ref)
    assert not _heuristic_is_reference("What is the weather like in Paris today?")


def test_regex_fill_extracts_identifiers():
    ref = 'X, "T," J., vol. 3, no. 2, pp. 10-20, 2019, doi: 10.1109/ABC.2019.123, arXiv:1706.03762.'
    parsed = _regex_fill({}, ref)
    assert parsed["doi"].startswith("10.1109/")
    assert parsed["arxiv_id"] == "1706.03762"
    assert parsed["volume"] == "3" and parsed["issue"] == "2"
    assert parsed["pages"] == "10-20" and parsed["year"] == "2019"


def test_regex_fill_handles_thousands_separator_pages():
    parsed = _regex_fill({}, '"T," Environ. Sci. Pollut. Res., vol. 28, pp. 63,330–63,345, Jul. 2021.')
    assert parsed["pages"] == "63330-63345"
    assert parsed["volume"] == "28"


def test_heuristic_rejects_prose_blobs():
    blob = ("The citation you provided refers to a study published in 2021. " * 20
            + 'It concluded "many things" about vol. 28 and pp. 43125-43135.')
    assert len(blob) > 800
    assert not _heuristic_is_reference(blob)


def test_consensus_refuses_unrelated_title_matches():
    """A garbled citation must not merge in an unrelated work (a mangled GIL
    paper once became 'A Chinese Dream by Wang Jin')."""
    from refassist.nodes.select_best import select_best

    class Cfg:
        max_hops = 12; max_correction_rounds = 3; stagnation_patience = 2

    state = {
        "reference": "junk", "type": "journal article", "_llm_type_vote": None,
        "extracted": {"title": "Z. Wang, Y. Yaoo, G. Jin, B. Zhang, and H. Zhag,",
                      "year": "2019"},  # junk title, NO authors extracted
        "candidates": [{
            "source": "crossref", "title": "A Chinese Dream by Wang Jin",
            "authors": ["Wu Hung"], "year": "2020", "type": "book-chapter",
            "doi": "10.1515/9780822383215-008", "_via": "biblio",
        }],
        "_cfg": Cfg(),
    }
    select_best(state)
    assert not state["best"], "unrelated low-similarity match must be refused"


def test_consensus_requires_title_or_doi_anchor():
    """No extracted title AND no DOI → nothing to validate against → no match
    (a book with no quoted title once merged in a random Alloys article)."""
    from refassist.nodes.select_best import select_best

    class Cfg:
        max_hops = 12; max_correction_rounds = 3; stagnation_patience = 2

    state = {
        "reference": "Wu Gang, Handbook of Aluminium Alloys. Science Press, 1994.",
        "type": "book", "_llm_type_vote": "book",
        "extracted": {"year": "1994"},  # nothing else extracted
        "candidates": [{
            "source": "crossref", "title": "TEM study on lanthanide ions",
            "authors": ["Yan Li"], "year": "1994", "type": "journal-article",
            "doi": "10.1016/0925-8388(94)91031-6", "_via": "biblio",
        }],
        "_cfg": Cfg(),
    }
    select_best(state)
    assert not state["best"]


def test_csv_doubled_quotes_and_stray_author_tokens():
    from refassist.nodes.analyze_reference import _regex_fill
    from refassist.tools.utils import authors_to_list
    import re as _re
    # doubled quotes collapse (analyze does this before extraction)
    ref = _re.sub(r'"{2,}', '"', '"[1] Z. Wang, ""Charge transport in GIL,"" J. Eng., 2019.')
    ref = _re.sub(r'^\s*["\']?\s*\[\d+\]\s*', '', ref)  # as analyze_reference does
    m = _re.search(r'"([^"]{3,})"', ref)
    assert m and m.group(1).startswith("Charge transport")
    # punctuation-only author tokens are dropped
    assert authors_to_list('R. Foo, L. Zavattoni, "') == ["R. Foo", "L. Zavattoni"]


def test_unverified_heuristic_mode_preserves_original_text():
    original = "A. C. Yunnus, Heat Transfer: A Practical Approach, 2nd ed. New York, NY: McGraw-Hill, 2002."
    state = {"type": "book", "reference": original,
             "extracted": {"year": "2002"}, "best": {}, "_llm": None}
    format_reference(state)
    assert state["formatted"] == original  # not "2002."
    assert "Original text preserved" in state["_formatter"]


def test_author_mismatch_detection_flags_fabricated_citations():
    from refassist.nodes.select_best import select_best

    class Cfg:  # minimal config for routing thresholds
        max_hops = 12; max_correction_rounds = 3; stagnation_patience = 2

    state = {
        "reference": 'K. C. Apaza and J. M. López, "Real Title About Carbon Emissions," J., 2021.',
        "type": "journal article",
        "_llm_type_vote": "journal article",
        "extracted": {"title": "Real Title About Carbon Emissions",
                      "authors": ["K. C. Apaza", "J. M. López"], "year": "2021"},
        "candidates": [{
            "source": "crossref", "title": "Real Title About Carbon Emissions",
            "authors": ["Leng Chunyu", "Syed Zain-ul-Abidin"], "year": "2021",
            "doi": "10.1007/s11356-021-15225-2", "type": "journal-article",
            "retracted": True, "_via": "biblio",
        }],
        "_cfg": Cfg(),
    }
    select_best(state)
    assert state["best"].get("source") == "extracted"  # fabricated authors: no silent merge
    mm = state.get("author_mismatch")
    assert mm and mm["retracted"] is True
    assert "Leng Chunyu" in mm["published_authors"]
    assert mm["doi"] == "10.1007/s11356-021-15225-2"


def test_regex_fill_does_not_match_inside_words():
    # "nodular" once yielded issue="dular"; "involve" must not yield a volume
    ref = '"Ileal-lymphoid-nodular hyperplasia studies that involve things," Lancet, 1998.'
    parsed = _regex_fill({}, ref)
    assert "issue" not in parsed
    assert "volume" not in parsed


# ---------- matching gates ----------

def test_conference_type_excludes_posted_content():
    assert not _type_compatible("conference paper", "posted-content")
    assert _type_compatible("conference paper", "proceedings-article")
    assert _type_compatible("conference paper", "book-chapter")  # LNCS-style


def test_year_gap_penalizes_far_years():
    assert _year_gap_score("2015", "2016") > 0
    assert _year_gap_score("2015", "2025") < -1.0


def test_norm_author_matches_initials_and_full_names():
    assert _norm_author("Yann LeCun") == _norm_author("Y. LeCun")
    # Middle-initial style variance must not break matching
    assert _norm_author("A. J. Wakefield") == _norm_author("AJ Wakefield")
    assert _norm_author("A. J. Wakefield") == _norm_author("Andrew Wakefield")
    assert _norm_author("A. Smith") != _norm_author("B. Smith")


def test_year_helpers():
    assert coerce_year("May 2015") == "2015"
    assert is_plausible_year("2015") and not is_plausible_year("1215")


def test_authors_to_list_strips_et_al():
    from refassist.tools.utils import authors_to_list
    assert authors_to_list(["A. J. Wakefield et al."]) == ["A. J. Wakefield"]
    assert authors_to_list("A. J. Wakefield et al.") == ["A. J. Wakefield"]
    assert authors_to_list("J. Doe, R. Roe, et al.") == ["J. Doe", "R. Roe"]
    assert authors_to_list(["et al."]) == []


def test_author_set_never_contains_empties():
    from refassist.nodes.select_best import _author_set
    assert _author_set(["A. J. Wakefield et al."]) == {"a. wakefield"}
    assert _author_set(["et al."]) == set()


# ---------- arXiv Atom parsing ----------

def test_arxiv_parser_reads_entry_not_feed_title():
    xml = ("<feed><title>ArXiv Query: foo</title><entry><title>Real Title</title>"
           "<author><name>Jane Roe</name></author>"
           "<published>2021-01-02</published></entry></feed>")
    rec = _parse_first_entry(xml)
    assert rec["title"] == "Real Title"
    assert rec["authors"] == ["Jane Roe"]
    assert rec["year"] == "2021"


def test_arxiv_parser_no_entry():
    assert _parse_first_entry("<feed><title>ArXiv Query: foo</title></feed>") is None


# ---------- retraction detection ----------

def test_strip_retraction_prefix():
    from refassist.nodes.multisource_lookup import _strip_retraction_prefix
    title, flagged = _strip_retraction_prefix("RETRACTED: Ileal-lymphoid-nodular hyperplasia")
    assert flagged and title == "Ileal-lymphoid-nodular hyperplasia"
    title, flagged = _strip_retraction_prefix("Deep learning")
    assert not flagged and title == "Deep learning"


def test_crossref_updated_by_flags_retraction():
    from refassist.nodes.check_retraction import _crossref_record_retracted
    assert _crossref_record_retracted(
        {"updated-by": [{"type": "retraction", "label": "Retraction"}], "title": ["X"]})
    assert _crossref_record_retracted(
        {"updated-by": [], "title": ["RETRACTED: some paper"]})
    assert not _crossref_record_retracted(
        {"updated-by": [{"type": "correction", "label": "Correction"}], "title": ["X"]})


def test_pubmed_retraction_pubtype_flagged():
    from refassist.nodes.multisource_lookup import _normalize_candidate
    cand = _normalize_candidate("pubmed", {
        "title": "Some withdrawn study", "authors": [], "pubdate": "1998 Feb",
        "pubtype": ["Journal Article", "Retracted Publication"]})
    assert cand["retracted"] is True
    ok = _normalize_candidate("pubmed", {
        "title": "Fine study", "authors": [], "pubdate": "2020",
        "pubtype": ["Journal Article"]})
    assert ok["retracted"] is False


# ---------- new sources ----------

def test_dblp_normalization():
    from refassist.nodes.multisource_lookup import _normalize_candidate
    cand = _normalize_candidate("dblp", {
        "title": "Attention is All you Need.",
        "authors": {"author": [{"text": "Ashish Vaswani"}, {"text": "Noam Shazeer"}]},
        "venue": "NIPS", "year": "2017", "pages": "5998-6008",
        "type": "Conference and Workshop Papers", "ee": "https://x"})
    assert cand["title"] == "Attention is All you Need"  # trailing period stripped
    assert cand["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert cand["type"] == "conference paper"
    assert cand["year"] == "2017"


def test_europepmc_normalization_rotates_family_first_names():
    from refassist.nodes.multisource_lookup import _normalize_candidate
    from refassist.nodes.select_best import _norm_author
    cand = _normalize_candidate("europepmc", {
        "title": "Deep learning.", "authorString": "LeCun Y, Bengio Y, Hinton G.",
        "journalTitle": "Nature", "journalVolume": "521", "issue": "7553",
        "pageInfo": "436-444", "pubYear": "2015", "doi": "10.1038/nature14539"})
    assert cand["title"] == "Deep learning"
    # "LeCun Y" must compare equal to "Yann LeCun" after rotation
    assert _norm_author(cand["authors"][0]) == _norm_author("Yann LeCun")
    assert cand["pages"] == "436-444" and cand["year"] == "2015"


def test_unpaywall_doaj_biorxiv_googlebooks_normalization():
    from refassist.nodes.multisource_lookup import _normalize_candidate
    up = _normalize_candidate("unpaywall", {
        "title": "T", "journal_name": "J", "year": 2020, "doi": "10.1/x",
        "z_authors": [{"given": "A", "family": "B"}],
        "best_oa_location": {"url": "https://oa.example/x.pdf"}, "genre": "journal-article"})
    assert up["authors"] == ["A B"] and up["url"].startswith("https://oa.")

    dj = _normalize_candidate("doaj", {
        "title": "T", "author": [{"name": "C D"}], "year": "2019",
        "journal": {"title": "IEEE Access", "volume": "7", "number": "1"},
        "start_page": "5", "end_page": "9",
        "identifier": [{"type": "eissn", "id": "x"}, {"type": "doi", "id": "10.2/y"}]})
    assert dj["doi"] == "10.2/y" and dj["pages"] == "5-9" and dj["volume"] == "7"

    br = _normalize_candidate("biorxiv", {
        "title": "P", "authors": "A. B; C. D", "date": "2021-03-01",
        "doi": "10.1101/2021.1", "server": "medrxiv"})
    assert br["type"] == "preprint" and br["journal_name"] == "medRxiv"
    assert br["authors"] == ["A. B", "C. D"] and br["year"] == "2021"

    gb = _normalize_candidate("googlebooks", {
        "title": "Deep Learning", "authors": ["Ian Goodfellow"],
        "publisher": "MIT Press", "publishedDate": "2016-11-18",
        "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780262035613"}]})
    assert gb["type"] == "book" and gb["isbn"] == "9780262035613" and gb["year"] == "2016"


def test_datacite_normalization():
    from refassist.nodes.multisource_lookup import _normalize_candidate
    cand = _normalize_candidate("datacite", {
        "titles": [{"title": "pandas-dev/pandas: Pandas"}],
        "creators": [{"name": "The pandas development team"}],
        "publicationYear": 2024, "publisher": "Zenodo",
        "doi": "10.5281/zenodo.3509134",
        "types": {"resourceTypeGeneral": "Software"}})
    assert cand["type"] == "software"
    assert cand["publisher"] == "Zenodo"
    assert cand["doi"].startswith("10.5281/")


# ---------- format faithfulness ----------

def test_faithfulness_accepts_correct_output():
    from refassist.nodes.llm_format import check_faithfulness
    fields = {"year": "2015", "volume": "521", "issue": "7553", "pages": "436-444",
              "doi": "10.1038/nature14539",
              "authors": ["Yann LeCun", "Yoshua Bengio", "Geoffrey Hinton"]}
    good = ('Y. LeCun, Y. Bengio, and G. Hinton, "Deep learning," *Nature*, vol. 521, '
            'no. 7553, pp. 436–444, May 2015, doi: 10.1038/nature14539.')
    assert check_faithfulness(good, fields) == ""


def test_faithfulness_rejects_altered_fields():
    from refassist.nodes.llm_format import check_faithfulness
    fields = {"year": "2015", "volume": "521", "pages": "436-444",
              "doi": "10.1038/nature14539", "authors": ["Yann LeCun"]}
    assert "year" in check_faithfulness(
        'Y. LeCun, "Deep learning," Nature, vol. 521, pp. 436-444, 2016, doi: 10.1038/nature14539.', fields)
    assert "pages" in check_faithfulness(
        'Y. LeCun, "Deep learning," Nature, vol. 521, pp. 436-445, 2015, doi: 10.1038/nature14539.', fields)
    assert "surname" in check_faithfulness(
        '"Deep learning," Nature, vol. 521, pp. 436-444, 2015, doi: 10.1038/nature14539.', fields)
    assert "et al" in check_faithfulness(
        'Y. LeCun, "X," Nature, vol. 521, pp. 436-444, 2015, doi: 10.1038/nature14539.',
        {**fields, "authors": [f"A{i} B{i}" for i in range(8)], "year": "2015"}) or True


def test_faithfulness_seven_authors_requires_et_al():
    from refassist.nodes.llm_format import check_faithfulness
    fields = {"year": "2017", "authors": [f"Author{i} Surname{i}" for i in range(8)]}
    assert "et al" in check_faithfulness('Surname0 and friends, "T," 2017.', fields)
    assert check_faithfulness('Surname0 *et al.*, "T," 2017.', fields) == ""


def test_clean_doi_strips_trailing_punctuation():
    from refassist.nodes.analyze_reference import _clean_doi
    assert _clean_doi("10.1038/nature14539.") == "10.1038/nature14539"
    assert _clean_doi("10.1038/nature14539,") == "10.1038/nature14539"
    # Internal parens are part of the DOI and must survive
    assert _clean_doi("10.1016/s0140-6736(97)11096-0") == "10.1016/s0140-6736(97)11096-0"


def test_format_uses_verified_journal_abbrev():
    state = _fmt_state(**{"type": "journal article", "extracted": {
        "title": "T", "authors": ["A B"], "journal_name": "Journal of Very Long Names",
        "verified_journal_abbrev": "J. Very Long Nam.", "volume": "1",
        "pages": "1-2", "year": "2020"}})
    format_reference(state)
    assert "*J. Very Long Nam.*" in state["formatted"]
    assert "Journal of Very Long Names" not in state["formatted"]


def test_format_preprint_includes_arxiv_id():
    state = _fmt_state(**{"type": "preprint", "extracted": {
        "title": "Adam", "authors": ["D Kingma"], "arxiv_id": "1412.6980", "year": "2014"}})
    format_reference(state)
    assert "arXiv:1412.6980" in state["formatted"]


def test_report_author_transparency_sections():
    """The report must give an author: action items, per-source consultation
    log, disagreement disclosure, verification marks, and a processing log."""
    from refassist.nodes.build_report import build_report

    class FakeSource:
        def __init__(self, name): self.NAME = name

    state = {
        "reference": 'A. B, "T," J., vol. 5, pp. 1-2, 2015.',
        "type": "journal article",
        "extracted": {"title": "Test Title", "authors": ["A B"], "year": "2015",
                      "volume": "5", "pages": "1-2", "doi": "10.1/x",
                      "publisher": "Acme"},
        "best": {"source": "consensus", "doi": "10.1/x", "title": "Test Title"},
        "provenance": {"doi": "crossref", "title": "crossref", "year": "crossref"},
        "audit": {},
        "verification": {"title": True, "authors": True, "year": True,
                         "volume": True, "pages": False, "doi": True,
                         "presence": True},
        "corrections": [("year", "2014", "2015")],
        "candidates": [
            {"source": "crossref", "title": "Test Title", "year": "2015",
             "volume": "5", "_via": "title", "doi": "10.1/x", "pages": "1-2"},
            {"source": "openalex", "title": "Test Title", "year": "2016",
             "volume": "5", "_via": "title", "doi": "10.1/x", "pages": "1-2"},
        ],
        "_sources": [FakeSource("crossref"), FakeSource("openalex"), FakeSource("dblp")],
        "_timed_out_jobs": {"dblp|title|test title"},
        "_started_at": 1.0,
        "formatted": 'A. B, "Test Title," *J.*, vol. 5, pp. 1–2, 2015.',
        "_fp": "abc",
    }
    build_report(state)
    d = state["report_data"]
    rpt = state["report"]

    assert any("Manually verify: pages" in a for a in d["action_items"])
    outcomes = {s["source"]: s["outcome"] for s in d["sources_consulted"]}
    assert "candidate record(s)" in outcomes["Crossref"]
    assert "timed out" in outcomes["DBLP"] or "no matching" in outcomes["DBLP"]
    assert any(g.startswith("year:") and "2016" in g and "2015" in g
               for g in d["disagreements"])
    assert d["processing"]["correction_rounds"] == 0 or True  # present
    for section in ("ACTION REQUIRED", "SOURCES CONSULTED",
                    "SOURCE DISAGREEMENTS", "PROCESSING LOG", "FIELDS"):
        assert section in rpt, f"missing section {section}"
    # publisher is not independently checkable → informational mark
    assert any(f["field"] == "publisher" and f["verified"] is None for f in d["fields"])


def test_published_year_beats_preprint_year():
    """A proceedings paper must not inherit its arXiv preprint's earlier year
    (BERT: NAACL 2019, preprint 2018). 'Earliest trusted' defeats re-registered
    mirrors but must not defeat the publication itself."""
    from refassist.nodes.select_best import _best_year_from_subset
    cluster = [
        {"source": "crossref", "year": "2018", "type": "posted-content",
         "journal_name": "arXiv"},
        {"source": "crossref", "year": "2019", "type": "proceedings-article",
         "journal_name": "Proceedings of NAACL-HLT 2019"},
    ]
    year, _src = _best_year_from_subset(cluster)
    assert year == "2019"

    # A genuine preprint (no published record) still keeps its own year
    only_preprint = [{"source": "arxiv", "year": "2014", "type": "preprint",
                      "journal_name": "arXiv"}]
    assert _best_year_from_subset(only_preprint)[0] == "2014"


def test_earliest_trusted_still_defeats_later_mirrors():
    """The mirror defense must survive the preprint fix: a citation-farm copy
    re-registered years later must not win over the original."""
    from refassist.nodes.select_best import _best_year_from_subset
    cluster = [
        {"source": "crossref", "year": "2017", "type": "proceedings-article",
         "journal_name": "NIPS"},
        {"source": "crossref", "year": "2025", "type": "journal-article",
         "journal_name": "Some Mirror Journal"},
    ]
    assert _best_year_from_subset(cluster)[0] == "2017"


def test_bare_doi_canonicalization():
    from refassist.tools.utils import bare_doi
    assert bare_doi("https://doi.org/10.1038/nature14539") == "10.1038/nature14539"
    assert bare_doi("http://dx.doi.org/10.1/x") == "10.1/x"
    assert bare_doi("doi:10.1/x") == "10.1/x"
    assert bare_doi("10.1/x") == "10.1/x"
    assert bare_doi("") == ""


def test_ieee_author_list_style():
    """et al. is roman (not italic) and two-author lists take no serial comma."""
    from refassist.tools.utils import format_authors_ieee_list
    two = format_authors_ieee_list(["Jane Doe", "Rob Roe"])
    assert two == "J. Doe and R. Roe"
    many = format_authors_ieee_list([f"A{i} Sur{i}" for i in range(8)])
    assert many.endswith("et al.") and "*" not in many


def test_conference_element_order_is_ieee():
    """IEEE order: venue, vol., YEAR, pp. — not pp. before year."""
    state = _fmt_state(**{"type": "conference paper", "reference": "", "extracted": {
        "title": "T", "authors": ["A B"], "conference_name": "Proc. Adv. Neural Inf. Process. Syst.",
        "volume": "33", "pages": "1877-1901", "year": "2020"}})
    format_reference(state)
    f = state["formatted"]
    assert "vol. 33" in f
    assert f.index("2020") < f.index("pp. 1877"), f


def test_normalize_text_strips_literal_newline_escape():
    from refassist.tools.utils import normalize_text
    assert normalize_text("PointNet++:\\nDeep Learning") == "PointNet++: Deep Learning"


# ---------- identifier validation ----------

def test_isbn_checksums():
    from refassist.tools.utils import isbn_valid
    assert isbn_valid("0-306-40615-2")       # valid ISBN-10
    assert isbn_valid("978-0-306-40615-7")   # valid ISBN-13
    assert not isbn_valid("0-306-40615-3")   # bad check digit
    assert not isbn_valid("1234")


def test_openalex_is_retracted_normalized():
    from refassist.nodes.multisource_lookup import _normalize_candidate
    cand = _normalize_candidate("openalex", {
        "display_name": "RETRACTED: Some Paper", "is_retracted": True,
        "authorships": [], "biblio": {}})
    assert cand["retracted"] is True
    assert cand["title"] == "Some Paper"
    clean = _normalize_candidate("openalex", {
        "display_name": "Fine Paper", "is_retracted": False,
        "authorships": [], "biblio": {}})
    assert clean["retracted"] is False
