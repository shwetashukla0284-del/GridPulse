# One image for the API and the simulator: they share Python code
# (the simulator's serializer and channel name) and requirements.
# Build from the repository root:  docker build -f backend/Dockerfile .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so code edits do not invalidate this layer.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend ./backend
COPY simulator ./simulator

# Run as an unprivileged user rather than root.
RUN useradd --create-home --uid 10001 gridpulse
USER gridpulse

EXPOSE 8000
# 0.0.0.0 so the server is reachable from outside the container.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
