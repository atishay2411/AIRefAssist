from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from refassist.graphs import run_one
from refassist.config import PipelineConfig
from docx import Document as DocxDocument
from typing import Optional, List, Tuple
import re
import asyncio
import io
import zipfile
import logging

# Optional PDF support (install: pip install pdfminer.six)
try:
    from pdfminer.high_level import extract_text as pdf_extract_text
    PDF_AVAILABLE = True
except Exception:
    PDF_AVAILABLE = False

# ---------- Setup ----------
app = FastAPI(title="RefAssist API", version="0.7.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/new_ui", StaticFiles(directory="new_UI"), name="new_ui")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("refassist")

# ---------- Text extraction from uploads ----------
async def _read_all(files: List[UploadFile]) -> str:
    """
    Server-side extraction:
      - .txt/.bbl/.tex : UTF-8 text
      - .docx          : python-docx paragraphs
      - .pdf           : pdfminer.six (if available)
      - .doc           : not supported (suggest converting to .docx)
    Returns the concatenated text from all files.
    """
    chunks: List[str] = []

    for up in files:
        name = (up.filename or "").lower()
        ext = "." + name.split(".")[-1] if "." in name else ""
        raw = await up.read()

        if ext in (".txt", ".bbl", ".tex"):
            try:
                chunks.append(raw.decode("utf-8", "ignore"))
            except Exception:
                chunks.append(raw.decode("latin-1", "ignore"))

        elif ext == ".docx":
            try:
                from io import BytesIO
                doc = DocxDocument(BytesIO(raw))
                paras = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
                chunks.append("\n".join(paras))
            except Exception as e:
                logger.exception("Failed to read DOCX: %s", name)
                raise HTTPException(status_code=400, detail=f"Failed to read DOCX {name}: {e}")

        elif ext == ".pdf":
            if not PDF_AVAILABLE:
                raise HTTPException(
                    status_code=400,
                    detail="PDF support is not available on the server. Install pdfminer.six."
                )
            try:
                from io import BytesIO
                text = pdf_extract_text(BytesIO(raw)) or ""
                chunks.append(text)
            except Exception as e:
                logger.exception("Failed to read PDF: %s", name)
                raise HTTPException(status_code=400, detail=f"Failed to read PDF {name}: {e}")

        elif ext == ".doc":
            # Old .doc is binary and not reliably parsed in pure Python.
            # Ask users to convert to .docx (Word/Google Docs) before uploading.
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' is .doc (legacy). Please convert to .docx and re-upload."
            )

        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type for '{name}'. Allowed: .pdf, .docx, .tex, .bbl, .txt"
            )

    # Join with blank lines to help the splitter
    return "\n\n".join(c.strip() for c in chunks if c and c.strip())


# ---------- Smart reference splitting ----------
_MARKER_PATTERNS = [
    re.compile(r"^\s*\[\d+\]"),  # [1]
    re.compile(r"^\s*\d+\."),    # 1.
    re.compile(r"^\s*[-•]"),     # - or •
]

# Signals that strongly suggest a boundary between references
_DOI_RE = re.compile(r"\b10\.\d{4,9}/\S+\b", re.I)
_URL_RE = re.compile(r"https?://\S+", re.I)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_AUTHOR_LIKE_RE = re.compile(r"^([A-Z][a-z]+|[A-Z]\.)[^\n]{0,60}\b([A-Z][a-z]+|[A-Z]\.)")  # crude author cue

def _has_any_marker(lines: List[str]) -> bool:
    for line in lines:
        if any(p.match(line) for p in _MARKER_PATTERNS):
            return True
    return False

def _strip_full_marker(line: str) -> str:
    return re.sub(r"^\s*(?:\[\d+\]|\d+\.|[-•])\s*", "", line)

