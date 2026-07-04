# Kompass API image.
#
# Infra (Postgres, Qdrant, Langfuse) comes from `docker compose up -d`.
# Seed the corpus once inside the container before first use:
#   python -m kompass.scripts.seed
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY kompass ./kompass
COPY corpus ./corpus
COPY evals ./evals
COPY ui ./ui

CMD ["uvicorn", "kompass.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
