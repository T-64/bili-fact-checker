FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip wheel --no-deps --wheel-dir /wheels .


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    BFC_DATA_DIR=/data \
    BFC_CACHE_DIR=/data/cache \
    BFC_CONFIG_PATH=/data/config.json

RUN groupadd --gid 10001 bfc \
    && useradd --uid 10001 --gid bfc --create-home --shell /usr/sbin/nologin bfc \
    && install -d -o bfc -g bfc -m 0700 /data

COPY --from=builder /wheels /wheels
RUN python -m pip install /wheels/*.whl \
    && rm -rf /wheels

USER 10001:10001
WORKDIR /data
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3)"

CMD ["bili-fact-checker", "serve", "--host", "0.0.0.0", "--port", "8765"]
