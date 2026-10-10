FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install curl for healthchecks + fonts for PDF generation
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# crewai 1.15 pins pydantic <2.13; this file is the ONLY place that pin is real.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY fixtures/ ./fixtures/
COPY scripts/ ./scripts/
COPY n8n/ ./n8n/
COPY frontend/ ./frontend/

RUN mkdir -p /app/outputs /data

EXPOSE 8000

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]