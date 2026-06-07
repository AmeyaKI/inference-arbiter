FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md config.yaml ./
COPY src ./src
RUN pip install --no-cache-dir .

EXPOSE 8080
CMD ["uvicorn", "inference_arbiter.gateway.app:app", "--host", "0.0.0.0", "--port", "8080"]
