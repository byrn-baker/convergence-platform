FROM python:3.12-slim
WORKDIR /app
COPY services/ids-ingest/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/ids-ingest/server.py services/ids-ingest/store.py services/ids-ingest/telemetry.py services/ids-ingest/ip_context.py services/ids-ingest/host_events.py services/ids-ingest/hypervisor_events.py services/ids-ingest/vm_security.py ./
COPY deploy/convergence/ids/update-ip-context.py ./
USER 10001:10001
EXPOSE 8445
CMD ["python", "-m", "uvicorn", "server:configured_app", "--factory", "--host", "0.0.0.0", "--port", "8445", "--ssl-certfile", "/run/secrets/ids/server.crt", "--ssl-keyfile", "/run/secrets/ids/server.key", "--no-access-log"]
