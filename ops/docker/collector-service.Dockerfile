FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
    PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
    PYTHONPATH=/app:/worker

WORKDIR /app

COPY apps/collector_service/requirements-docker.txt /tmp/requirements.txt
COPY apps/worker/requirements-docker.txt /tmp/worker-requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt -r /tmp/worker-requirements.txt

COPY apps/collector_service /app
COPY apps/worker /worker

CMD ["uvicorn", "collector_service.main:app", "--host", "0.0.0.0", "--port", "8100"]
