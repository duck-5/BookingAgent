# Stage 1: Builder
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies if needed
# (e.g., if any of the requirements need compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install dependencies to a local directory
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# Stage 2: Runner
FROM python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source code
COPY . .

# Expose the dashboard port
EXPOSE 8000

# Run the application
CMD ["python", "main.py"]
