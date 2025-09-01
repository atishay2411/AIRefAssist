from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncio
from refassist.graphs import run_one
from refassist.config import PipelineConfig

app = FastAPI(title="RefAssist API", version="0.1.0")

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
