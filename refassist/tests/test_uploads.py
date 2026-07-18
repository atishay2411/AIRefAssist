"""Unit tests for the hardened upload extraction (api/uploads.py). No network."""
import asyncio
import io
import os
import sys
from pathlib import Path

import pytest

# Make the api package (repo root) importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.uploads import (  # noqa: E402
    extract_files, read_limited, safe_name, tex_to_text, validate_magic,
    _extract_docx_sync,
)
from fastapi import HTTPException, UploadFile  # noqa: E402


def _upload(content: bytes, filename: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


# ---------- filename hygiene ----------

def test_safe_name_strips_paths_and_control_chars():
    assert safe_name("../../etc/passwd.txt") == "passwd.txt"
    assert safe_name("evil\x00\x1bname.pdf") == "evilname.pdf"
    assert safe_name(None) == "upload"
    assert len(safe_name("a" * 500 + ".txt")) <= 120


# ---------- magic-byte validation ----------

def test_magic_rejects_mismatched_content():
    with pytest.raises(HTTPException) as e:
        validate_magic(".pdf", b"just some text pretending", "fake.pdf")
    assert e.value.status_code == 400

    with pytest.raises(HTTPException):
        validate_magic(".docx", b"not a zip archive at all", "fake.docx")

    with pytest.raises(HTTPException):
        validate_magic(".txt", b"binary\x00garbage", "fake.txt")


def test_magic_accepts_valid_content():
    validate_magic(".pdf", b"%PDF-1.7 rest of file...", "ok.pdf")
    validate_magic(".txt", b"plain text reference list", "ok.txt")


# ---------- size limit (chunked) ----------

def test_read_limited_rejects_oversized_upload(monkeypatch):
    import api.uploads as uploads_mod
    monkeypatch.setattr(uploads_mod, "MAX_UPLOAD_BYTES", 2048)
    big = b"x" * 4096
    with pytest.raises(HTTPException) as e:
        asyncio.run(read_limited(_upload(big, "big.txt"), "big.txt"))
    assert e.value.status_code == 413


# ---------- LaTeX / .bbl cleanup ----------

def test_tex_to_text_splits_bibitems_and_strips_commands():
    bbl = r"""
\begin{thebibliography}{10}
\bibitem{lecun2015}
Y.~LeCun, Y.~Bengio, and G.~Hinton.
\newblock \emph{Deep learning}.
\newblock Nature, 521(7553):436--444, 2015.
\bibitem[Vaswani]{vaswani2017}
A.~Vaswani et~al.
\newblock Attention is all you need. % inline comment
\end{thebibliography}
"""
    out = tex_to_text(bbl)
    paragraphs = [p for p in out.split("\n\n") if p.strip()]
    assert len(paragraphs) == 2
    assert "Deep learning" in paragraphs[0]
    assert "\\emph" not in out and "\\newblock" not in out
    assert "~" not in out and "% inline comment" not in out
    assert "436–444" in out  # -- became an en dash


# ---------- DOCX extraction ----------

def test_docx_extraction_includes_tables():
    docx_mod = pytest.importorskip("docx")
    doc = docx_mod.Document()
    doc.add_paragraph("[1] A paragraph reference, 2020.")
    table = doc.add_table(rows=1, cols=1)
    table.rows[0].cells[0].text = "[2] A table reference, 2021."
    buf = io.BytesIO()
    doc.save(buf)
    text = _extract_docx_sync(buf.getvalue())
    assert "paragraph reference" in text
    assert "table reference" in text  # tables must not be silently dropped


# ---------- end-to-end extract_files ----------

def test_extract_files_happy_path_txt_and_bbl():
    txt = _upload(b"[1] J. Doe, Some Paper, 2020.", "refs.txt")
    bbl = _upload("\\bibitem{x} R. Roe. \\newblock Ünïcode paper. 2021.".encode("utf-8"), "refs.bbl")
    out = asyncio.run(extract_files([txt, bbl]))
    assert "Some Paper" in out and "Ünïcode paper. 2021." in out


def test_extract_files_rejects_empty_unsupported_and_legacy_doc():
    with pytest.raises(HTTPException):
        asyncio.run(extract_files([_upload(b"", "empty.txt")]))
    with pytest.raises(HTTPException):
        asyncio.run(extract_files([_upload(b"data", "image.png")]))
    with pytest.raises(HTTPException) as e:
        asyncio.run(extract_files([_upload(b"\xd0\xcf\x11\xe0", "old.doc")]))
    assert ".docx" in e.value.detail
