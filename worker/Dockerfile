FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies (same as backend for AI models)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    wget \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements from backend (workers use same dependencies)
COPY ../backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download YOLOv8 weights
RUN mkdir -p /models && \
    wget -O /models/yolov8n.pt https://github.com/ultralytics/assets/releases/download/v0.0.0/yolov8n.pt

# Copy backend code (workers need access to models and utilities)
COPY ../backend .

# Create data directories
RUN mkdir -p data/uploads data/thumbnails logs

# Set permissions
RUN chmod -R 755 /app

# Health check for worker process
HEALTHCHECK --interval=60s --timeout=30s --start-period=5s --retries=3 \
    CMD python -c "import redis; redis.Redis.from_url('redis://redis:6379/0').ping()" || exit 1

# Run the RQ worker
CMD ["python", "workers/processor.py"]