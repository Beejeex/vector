# https://github.com/Beejeex/vector

# ── test stage ────────────────────────────────────────────────────────────────
# Run with: docker build --target test .
# Or via docker compose: docker compose run tests
FROM python:3.11-slim AS test

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt

COPY src/ ./src/
COPY tests/ ./tests/

CMD ["python", "-m", "pytest", "--tb=short", "-q"]

# ── runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

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
