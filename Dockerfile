# Backend only — the frontend (frontend/) is a separate static site,
# deployed separately (see README's "Hosting on GCP" section). This
# mirrors the existing two-service split already used on Render: one
# service for the API, one for the static frontend, talking to each
# other via API_BASE (frontend) and CORS_ALLOW_ORIGINS (backend).

FROM python:3.12-slim

WORKDIR /app

# Installed as its own layer, before copying app code, so `docker build`
# only re-installs dependencies when requirements.txt actually changes —
# not on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Includes app/data/*.db and *.json — schools.db and the listing datasets
# are meant to be pre-built and committed (see README's Schools section
# and docs/architecture.md), not generated inside the container. Nothing
# in the runtime path writes to them.
COPY app/ ./app/

# Cloud Run sets PORT itself (usually 8080) and requires the container to
# listen on whatever value that is — 8080 here is only the default for
# running this image somewhere that doesn't set PORT (e.g. local `docker run`).
ENV PORT=8080
EXPOSE 8080

# Same command as local dev (see app/main.py's own docstring: "Run with
# uvicorn app.main:app ... from the project root"), just bound to 0.0.0.0
# and Cloud Run's injected $PORT instead of the 127.0.0.1:8000 default.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
