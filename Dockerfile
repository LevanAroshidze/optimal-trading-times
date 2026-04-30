FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY bot ./bot
COPY config ./config

RUN pip install --upgrade pip \
 && pip install . \
 && mkdir -p /app/data

ENTRYPOINT ["python", "-m", "bot"]
