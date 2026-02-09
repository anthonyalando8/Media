FROM python:3.11-slim

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies for FFmpeg, yt-dlp, and video processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /code

# Install Python dependencies
COPY requirements.txt /code/
RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# Ensure yt-dlp is at the latest version (critical for YouTube support)
RUN pip install --no-cache-dir --upgrade --force-reinstall yt-dlp

# Copy project files
COPY . /code/

# Create media directory with proper permissions
RUN mkdir -p /code/media && chmod 755 /code/media

# Create a non-root user for security (optional but recommended)
# RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /code
# USER appuser

EXPOSE 8000

# Health check (optional but recommended)
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8000/api/health/ || exit 1

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "MediaConverter.asgi:application"]