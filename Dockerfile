FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[serve]"
# bundles are produced by scripts/train_models.py and mounted or copied in at /app/models
COPY models ./models
COPY config ./config
ENV DOSENTINEL_MODELS=/app/models DOSENTINEL_CONFIG=/app/config/sites.yaml
EXPOSE 8000
CMD ["uvicorn", "dosentinel.api:app", "--host", "0.0.0.0", "--port", "8000"]
