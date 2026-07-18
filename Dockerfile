# RefAssist API + UI
FROM python:3.11-slim

WORKDIR /app

# Package first (requirements.txt installs it editable from ./refassist)
COPY refassist/pyproject.toml refassist/README.md refassist/
COPY refassist/src refassist/src
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api api
COPY web web

EXPOSE 8000

# Single worker by design: the job store, result cache, and rate limiter are
# in-process. Scale by raising REFASSIST_MAX_PARALLEL_REFS or by moving the
# job store to a shared backend before adding workers.
CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
