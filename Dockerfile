FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# pinned runtime dependencies first, so this layer is cached across code changes
COPY requirements-serve.txt .
RUN pip install -r requirements-serve.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

# trained per-site bundles (produced by scripts/train_models.py) and the site list
COPY models ./models
COPY config ./config

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

ENV DOSENTINEL_MODELS=/app/models DOSENTINEL_CONFIG=/app/config/sites.yaml
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4)"

# hosts such as Render and Cloud Run provide $PORT
CMD ["sh", "-c", "uvicorn dosentinel.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
