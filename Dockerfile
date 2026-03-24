# https://github.com/Beejeex/vector
FROM python:3.11-slim

WORKDIR /app

# Install dependencies before copying source so this layer is cached
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/

# Run as non-root (matches securityContext in deploy/deployment.yaml)
RUN useradd -u 1000 -m vector
USER vector

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.main"]