def split_references(text: str) -> List[str]:
    """
    Smarter splitter:
      1) If markers like [1], '1.', '-' exist — use them (existing behavior).
      2) Else: split on blank lines (paragraphs).
      3) Else: heuristic segmentation using cues (title quotes/DOI/url/years/authors).
    """
    text = (text or "").strip()
    if not text:
        return []

    # Normalize
    text = re.sub(r"\r\n?", "\n", text)
    # Condense multiple blank lines but remember boundaries
    text = re.sub(r"\n{3,}", "\n\n", text)

    lines = [ln.strip() for ln in text.split("\n")]

    # Path 1: marker-based
    if _has_any_marker(lines):
        refs: List[str] = []
        cur: List[str] = []
        for line in lines:
            if not line:
                continue
            if any(p.match(line) for p in _MARKER_PATTERNS):
                if cur:
                    refs.append(" ".join(cur).strip())
                cur = [_strip_full_marker(line)]
            else:
                cur.append(line)
        if cur:
            refs.append(" ".join(cur).strip())
        return [r for r in refs if r]

    # Path 2: paragraph-based (blank lines)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paragraphs) > 1:
        return paragraphs

    # Path 3: heuristic segmentation inside one big block
    blob = paragraphs[0] if paragraphs else text
    raw_lines = [ln.strip() for ln in blob.split("\n") if ln.strip()]
    if len(raw_lines) == 1:
        # Single very long line: try to split on ".” " or period followed by uppercase author/title cue
        s = raw_lines[0]
        # First, try quotes around titles
        parts = re.split(r"\"\s*(?=[A-Z0-9])", s)  # split AFTER closing quotes sometimes stuck together; safe attempt
        if len(parts) > 1:
            # Re-glue with heuristics
            # fallback: split on ". " followed by uppercase start
            pass

    # Build references by scanning for "strong start" lines
    refs: List[str] = []
    cur: List[str] = []

    def push_cur():
        if cur:
            refs.append(" ".join(cur).strip())

    def looks_like_new_ref(line: str) -> bool:
        # Starts like an author/title line, or contains strong identifiers (DOI/URL) in the previous chunk.
        # Use a combination: uppercase word, commas, et al., year cues etc.
        if _AUTHOR_LIKE_RE.match(line):
            return True
        # Short lines with trailing period often begin titles in pasted refs
        if len(line) < 140 and line.endswith("."):
            return True
        # Container/venue cue
        if "Proc." in line or "Proceedings" in line or "IEEE" in line:
            return True
        return False

    for i, line in enumerate(raw_lines):
        if not cur:
            cur.append(line)
            continue
        prev = " ".join(cur[-2:])  # last ~2 lines combined
        # boundary if current looks like a new ref AND previous had a DOI/URL/year
        prev_has_key = bool(_DOI_RE.search(prev) or _URL_RE.search(prev) or _YEAR_RE.search(prev))
        if looks_like_new_ref(line) and prev_has_key:
            push_cur()
            cur = [line]
        else:
            cur.append(line)

    push_cur()
    # Post-fix: if heuristics produced one giant ref, just return it
    return [r for r in refs if r] or [blob.strip()]


