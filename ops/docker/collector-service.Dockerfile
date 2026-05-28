FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
    PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
    PYTHONPATH=/app

WORKDIR /app

COPY apps/collector_service/requirements-docker.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt

COPY apps/collector_service /app

CMD ["uvicorn", "collector_service.main:app", "--host", "0.0.0.0", "--port", "8100"]
