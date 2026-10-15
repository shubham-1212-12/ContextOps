FROM python:3.12.8-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8000

WORKDIR /app

COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --requirement requirements.lock

COPY src ./src
COPY tests ./tests
COPY pyproject.toml ./pyproject.toml
COPY Makefile ./Makefile
COPY README.md ./README.md
COPY .env.example ./.env.example

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "contextops.main:app", "--host", "0.0.0.0", "--port", "8000"]
