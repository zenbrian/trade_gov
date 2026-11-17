FROM python:3.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    UV_PYTHON_DOWNLOADS=0 \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY scripts/ ./scripts/
COPY data/input_gc453.csv ./data/input_gc453.csv
RUN python scripts/captcha_ocr.py --prepare

CMD ["sh", "/app/scripts/railway_start.sh"]
