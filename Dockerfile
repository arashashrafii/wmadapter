FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEBBRIDGE_CONFIG=/app/config.yaml

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && playwright install --with-deps chromium
COPY config.example.yaml ./config.yaml
EXPOSE 8000
CMD ["python", "-m", "webbridgefreeride"]
