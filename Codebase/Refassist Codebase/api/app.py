from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from refassist.graphs import run_one
from refassist.config import PipelineConfig
from docx import Document
from typing import Optional, List, Tuple
import re
import asyncio
import io
import zipfile
import logging

# ---------- Setup ----------
app = FastAPI(title="RefAssist API", version="0.6.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("refassist")

def split_references(text: str) -> List[str]:
    """
    Robust reference splitter:
    - Handles IEEE [1], numbered 1., bullets -, •
    - Keeps multi-line references together
    """
    text = text.strip()
    if not text:
        return []

    # Normalize line breaks
    text = re.sub(r"\r\n?", "\n", text)
    lines = text.split("\n")

    refs = []
    current_ref = []

    # Patterns for reference start
    patterns = [
        re.compile(r"^\s*\[\d+\]"),  # [1] style
        re.compile(r"^\s*\d+\."),    # 1. style
        re.compile(r"^\s*[-•]"),     # bullet style
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue  # skip empty lines

        # Check if this line starts a new reference
        if any(p.match(line) for p in patterns):
            if current_ref:
                refs.append(" ".join(current_ref).strip())
            current_ref = [re.sub(r"^[-•\[\]\d\.]\s*", "", line)]  # remove marker
        else:
            # continuation of previous reference
            current_ref.append(line)

    if current_ref:
        refs.append(" ".join(current_ref).strip())

    return refs


# ---------- Helper: process references concurrently ----------
async def process_references(refs: List[str]) -> Tuple[List[str], Document]:
    formatted_refs: List[str] = []
    report_doc = Document()
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
                "matching_fields": out.get("matching_fields", [])  # NEW: Include matching fields
            }
            return formatted, report_entry
        except Exception as e:
            logger.exception(f"Error processing reference {idx}")
            return ref + " [ERROR]", {
                "idx": idx,
                "original": ref,
                "formatted": None,
                "report": f"Error: {str(e)}",
                "matching_fields": []
            }

    tasks = [process_single(i + 1, ref) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    for formatted, entry in results:
        formatted_refs.append(formatted)
        report_doc.add_heading(f"Reference {entry['idx']}", level=2)
        report_doc.add_paragraph(f"Original: {entry['original']}")
        if entry['formatted']:
            report_doc.add_paragraph(f"Processed: {entry['formatted']}")
        report_doc.add_paragraph(f"Matching Fields: {', '.join(entry['matching_fields']) if entry['matching_fields'] else 'None'}")
        report_doc.add_paragraph(entry['report'])

    return formatted_refs, report_doc


# ---------- UI route ----------
@app.get("/", response_class=HTMLResponse)
async def get_home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ---------- API: handle copy-paste OR file ----------
@app.post("/v1/upload")
async def upload_references(
    references: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    refs_text: Optional[str] = None

    # Case 1: pasted references
    if references and references.strip():
        refs_text = references.strip()

    # Case 2: uploaded file
    elif file:
        content = await file.read()
        try:
            if file.filename.lower().endswith(".docx"):
                from io import BytesIO
                doc = Document(BytesIO(content))
                refs_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            else:
                refs_text = content.decode("utf-8", errors="ignore")
        except Exception as e:
            logger.exception("Failed to read uploaded file")
            raise HTTPException(status_code=400, detail=f"Invalid file format: {str(e)}")

    if not refs_text:
        raise HTTPException(status_code=400, detail="No references provided")

    # Split and process references
    refs = split_references(refs_text)
    if not refs:
        raise HTTPException(status_code=400, detail="No references detected in input")

    formatted_refs, report_doc = await process_references(refs)

    # Create in-memory ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Write TXT
        txt_content = "\n".join(f"[{i+1}] {ref}" for i, ref in enumerate(formatted_refs))
        zipf.writestr("formatted_references.txt", txt_content)

        # Write DOCX report
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