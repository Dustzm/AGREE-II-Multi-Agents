FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    DATA_ROOT=/app/output \
    DIR_INPUT=/app/input \
    DIR_OUTPUT=/app/output

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./

RUN sed '/^# 开发依赖/,$d' requirements.txt > /tmp/requirements-runtime.txt \
    && pip install --upgrade --no-cache-dir --no-compile pip setuptools wheel \
    && pip install --no-cache-dir --no-compile -r /tmp/requirements-runtime.txt \
    && rm -f /tmp/requirements-runtime.txt \
    && find /usr/local/lib/python3.11/site-packages -depth \
        \( -type d \( -name tests -o -name test \) -o -type d -name __pycache__ \) \
        -exec rm -rf '{}' +

COPY . .

RUN mkdir -p /app/input /app/output /app/logs /app/data

EXPOSE 8011

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8011/ama/health', timeout=3)" || exit 1

CMD ["uvicorn", "web.backend.app.main:app", "--host", "0.0.0.0", "--port", "8011"]
