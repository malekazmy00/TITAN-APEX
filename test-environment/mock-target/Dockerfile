FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# gunicorn, not the Flask dev server -- this runs for real in CI/docker-compose.
# A single worker is deliberate: the rate limiter and markup randomizer keep
# their state in-process (in memory), so more than one worker would give
# each request a different, inconsistent view of both -- not a real
# concurrency need for a test-only target.
# NOTE: gunicorn has no `--factory` CLI flag (confirmed against the
# pinned gunicorn==22.0.0 -- `gunicorn --help` lists no such option); the
# real way to point it at an application-factory callable is the
# trailing `()` on the module:callable target, which is what actually
# invokes create_app() to build the Flask app.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "app:create_app()"]
