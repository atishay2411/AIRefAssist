"""Unit tests for the Retraction Watch dataset integration. No network."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from refassist.tools import retractionwatch as rw

_FIXTURE = """Record ID,Title,Subject,Institution,Journal,Publisher,Country,Author,URLS,ArticleType,RetractionDate,RetractionDOI,RetractionPubMedID,OriginalPaperDate,OriginalPaperDOI,OriginalPaperPubMedID,RetractionNature,Reason,Paywalled,Notes,
1,Bad Paper,Med,Uni,The Lancet,Elsevier,UK,A Author,,Research Article;,2/2/2010 0:00,10.1016/notice.1,111,2/28/1998 0:00,10.1016/S0140-6736(97)11096-0,222,Retraction,+Falsification/Fabrication of Data;+Investigation by Journal/Publisher;,No,,
2,Concerning Paper,Bio,Uni,J Biol,Wiley,US,B Author,,Research Article;,3/3/2020 0:00,10.1002/notice.2,333,1/1/2018 0:00,10.1002/concern.1,444,Expression of concern,+Concerns/Issues About Data;,No,,
3,Concerning Paper,Bio,Uni,J Biol,Wiley,US,B Author,,Research Article;,4/4/2021 0:00,10.1002/notice.3,555,1/1/2018 0:00,10.1002/concern.1,444,Retraction,+Misconduct by Author;,No,,
4,No DOI row,Bio,Uni,J,W,US,C,,Research;,1/1/2020 0:00,,,1/1/2019 0:00,unavailable,,Retraction,+Other;,No,,
"""


def _load_fixture(tmp_path) -> rw.RetractionWatchDB:
    csv_path = tmp_path / "rw.csv"
    csv_path.write_text(_FIXTURE)
    db = rw.RetractionWatchDB()
    db._index = rw._parse_csv(csv_path)
    return db


def test_lookup_retraction_with_reasons(tmp_path):
    db = _load_fixture(tmp_path)
    hit = db.lookup("10.1016/S0140-6736(97)11096-0")  # case-insensitive
    assert hit and hit["nature"] == "Retraction"
    assert hit["reasons"] == ["Falsification/Fabrication of Data",
                              "Investigation by Journal/Publisher"]
    assert hit["date"] == "2/2/2010"
    assert hit["notice_doi"] == "10.1016/notice.1"


def test_most_severe_notice_wins(tmp_path):
    # Same DOI has an expression of concern AND a later retraction — the
    # retraction must win regardless of row order.
    db = _load_fixture(tmp_path)
    hit = db.lookup("10.1002/concern.1")
    assert hit["nature"] == "Retraction"


def test_clean_doi_and_invalid_rows(tmp_path):
    db = _load_fixture(tmp_path)
    assert db.lookup("10.9999/perfectly-fine") is None
    assert db.lookup("unavailable") is None  # non-DOI rows are not indexed
    assert db.lookup("") is None


def test_unloaded_db_returns_none_not_false_negative():
    db = rw.RetractionWatchDB()
    # No dataset: must return None (no signal) without raising, even with no
    # running event loop to schedule the background load.
    assert db.lookup("10.1016/S0140-6736(97)11096-0") is None
    assert db.ready is False


def test_failed_download_backs_off(tmp_path, monkeypatch):
    monkeypatch.setattr(rw, "RW_DATASET_URL", "https://127.0.0.1:1/nope.csv")
    monkeypatch.setattr(rw, "RW_CACHE_PATH", tmp_path / "missing.csv")
    db = rw.RetractionWatchDB()

    async def run():
        ok = await db.ensure_loaded()
        assert ok is False
        assert db._failed_at > 0
        # Within backoff: no new task is created
        task_before = db._load_task
        db.start_background_load()
        assert db._load_task is task_before

    asyncio.run(run())
