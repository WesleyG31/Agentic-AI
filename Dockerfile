# Kompass API image.
#
# The app is local-first (Chroma + SQLite). Langfuse has a dedicated compose stack.
# Seed the corpus once inside the container before first use:
#   python -m kompass.scripts.seed
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt requirements-production.txt ./
RUN pip install --no-cache-dir -r requirements-production.txt

COPY kompass ./kompass
COPY corpus ./corpus
COPY evals ./evals
COPY ui ./ui
COPY infra ./infra

# Package the synthetic business fixture and embedded retrieval index so the local
# Compose app can exercise reads/actions without a separate data service.
RUN python -m kompass.scripts.seed

CMD ["uvicorn", "kompass.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
