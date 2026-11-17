# Production image for the JobRec API (Render free tier: 512 MB RAM).
#
# Differences from the dev image (backend/Dockerfile):
#   * requirements.prod.txt — no torch / sentence-transformers / gensim; the
#     MiniLM embedder runs through onnxruntime (EMBEDDING_BACKEND=onnx):
#     identical vectors at ~1/10 the memory, and a much smaller image
#   * the ONNX model is downloaded at build time (no cold-start download)
#   * no --reload; runs as a non-root user; listens on $PORT
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 app
ENV HOME=/home/app \
    FASTEMBED_CACHE_PATH=/home/app/.cache/fastembed
WORKDIR /home/app/srv

COPY --chown=app requirements.prod.txt .
RUN pip install -r requirements.prod.txt

USER app
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')"

COPY --chown=app . .
RUN mkdir -p uploads && chmod +x start.sh

ENV ENVIRONMENT=production \
    EMBEDDING_BACKEND=onnx \
    PORT=10000
EXPOSE 10000

CMD ["./start.sh"]
