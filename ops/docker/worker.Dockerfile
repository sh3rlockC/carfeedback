FROM python:3.11-slim

LABEL org.opencontainers.image.description="Vehicle koubei RQ and Temporal worker runtime"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple \
    PIP_TRUSTED_HOST=mirrors.cloud.tencent.com \
    PYTHONPATH=/app \
    HERMES_HOME=/root/.hermes

WORKDIR /app

RUN sed -i \
        -e 's#http://deb.debian.org/debian#https://mirrors.cloud.tencent.com/debian#g' \
        -e 's#http://security.debian.org/debian-security#https://mirrors.cloud.tencent.com/debian-security#g' \
        /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl fonts-noto-cjk git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://res1.hermesagent.org.cn/install.sh -o /tmp/hermes-install.sh \
    && sha256sum /tmp/hermes-install.sh \
    && grep -q -- "--skip-setup" /tmp/hermes-install.sh \
    && grep -q "prepare_repo" /tmp/hermes-install.sh \
    && bash /tmp/hermes-install.sh --skip-setup --dir /opt/hermes-agent \
    && ln -sf /opt/hermes-agent/venv/bin/hermes /usr/local/bin/hermes \
    && rm -rf /root/.hermes/skills/* \
    && rm -f /tmp/hermes-install.sh

COPY apps/worker/requirements-docker.txt /tmp/requirements.txt
RUN /opt/hermes-agent/venv/bin/python -m pip install --upgrade pip \
    && /opt/hermes-agent/venv/bin/python -m pip install -r /tmp/requirements.txt

ENV PATH=/opt/hermes-agent/venv/bin:/root/.local/bin:$PATH

COPY apps/worker /app
COPY ops/docker/start-worker.sh /start-worker.sh
RUN chmod +x /start-worker.sh

CMD ["/start-worker.sh"]
