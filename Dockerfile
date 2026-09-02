FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEBBRIDGE_CONFIG=/app/config.yaml \
    WEBBRIDGE_XVFB=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY config.example.yaml ./config.yaml
EXPOSE 11555
CMD ["sh", "-c", "Xvfb :99 -screen 0 1440x1000x24 -ac & export DISPLAY=:99; exec python -m webbridgefreeride"]
