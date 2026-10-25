FROM python:3.13-slim-bookworm

ARG BUILD_VERSION=0.2.0
ARG BUILD_ARCH=amd64
LABEL io.hass.version="${BUILD_VERSION}" \
      io.hass.type="app" \
      io.hass.arch="${BUILD_ARCH}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    HOME=/data \
    XDG_CACHE_HOME=/data/cache \
    HF_HOME=/data/cache/huggingface

RUN pip install --no-cache-dir "fast-alpr[onnx]==0.4.0"

WORKDIR /app
COPY server.py /app/server.py
EXPOSE 8099
CMD ["python", "-u", "/app/server.py"]
