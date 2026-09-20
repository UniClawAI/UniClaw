FROM python:3.14-slim

# Prevent Python from writing .pyc files and enable unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependency metadata first for better layer caching
COPY pyproject.toml uv.lock .python-version ./

# Install production dependencies (skip dev group)
# Build dependencies are needed temporarily for native packages (evdev, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential linux-libc-dev && \
    uv sync --no-dev --no-install-project && \
    apt-get purge -y build-essential && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Copy source code
COPY src/ src/
COPY README.md LICENSE ./

# Install the project itself
RUN uv sync --no-dev

# Install playwright browsers (needed for web_browse tools)
RUN uv run playwright install --with-deps chromium

# Expose WebUI port
EXPOSE 8082

# Default: start in WebUI mode
CMD ["uv", "run", "uniclaw", "--mode", "webui", "--host", "0.0.0.0", "--port", "8082"]
