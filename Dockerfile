FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# TikTok's server-safe fallback uses headless Chromium. Keep browser payloads
# outside root's home so the unprivileged runtime account can execute them.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/.cache \
    && chown -R app:app /app
ENV XDG_CACHE_HOME=/app/.cache
USER app
RUN python -m camoufox fetch

COPY --chown=app:app . .

EXPOSE $PORT

CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
