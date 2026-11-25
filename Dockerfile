FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data
RUN pip install --no-cache-dir . && useradd --create-home broker && mkdir /var/broker && chown broker /var/broker

USER broker
VOLUME ["/var/broker"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["broker-serve", "--data-dir", "/var/broker", "--host", "0.0.0.0", "--port", "8000"]
