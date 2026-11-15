FROM python:3.13-slim

WORKDIR /app

COPY main.py .
COPY weatherhub/ weatherhub/
COPY static/ static/
COPY scripts/ scripts/

EXPOSE 8080

# /healthz answers without calling Tempest, so a Tempest cloud outage does not mark this container
# unhealthy. The slim image has no curl or wget, hence Python. urlopen raises on a non-2xx reply.
# The port comes from PORT so the check still works when the listen port is changed.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/healthz', timeout=3)"]

CMD ["python", "main.py"]