# ---------- Formatting batch processing ----------
async def process_references(refs: List[str]) -> Tuple[List[str], DocxDocument]:
    formatted_refs: List[str] = []
    report_doc = DocxDocument()
    report_doc.add_heading("Reference Processing Report", 0)

    async def process_single(idx: int, ref: str) -> Tuple[str, dict]:
        try:
            out = await run_one(ref.strip(), PipelineConfig())
            formatted = out.get("formatted", ref)
            report_entry = {
                "idx": idx,
                "original": ref,
                "formatted": formatted,
                "report": out.get("report", "No changes"),
            }
            return formatted, report_entry
        except Exception as e:
            logger.exception("Error processing reference %s", idx)
            return ref + " [ERROR]", {
                "idx": idx, "original": ref, "formatted": None, "report": f"Error: {str(e)}"
            }

    tasks = [process_single(i + 1, ref) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    for formatted, entry in results:
        formatted_refs.append(formatted)
        report_doc.add_heading(f"Reference {entry['idx']}", level=2)
        report_doc.add_paragraph(f"Original: {entry['original']}")
        if entry['formatted']:
            report_doc.add_paragraph(f"Processed: {entry['formatted']}")
        report_doc.add_paragraph(entry['report'])

    return formatted_refs, report_doc


# ---------- Routes ----------
@app.get("/")
async def get_new_home(request: Request):
    return FileResponse("new_UI/index.html")


# Single-reference resolver
class ResolveRequest(BaseModel):
    reference: str

@app.post("/v1/resolve")
async def resolve(req: ResolveRequest):
    if not req.reference.strip():
        raise HTTPException(status_code=400, detail="reference is required")
    try:
        out = await run_one(req.reference, PipelineConfig())
        return {
            "type": out.get("type"),
            "formatted": out.get("formatted"),
            "report": out.get("report"),
            "verification": out.get("verification"),
            "csl_json": out.get("csl_json"),
            "bibtex": out.get("bibtex"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# NEW: Server-side text extraction for uploaded files (multiple)
@app.post("/api/extract")
async def extract_files_endpoint(files: List[UploadFile] = File(...)):
    """
    Accepts multiple files and returns concatenated plain text suitable for splitting/processing.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    text = await _read_all(files)
    if not text.strip():
        raise HTTPException(status_code=400, detail="No text could be extracted")
    return {"text": text}


# Process pasted text OR (optionally) files: return JSON summary
@app.post("/api/process")
async def process_references_api(
    references: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None)
):
    refs_text: str = (references or "").strip()

    # If files were sent directly to this endpoint, extract them here too
    if (not refs_text) and files:
        refs_text = (await _read_all(files)).strip()

    if not refs_text:
        raise HTTPException(status_code=400, detail="No references provided")

    refs = split_references(refs_text)
    if not refs:
        raise HTTPException(status_code=400, detail="No references detected in input")

    # Process
    formatted_refs: List[str] = []
    detailed: List[dict] = []

    async def process_single_detailed(idx: int, ref: str) -> Tuple[str, dict]:
        try:
            out = await run_one(ref.strip(), PipelineConfig())
            formatted = out.get("formatted", ref)
            return formatted, {
                "idx": idx, "original": ref, "formatted": formatted,
                "report": out.get("report", "No changes"), "status": "success"
            }
        except Exception as e:
            err_fmt = ref + " [ERROR]"
            return err_fmt, {
                "idx": idx, "original": ref, "formatted": err_fmt,
                "report": f"Error: {str(e)}", "status": "error"
            }

    tasks = [process_single_detailed(i + 1, ref) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    for formatted, entry in results:
        formatted_refs.append(formatted)
        detailed.append(entry)

    formatted_output = "\n".join(f"[{i+1}] {ref}" for i, ref in enumerate(formatted_refs))

    # Build preview text
    total_refs = len(detailed)
    success_count = sum(1 for r in detailed if r["status"] == "success")
    error_count = total_refs - success_count

    preview_lines = [
        "Reference Processing Report", "=" * 30, "",
        f"Total references processed: {total_refs}",
        f"Successfully processed: {success_count}",
        f"Errors encountered: {error_count}", ""
    ]
    for entry in detailed[:10]:
        preview_lines.append(f"Reference {entry['idx']}:")
        preview_lines.append(f"Original: {entry['original']}")
        if entry['status'] == 'success':
            preview_lines.append(f"Formatted: {entry['formatted']}")
        preview_lines.append(f"Notes: {entry['report']}")
        preview_lines.append("")
    preview_text = "\n".join(preview_lines)

    return {
        "success": True,
        "total_references": total_refs,
        "formatted_output": formatted_output,
        "preview": preview_text,
        "summary": {"total": total_refs, "success": success_count, "errors": error_count},
    }


# Full report ZIP
@app.post("/api/download-report")
async def download_full_report(
    references: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None)
):
    refs_text: str = (references or "").strip()
    if (not refs_text) and files:
        refs_text = (await _read_all(files)).strip()

    if not refs_text:
        raise HTTPException(status_code=400, detail="No references provided")

    refs = split_references(refs_text)
    if not refs:
        raise HTTPException(status_code=400, detail="No references detected in input")

    formatted_refs, report_doc = await process_references(refs)

    # Build ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        txt_content = "\n".join(f"[{i+1}] {ref}" for i, ref in enumerate(formatted_refs))
        zipf.writestr("formatted_references.txt", txt_content)

        docx_buffer = io.BytesIO()
        report_doc.save(docx_buffer)
        docx_buffer.seek(0)
        zipf.writestr("report.docx", docx_buffer.read())

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="refassist_report.zip"'}
    )


# Legacy batch (kept)
@app.post("/v1/upload")
async def upload_references_legacy(
    references: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    refs_text: Optional[str] = (references or "").strip()
    if (not refs_text) and file:
        files = [file]
        refs_text = (await _read_all(files)).strip()

    if not refs_text:
        raise HTTPException(status_code=400, detail="No references provided")

    refs = split_references(refs_text)
    if not refs:
        raise HTTPException(status_code=400, detail="No references detected in input")

    formatted_refs, report_doc = await process_references(refs)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        txt_content = "\n".join(f"[{i+1}] {ref}" for i, ref in enumerate(formatted_refs))
        zipf.writestr("formatted_references.txt", txt_content)

        docx_buffer = io.BytesIO()
        report_doc.save(docx_buffer)
        docx_buffer.seek(0)
        zipf.writestr("report.docx", docx_buffer.read())

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="results.zip"'}
    )
