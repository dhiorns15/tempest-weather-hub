FROM python:3.13-slim

WORKDIR /app

COPY main.py .
COPY weatherhub/ weatherhub/
COPY static/ static/
COPY scripts/ scripts/

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)" || exit 1

CMD ["python", "main.py"]
