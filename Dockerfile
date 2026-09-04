FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY runtime ./runtime
COPY scripts ./scripts
COPY web ./web
ENV RR_CONFIG=/app/config.yaml
CMD ["python", "-m", "runtime.main"]
