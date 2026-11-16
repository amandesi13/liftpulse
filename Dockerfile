FROM python:3.12-slim

# No database URL here: DATABASE_URL (e.g. Neon) or LIFTPULSE_DATABASE_URL from
# the host wins; without either, the app uses SQLite in /app/data.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY sim ./sim

RUN useradd --system --uid 10001 liftpulse && mkdir /app/data && chown liftpulse /app/data
USER liftpulse
VOLUME /app/data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/healthz')"

# PORT is set by hosts like Render; 8000 otherwise. --proxy-headers: behind a TLS
# tunnel or load balancer, QR codes and links use the public https URL.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
