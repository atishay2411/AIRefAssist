# RefAssist

Agentic IEEE reference pipeline. Takes freeform bibliographic references,
verifies them against authoritative metadata sources, corrects errors,
flags **retracted works**, and formats them to IEEE style — with a full
per-field provenance report.

## How it works

One LLM pass validates/classifies/extracts the reference, then parallel
lookups across **14 sources** — Crossref, IEEE Xplore (key), OpenAlex,
Semantic Scholar, PubMed, Europe PMC, arXiv, bioRxiv/medRxiv, DBLP (CS
venues), DataCite (dataset/software DOIs), Unpaywall (per-DOI + OA links),
DOAJ, Open Library and Google Books (key) for books — feed a gated consensus
that corrects fields and records where each value came from. The report's
SOURCES CONSULTED section logs every source's answer per reference. LLM-formatted output passes a faithfulness check — any
dropped or altered field falls back to the deterministic formatter. See the
graph diagram in `src/refassist/graphs/pipeline.py`.

Accuracy is measured, not asserted: `tests/benchmark_accuracy.py` perturbs
ground-truth references (wrong years/volumes/pages, typos) and scores
field-level recovery. Judge matching-gate changes by that number.

```
InitRuntime → AnalyzeReference → [VerifyJournalAbbrev] → MultiSourceLookup
    → SelectBest → VerifyAgents ⇄ (ApplyCorrections → EnrichFromBest)
    → LLMFormat | FormatReference → BuildExports → CheckRetraction
    → BuildReport → Cleanup
```

## Setup

```bash
cd refassist
python -m venv .venv && source .venv/bin/activate
pip install -e .            # core pipeline
pip install -r ../requirements.txt   # API extras (from the repo root dir)
cp .env.example .env        # then fill in your provider credentials
```

Pick an LLM provider via `IEEE_REF_LLM` (`auto` | `openai` | `azure` |
`anthropic` | `ollama`). `auto` selects the first provider with credentials
in the environment. All configuration is documented in `.env.example`.

## Run

```bash
# API + web UI (from the repo root directory, one level above this file)
uvicorn api.app:app --host 0.0.0.0 --port 8000
# then open http://localhost:8000/

# CLI
python cli/refassist_cli.py --ref 'A. Author, "Title," Journal, vol. 1, 2020.' --verbose
```

Key endpoints:

- `POST /v1/resolve` — single reference, JSON incl. `report_data` and `retracted`.
- `POST /api/jobs` — **bulk processing** (text or files). Returns a `job_id`
  immediately; processing runs in the background so no HTTP request has to
  outlive a proxy timeout.
- `GET /api/jobs/{id}` — status + progress (`done`/`total`) with progressive
  per-reference results; full results/summary once completed.
- `GET /api/jobs/{id}/report` — ZIP (formatted list + .docx report) built from
  the job's stored results — the pipeline is never re-run for a download.
- `DELETE /api/jobs/{id}` — cancel a running job.
- `POST /api/process` — synchronous batch, same JSON shape as a completed job.
  Batches over `REFASSIST_SYNC_MAX_REFS` (default 10) are auto-queued and
  answered with **202 + job info** so no request can outlive a proxy timeout;
  clients must handle 202 by polling `status_url`. Same guard applies to
  `POST /api/download-report` (synchronous ZIP) and legacy `/v1/upload`.
- `GET /healthz`.

## Tests

```bash
pytest tests/test_units.py                      # fast, no network
REFASSIST_RUN_LIVE=1 pytest tests/test_live_corrections.py -v   # live: network + LLM
```

The live suite feeds references with intentionally planted errors (wrong
year/volume/pages, misspelled title, retracted article) and asserts the
pipeline corrects and flags them.

## Operational notes

- Set `REFASSIST_CONTACT_EMAIL` — it joins the polite pools of Crossref,
  NCBI, and OpenAlex (anonymous access is heavily rate-limited).
- Server limits: `REFASSIST_MAX_PARALLEL_REFS`, `REFASSIST_MAX_REFERENCES`,
  `REFASSIST_MAX_UPLOAD_BYTES`, `REFASSIST_MAX_FILES`, `REFASSIST_MAX_TEXT_CHARS`,
  `REFASSIST_MAX_PDF_PAGES`, `REFASSIST_MAX_DOCX_UNCOMPRESSED`.
- Uploads (`api/uploads.py`) are validated by content, not extension: magic
  bytes, DOCX zip-bomb guard, PDF page cap, chunked size enforcement, and a
  total-text cap; parsers run off the event loop.
- Bulk jobs (`api/jobs.py`) are stored in-process (`REFASSIST_JOB_TTL`,
  `REFASSIST_MAX_ACTIVE_JOBS`, `REFASSIST_MAX_STORED_JOBS`). Run a single
  uvicorn worker, or swap the store for a shared backend (e.g. Redis) before
  scaling out — job state does not cross process boundaries.
- Retraction detection layers four signals: the **Retraction Watch database**
  (downloaded from Crossref's open distribution at startup, cached in
  `.rw_cache/`, refreshed every `REFASSIST_RW_REFRESH_DAYS`; supplies the
  retraction nature, date, and reasons), OpenAlex `is_retracted`, Crossref
  `updated-by`, and PubMed's publication types. Expressions of concern and
  corrections are surfaced as advisories. Works without a DOI but is
  strongest with one; disable the dataset with `REFASSIST_RW_PRELOAD=0`.
- Never commit `.env` — it is gitignored; use `.env.example` as the template.
