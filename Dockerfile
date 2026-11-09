FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY dados ./dados
RUN pip install --no-cache-dir ".[postgres]" && useradd -m app
USER app
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "conciliador.api:app", "--host", "0.0.0.0", "--port", "8000"]
