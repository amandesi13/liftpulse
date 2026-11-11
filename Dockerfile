FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LIFTPULSE_DATABASE_URL=sqlite:////data/liftpulse.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY sim ./sim

RUN useradd --system --uid 10001 liftpulse && mkdir /data && chown liftpulse /data
USER liftpulse
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

# --proxy-headers: behind a TLS tunnel or ingress, QR codes and links use the public https URL.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
