FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    DATA_ROOT=/app/output \
    DIR_INPUT=/app/input \
    DIR_OUTPUT=/app/output

WORKDIR /app

ARG INSTALL_PLAYWRIGHT=true

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt pyproject.toml ./

RUN pip install --upgrade pip setuptools wheel \
    && pip install -r requirements.txt

RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then python -m playwright install --with-deps chromium; fi

COPY . .

RUN mkdir -p /app/input /app/output /app/logs /app/data

EXPOSE 8011

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8011/ama/health', timeout=3)" || exit 1

CMD ["uvicorn", "web.backend.app.main:app", "--host", "0.0.0.0", "--port", "8011"]
