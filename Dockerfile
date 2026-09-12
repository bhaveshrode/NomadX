# ==========================================
# Stage 1: Build dependencies in virtualenv
# ==========================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies for compilation (if any)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml to install requirements
COPY pyproject.toml .

# Create virtual environment and install dependencies
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# ==========================================
# Stage 2: Final lightweight runner image
# ==========================================
FROM python:3.11-slim AS runner

WORKDIR /app

# Install runtime dependencies (e.g. PostgreSQL client libraries)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Configure PATH to use the virtualenv dependencies
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create a non-privileged app user and group for runtime security
RUN groupadd -g 10001 nomadx && \
    useradd -u 10001 -g nomadx -d /app -s /sbin/nologin -c "NomadX App User" nomadx

# Copy application code and migrations
COPY src/ /app/nomadx/src
COPY apps/ /app/nomadx/apps
COPY migrations/ /app/migrations
COPY alembic.ini /app/alembic.ini
COPY scripts/ /app/scripts/

# Ensure scripts are executable and set correct ownership
RUN chmod +x /app/scripts/*.sh && \
    chown -R nomadx:nomadx /app

# Switch to the non-root user
USER nomadx

# Expose FastAPI default port
EXPOSE 8000

# Set default entrypoint script
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["api"]
