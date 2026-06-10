FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md config.yaml ./
COPY benchmarks ./benchmarks
COPY src ./src
RUN pip install --no-cache-dir .
RUN mkdir -p /app/benchmarks/results /app/benchmarks/sessions /app/data

EXPOSE 8080
CMD ["uvicorn", "inference_arbiter.gateway.app:app", "--host", "0.0.0.0", "--port", "8080"]
