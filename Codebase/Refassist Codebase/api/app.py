from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
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
app.mount("/new_ui", StaticFiles(directory="new_UI"), name="new_ui")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("refassist")


# ---------- Helpers ----------
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
    current_ref: List[str] = []

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

        # If this line starts a new reference, flush the previous one
        if any(p.match(line) for p in patterns):
            if current_ref:
                refs.append(" ".join(current_ref).strip())

            # Remove the ENTIRE marker (e.g., "[12] ", "3. ", "- ", "• ")
            cleaned = re.sub(r"^\s*(?:\[\d+\]|\d+\.|[-•])\s*", "", line)
            current_ref = [cleaned]
        else:
            # continuation of previous reference
            current_ref.append(line)

    if current_ref:
        refs.append(" ".join(current_ref).strip())

    return refs


async def process_references(refs: List[str]) -> Tuple[List[str], Document]:
    """Process a batch concurrently and build a DOCX report."""
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
                "report": out.get("report", "No changes")
            }
            return formatted, report_entry
        except Exception as e:
            logger.exception(f"Error processing reference {idx}")
            return ref + " [ERROR]", {
                "idx": idx,
                "original": ref,
                "formatted": None,
                "report": f"Error: {str(e)}"
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


# Single-reference resolver (useful for programmatic clients)
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


# New UI: process pasted text or uploaded file, return JSON
@app.post("/api/process")
async def process_references_api(
    references: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    refs_text: Optional[str] = None

    if references and references.strip():
        refs_text = references.strip()
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

    refs = split_references(refs_text)
    if not refs:
        raise HTTPException(status_code=400, detail="No references detected in input")

    # Process references and collect detailed results
    formatted_refs: List[str] = []
    detailed_reports: List[dict] = []

    async def process_single_detailed(idx: int, ref: str) -> Tuple[str, dict]:
        try:
            out = await run_one(ref.strip(), PipelineConfig())
            formatted = out.get("formatted", ref)
            report_entry = {
                "idx": idx,
                "original": ref,
                "formatted": formatted,
                "report": out.get("report", "No changes"),
                "status": "success"
            }
            return formatted, report_entry
        except Exception as e:
            logger.exception(f"Error processing reference {idx}")
            error_formatted = ref + " [ERROR]"
            report_entry = {
                "idx": idx,
                "original": ref,
                "formatted": error_formatted,
                "report": f"Error: {str(e)}",
                "status": "error"
            }
            return error_formatted, report_entry

    tasks = [process_single_detailed(i + 1, ref) for i, ref in enumerate(refs)]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    for formatted, entry in results:
        formatted_refs.append(formatted)
        detailed_reports.append(entry)

    # Generate outputs
    formatted_output = "\n".join(f"[{i+1}] {ref}" for i, ref in enumerate(formatted_refs))

    # Build a concise preview report
    report_lines = ["Reference Processing Report", "=" * 30, ""]
    total_refs = len(detailed_reports)
    success_count = sum(1 for r in detailed_reports if r["status"] == "success")
    error_count = total_refs - success_count

    report_lines.append(f"Total references processed: {total_refs}")
    report_lines.append(f"Successfully processed: {success_count}")
    report_lines.append(f"Errors encountered: {error_count}")
    report_lines.append("")

    for entry in detailed_reports[:10]:  # short preview
        report_lines.append(f"Reference {entry['idx']}:")
        report_lines.append(f"Original: {entry['original']}")
        if entry['status'] == 'success':
            report_lines.append(f"Formatted: {entry['formatted']}")
        report_lines.append(f"Notes: {entry['report']}")
        report_lines.append("")

    preview_text = "\n".join(report_lines)

    return {
        "success": True,
        "total_references": total_refs,
        "formatted_output": formatted_output,
        "preview": preview_text,
        "summary": {
            "total": total_refs,
            "success": success_count,
            "errors": error_count
        }
    }


# New UI: download full ZIP report
@app.post("/api/download-report")
async def download_full_report(
    references: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    refs_text: Optional[str] = None

    if references and references.strip():
        refs_text = references.strip()
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

    refs = split_references(refs_text)
    if not refs:
        raise HTTPException(status_code=400, detail="No references detected in input")

    formatted_refs, report_doc = await process_references(refs)

    # Create in-memory ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        # TXT
        txt_content = "\n".join(f"[{i+1}] {ref}" for i, ref in enumerate(formatted_refs))
        zipf.writestr("formatted_references.txt", txt_content)

        # DOCX
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


# Legacy batch upload endpoint (kept for compatibility)
@app.post("/v1/upload")
async def upload_references(
    references: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None)
):
    refs_text: Optional[str] = None

    if references and references.strip():
        refs_text = references.strip()
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

    refs = split_references(refs_text)
    if not refs:
        raise HTTPException(status_code=400, detail="No references detected in input")

    formatted_refs, report_doc = await process_references(refs)

    # Create in-memory ZIP
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        # TXT
        txt_content = "\n".join(f"[{i+1}] {ref}" for i, ref in enumerate(formatted_refs))
        zipf.writestr("formatted_references.txt", txt_content)

        # DOCX
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
