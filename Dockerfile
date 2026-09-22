FROM mcr.microsoft.com/playwright/python:v1.63.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /data \
    && chown -R agent:agent /app /data

ENV HEADLESS=1 \
    STATE_DB_PATH=/data/state.db \
    WORK_DIR=/data/work \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
VOLUME ["/data"]
USER agent

CMD ["python", "-m", "src.main"]
