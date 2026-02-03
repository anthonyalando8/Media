FROM python:3.11-slim

# Install system dependencies for FFmpeg and yt-dlp
RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Install Python dependencies
COPY requirements.txt /code/
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Ensure yt-dlp is at the latest version
RUN pip install --no-cache-dir --upgrade yt-dlp

# Copy project
COPY . /code/

# Create media directory
RUN mkdir -p /code/media

EXPOSE 8000

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "MediaConverter.asgi:application"]